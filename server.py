"""BTC 15-Minute Kalshi Analyzer — standalone Flask website (no Streamlit).

Run:  python server.py        ->  http://127.0.0.1:5000
Logic ported faithfully from the Streamlit app (~/workspace/btc_kalshi_realtime/app.py).
"""
import json
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from flask import Flask, jsonify, request, redirect, render_template, send_from_directory, Response

import config
from models import db, User
from flow_signals import get_flow_signals
from ai_reader import get_ai_read, configured as ai_configured
from parametric import parametric_up_prob, get_sentiment
import call_record
from call_record import kalshi_taker_fee_cents

BASE_DIR = Path(__file__).parent
KALSHI = "https://external-api.kalshi.com/trade-api/v2"

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["SQLALCHEMY_DATABASE_URI"] = config.DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)
call_record.set_app(app)

from flask_login import LoginManager, current_user  # noqa: E402
from functools import wraps  # noqa: E402

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.init_app(app)


@login_manager.user_loader
def load_user(uid):
    return User.query.get(int(uid))


def subscriber_required(fn):
    """Page -> redirect; /api/* -> JSON error. DEV_MODE bypasses (local use)."""
    @wraps(fn)
    def wrapper(*a, **kw):
        if config.DEV_MODE:
            return fn(*a, **kw)
        is_api = request.path.startswith("/api/")
        if not current_user.is_authenticated:
            if is_api:
                return jsonify({"ok": False, "error": "login required"}), 401
            return redirect("/login?next=" + request.path)
        if not current_user.is_subscriber:
            if is_api:
                return jsonify({"ok": False, "error": "subscription required"}), 402
            return redirect("/billing")
        return fn(*a, **kw)
    return wrapper


from auth_routes import auth_bp  # noqa: E402
from billing_routes import billing_bp  # noqa: E402
from admin_routes import admin_bp  # noqa: E402
app.register_blueprint(auth_bp)
app.register_blueprint(billing_bp)
app.register_blueprint(admin_bp)


@app.context_processor
def inject_admin():
    try:
        admin = bool(current_user.is_authenticated
                     and current_user.email.lower() in config.ADMIN_EMAILS)
    except Exception:
        admin = False
    return {"is_admin": admin}

with app.app_context():
    db.create_all()

# v26.5: in production the record signing key must come from
# RECORD_SIGNING_KEY. Fail loud at boot — a deploy without it must never
# grade calls with an ephemeral key. In dev this loads (or generates)
# the local instance/ key.
call_record.require_signing_key()

# --------------------------------------------------------------------------
# Simple in-memory time-based caches (replaces st.cache_data)
# --------------------------------------------------------------------------
_cache: dict = {}


def cached(key, ttl_seconds, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl_seconds:
        return hit[1]
    value = fn()
    _cache[key] = (now, value)
    return value


# --------------------------------------------------------------------------
# v26 call-card state machine (per live ticker, in-memory).
# One latched call per 15-min window: gates -> 2-poll confirmation -> seal.
# The call stays latched until a sell trigger fires or the window rolls;
# at rollover the sealed call is graded (Kalshi result, candle fallback)
# and appended to the public record. In-memory state is lost on restart —
# only an in-flight call is lost; graded calls live in the DB-backed
# record (JSONL backup).
# --------------------------------------------------------------------------
CALL_W_TARGET = 0.85   # weight on the empirical history vote (target_p)
CALL_W_PARAM = 0.15    # weight on the parametric vote (param_p)
CALL_CONFIRM_POLLS = 2  # consecutive polls gates must pass before a call fires
CALL_MIN_MINUTES_LEFT = 2.0  # don't fire a new call with less than this left
CALL_TOO_CLOSE_PCT = 0.05  # |spot-target|/target below this -> too close to call
CALL_BAND_HALF_C = 10.0   # validity band: seal ask +/- 10c
CALL_SELL_MINUTES_LEFT = 1.0  # under this -> sell (settlement lottery)
CALL_BANK_ASK_C = 93.0   # ask at/above this -> sell (bank it)
CALL_STRONG_ASK_C = 75.0  # seal ask at/above this -> strong call
CALL_MAX_SPREAD_C = 10.0  # entry gate: spread must be at most this

_call_state: dict = {}
_call_lock = threading.Lock()
_LAST_TICKER = {"ticker": None}

# v26.8: scalp verdict confirmation — the scalp verdict only changes after
# 2 consecutive polls agree (same pattern as the call card). Default views
# advance it; custom views (force ticker / override) read it without
# advancing (read-only, like the call machine snapshot).
_scalp_state = {"pending": None, "confirm": 0, "confirmed": "none"}
_scalp_lock = threading.Lock()


def _confirm_scalp_verdict(raw_verdict):
    """Advance the scalp confirmation state.

    Returns (confirmed_verdict, confirm_count_0_to_2, confirming_bool).
    While pending != confirmed the UI shows 'confirming…'.
    """
    with _scalp_lock:
        if raw_verdict == _scalp_state["pending"]:
            _scalp_state["confirm"] += 1
        else:
            _scalp_state["pending"] = raw_verdict
            _scalp_state["confirm"] = 1
        if _scalp_state["confirm"] >= 2:
            _scalp_state["confirmed"] = raw_verdict
        return (_scalp_state["confirmed"],
                min(_scalp_state["confirm"], 2),
                _scalp_state["pending"] != _scalp_state["confirmed"])


def _scalp_disagrees(confirmed_verdict, call):
    """True when the (confirmed) scalp leans the other way from an ACTIVE
    latched call. Display-only; the call takes priority. A sold / sit-out
    call never disagrees, and a 'none' scalp verdict has no side."""
    if not isinstance(call, dict) or call.get("status") != "active":
        return False
    call_side = call.get("side")
    scalp_side = None
    if (confirmed_verdict or "").startswith("up"):
        scalp_side = "up"
    elif (confirmed_verdict or "").startswith("down"):
        scalp_side = "down"
    return bool(call_side is not None and scalp_side is not None
                and scalp_side != call_side)

# v26.2 Momentum Flip Watch (display-only): last momentum state per ticker so
# the frontend can show when momentum last flipped. Momentum is BTC-wide, so
# the key is the BTC feed itself ("BTC"), not a Kalshi market ticker.
_momentum_flip_state: dict = {}


def _new_call_state():
    return {"status": "none", "side": None, "confirm": 0,
            "flip_confirm": 0, "edge_confirm": 0,
            "sealed_at": None, "seal_ask": None, "seal_blended": None,
            "seal_price": None, "seal_target": None, "close_epoch_ms": None,
            "seal_gates": [], "band_lo": None, "band_hi": None,
            "strong": False, "sell_status": "hold", "sell_reason": None,
            "sell_triggers": []}


# --------------------------------------------------------------------------
# BTC market data: Coinbase -> Kraken fallback
# --------------------------------------------------------------------------
def get_market_klines(limit=1000):
    errors = []
    try:
        now = int(time.time())
        start_ts = now - (limit * 60 + 300)
        url = "https://api.coinbase.com/api/v3/brokerage/market/products/BTC-USD/candles"
        params = {"start": str(start_ts), "end": str(now), "granularity": "ONE_MINUTE",
                  "limit": min(limit, 350)}
        r = requests.get(url, params=params, timeout=12, headers={"Accept": "application/json"})
        r.raise_for_status()
        candles = r.json().get("candles", [])
        if not candles:
            raise ValueError("Coinbase returned no candles")
        df = pd.DataFrame(candles)
        df["time"] = pd.to_datetime(pd.to_numeric(df["start"]), unit="s", utc=True)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.sort_values("time").drop_duplicates("time").set_index("time")
        df["quote_volume"] = df["volume"] * df["close"]
        if len(df) < 100:
            raise ValueError(f"Coinbase returned only {len(df)} candles")
        return df[["open", "high", "low", "close", "volume", "quote_volume"]].tail(limit), "Coinbase", errors
    except Exception as e:
        errors.append(f"Coinbase: {e}")
    try:
        url = "https://api.kraken.com/0/public/OHLC"
        r = requests.get(url, params={"pair": "XBTUSD", "interval": 1}, timeout=12)
        r.raise_for_status()
        payload = r.json()
        if payload.get("error"):
            raise ValueError("; ".join(payload["error"]))
        result = payload.get("result", {})
        pair_key = next((k for k in result if k != "last"), None)
        if not pair_key:
            raise ValueError("Kraken returned no OHLC series")
        rows = result[pair_key]
        df = pd.DataFrame(rows, columns=["time_s", "open", "high", "low", "close", "vwap", "volume", "count"])
        df["time"] = pd.to_datetime(pd.to_numeric(df["time_s"]), unit="s", utc=True)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.sort_values("time").drop_duplicates("time").set_index("time")
        df["quote_volume"] = df["volume"] * df["close"]
        if len(df) < 100:
            raise ValueError(f"Kraken returned only {len(df)} candles")
        return df[["open", "high", "low", "close", "volume", "quote_volume"]].tail(limit), "Kraken (fallback)", errors
    except Exception as e:
        errors.append(f"Kraken: {e}")
    raise RuntimeError("All market-data sources failed. " + " | ".join(errors))


def get_market_klines_cached(limit=1000):
    return cached(("klines", limit), 20, lambda: get_market_klines(limit))


# --- Instant spot price feed (v18) ---
# /api/quick polls every 1s, so it needs its own tiny TTL — the 20s klines
# cache above would make "instant" ticks a lie. Coinbase spot is a cheap,
# fast endpoint; Kraken ticker is the fallback.
_spot_cache = {"ts": 0.0, "price": None, "source": None}
SPOT_TTL = 2  # seconds
_spot_history = deque(maxlen=180)  # (epoch, price) for the 1-minute change


def _fetch_spot():
    try:
        r = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=6)
        r.raise_for_status()
        return float(r.json()["data"]["amount"]), "Coinbase"
    except Exception:
        pass
    r = requests.get("https://api.kraken.com/0/public/Ticker",
                     params={"pair": "XBTUSD"}, timeout=6)
    r.raise_for_status()
    payload = r.json()
    if payload.get("error"):
        raise RuntimeError("; ".join(payload["error"]))
    result = payload["result"]
    key = next(k for k in result.keys() if k != "last")
    return float(result[key]["c"][0]), "Kraken"


def get_spot_price():
    now = time.time()
    if _spot_cache["price"] is not None and now - _spot_cache["ts"] < SPOT_TTL:
        return _spot_cache["price"], _spot_cache["source"]
    price, source = _fetch_spot()
    _spot_cache.update(ts=now, price=price, source=source)
    _spot_history.append((now, price))
    return price, source


def spot_change_1m_pct():
    if len(_spot_history) < 2:
        return None
    now, price = _spot_history[-1]
    for ts, p in reversed(_spot_history):
        if now - ts >= 60:
            return (price - p) / p * 100 if p else None
    ts0, p0 = _spot_history[0]
    return (price - p0) / p0 * 100 if p0 and now > ts0 else None


# --------------------------------------------------------------------------
# Kalshi discovery (4 strategies, ported from the Streamlit app)
# --------------------------------------------------------------------------
def kalshi_get(path, params=None, timeout=12):
    r = requests.get(f"{KALSHI}{path}", params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_paginated_markets(base_params, max_pages=5):
    out, cursor, seen = [], None, set()
    for _ in range(max_pages):
        params = dict(base_params)
        if cursor:
            params["cursor"] = cursor
        payload = kalshi_get("/markets", params)
        for m in payload.get("markets", []):
            t = m.get("ticker")
            if t and t not in seen:
                seen.add(t)
                out.append(m)
        cursor = payload.get("cursor")
        if not cursor:
            break
    return out


def get_close_time(market):
    for key in ("close_time", "expiration_time", "expected_expiration_time"):
        value = market.get(key)
        if value:
            try:
                return pd.to_datetime(value, utc=True)
            except Exception:
                pass
    return None


def _discover_open_btc_markets():
    errors = []
    tried = []
    try:
        tried.append("series_ticker=KXBTC15M (paginated)")
        markets = fetch_paginated_markets({"series_ticker": "KXBTC15M", "status": "open", "limit": 200})
        markets = [m for m in markets if m.get("status", "open") == "open"]
        now = pd.Timestamp.now(tz="UTC")
        markets.sort(key=lambda m: get_close_time(m) or pd.Timestamp.max.tz_localize("UTC"))
        kx = [m for m in markets if str(m.get("ticker", "")).upper().startswith("KXBTC15M")]
        future = [m for m in kx if (get_close_time(m) is None or get_close_time(m) > now)]
        if future:
            return future
        if kx:
            return kx
        if markets:
            return markets
    except Exception as e:
        errors.append(f"Series paginated lookup: {e}")
    try:
        tried.append("event scan KXBTC15M")
        payload = kalshi_get("/events", {"series_ticker": "KXBTC15M", "status": "open",
                                         "with_nested_markets": True, "limit": 50})
        found = []
        for ev in payload.get("events", []):
            for m in ev.get("markets", []) or []:
                if m.get("status", "open") == "open":
                    found.append(m)
        if found:
            found.sort(key=lambda m: get_close_time(m) or pd.Timestamp.max.tz_localize("UTC"))
            return found
    except Exception as e:
        errors.append(f"Event scan: {e}")
    try:
        tried.append("ticker-prefix scan")
        markets = fetch_paginated_markets({"status": "open", "limit": 1000}, max_pages=2)
        found = [m for m in markets if str(m.get("ticker", "")).upper().startswith("KXBTC15M")]
        if found:
            now = pd.Timestamp.now(tz="UTC")
            found.sort(key=lambda m: get_close_time(m) or pd.Timestamp.max.tz_localize("UTC"))
            future = [m for m in found if (get_close_time(m) is None or get_close_time(m) > now)]
            return future or found
    except Exception as e:
        errors.append(f"Prefix scan: {e}")
    try:
        tried.append("fallback window feed")
        r = requests.get("https://degenhedge.com/api/btc-window", timeout=10)
        r.raise_for_status()
        window = r.json()
        if window.get("available", True) and window.get("ticker"):
            pseudo = {
                "ticker": window.get("ticker"),
                "title": "Bitcoin Up or Down — current 15-minute window",
                "subtitle": "Current KXBTC15M window (read-only fallback feed)",
                "status": "open",
                "close_time": window.get("close_time"),
                "open_time": window.get("open_time"),
                "floor_strike": window.get("strike"),
                "yes_ask_dollars": (float(window["yes_ask_cents"]) / 100 if window.get("yes_ask_cents") is not None else None),
                "no_ask_dollars": (float(window["no_ask_cents"]) / 100 if window.get("no_ask_cents") is not None else None),
                "yes_bid_dollars": (float(window["yes_bid_cents"]) / 100 if window.get("yes_bid_cents") is not None else None),
                "no_bid_dollars": (float(window["no_bid_cents"]) / 100 if window.get("no_bid_cents") is not None else None),
                "_discovery_source": "DegenHedge current-window fallback",
                "_kalshi_url": window.get("kalshi_url"),
            }
            return [pseudo]
    except Exception as e:
        errors.append(f"Current-window fallback: {e}")
    raise RuntimeError("No KXBTC15M market found. Tried: " + ", ".join(tried) + ". " + " | ".join(errors))


def get_open_btc_markets_cached():
    return cached("kalshi_open_btc", 5, _discover_open_btc_markets)


def get_kalshi_market(ticker):
    return kalshi_get(f"/markets/{ticker}").get("market", {})


def extract_target_price(market):
    for key in ("floor_strike", "strike_price", "strike", "target_price", "price_level", "cap_strike"):
        try:
            number = float(market.get(key))
            if number > 1000:
                return number
        except (TypeError, ValueError):
            pass
    texts = [market.get(k, "") for k in ("title", "subtitle", "yes_sub_title", "no_sub_title", "rules_primary")]
    for value in texts:
        if not isinstance(value, str):
            continue
        for match in re.finditer(r"\$\s?([0-9]{2,3}(?:,[0-9]{3})+(?:\.\d+)?|[0-9]{4,6}(?:\.\d+)?)", value):
            try:
                number = float(match.group(1).replace(",", ""))
                if 1000 <= number <= 10000000:
                    return number
            except ValueError:
                pass
    return None


def estimate_target_probability(btc, target, minutes_left):
    close = btc["close"].astype(float)
    horizon = int(np.clip(round(minutes_left), 1, 15))
    future_return = close.shift(-horizon) / close - 1
    sample = future_return.dropna().tail(1500)
    if len(sample) < 80:
        return None, horizon, len(sample)
    required_return = target / float(close.iloc[-1]) - 1
    lam = np.log(2) / 240
    w = np.exp(-lam * np.arange(len(sample))[::-1])
    above = (sample > required_return).to_numpy(dtype=float)
    probability = float(np.average(above, weights=w))
    return probability, horizon, len(sample)


def spike_projection(btc, minutes_left):
    """Rough projected up/down spike over the remaining minutes.

    Uses one-sided realized volatility from the last 60 one-minute candles:
    expected max favorable excursion ~= 1.25 * sigma_one_sided * sqrt(h).
    Returns dict with pct moves and projected prices, or None if too little data.
    """
    close = btc["close"].astype(float)
    rets = close.pct_change().tail(60).dropna()
    if len(rets) < 20:
        return None
    pos = rets[rets > 0]
    neg = rets[rets < 0]
    # one-sided sigma scaled back to full-sample equivalent; fall back to overall vol
    up_vol = float(pos.std() * np.sqrt(2)) if len(pos) >= 10 else float(rets.std())
    down_vol = float(neg.std() * np.sqrt(2)) if len(neg) >= 10 else float(rets.std())
    h = max(float(minutes_left), 0.5)
    k = 1.25 * np.sqrt(h)
    return {"up_vol": float(up_vol), "down_vol": float(down_vol),
            "up_spike_pct": float(up_vol * k * 100),
            "down_spike_pct": float(down_vol * k * 100),
            "horizon_min": round(float(h), 1)}


def quote_cents(market, keys):
    """First available quote in integer cents, or None."""
    for key in keys:
        value = market.get(key)
        if value is not None:
            try:
                return float(value) * (100 if key.endswith("_dollars") else 1)
            except (TypeError, ValueError):
                pass
    return None


# --------------------------------------------------------------------------
# Signal + entry/exit engine (pure logic, no Flask/Streamlit)
# --------------------------------------------------------------------------
GIRL_FOR_CLS = {"up": "img/yuki_bullish.webp?v=26",
                "down": "img/yuki_bearish.webp?v=26",
                "wait": "img/yuki_chill.webp?v=26"}


def chop_ratio(btc):
    """Recent 5-min range vs its median over the last hour. >2.0 = choppy regime."""
    try:
        close = btc["close"].astype(float)
        high = btc["high"].astype(float)
        low = btc["low"].astype(float)
        rng5 = (high.tail(5).max() - low.tail(5).min()) / close.iloc[-1]
        roll = (high.rolling(5).max() - low.rolling(5).min()) / close
        med = float(roll.tail(60).median())
        return float(rng5 / med) if med > 0 else 1.0
    except Exception:
        return 1.0


def compute_call_eval(blended_up, target_p, param_p, up_ask, up_bid, down_ask, down_bid,
                      r3, r5, minutes_left, chop, current, target):
    """Pure-function evaluation of the v26 call gates (no state, no latching).

    The probability is the pure contract blend only: 0.85 * target_p +
    0.15 * param_p. No ML model, no timeframe vote, no Engine B, no AI vote
    in the call — those were the directional votes that poisoned the old
    blend (v26 audit). Flow/AI are display context only, never call inputs.

    Edge is net of the Kalshi taker fee: net_edge = blended*100 - ask - fee.
    Returns an eval dict with gates, conviction (display), and reasons.
    """
    direction = "up" if blended_up >= 0.5 else "down"
    prob_side = blended_up if direction == "up" else 1.0 - blended_up
    fee_up = kalshi_taker_fee_cents(up_ask)
    fee_down = kalshi_taker_fee_cents(down_ask)
    up_net = (blended_up * 100 - up_ask - fee_up) if (up_ask is not None and fee_up is not None) else None
    down_net = ((1 - blended_up) * 100 - down_ask - fee_down) if (down_ask is not None and fee_down is not None) else None
    edge_here = up_net if direction == "up" else down_net
    min_edge = 8.0 if minutes_left < 3 else (5.0 if minutes_left < 8 else 3.0)
    if chop > 2.0:
        min_edge += 2.0  # choppy regime demands a bigger net edge
    prob_bar = 0.65 if minutes_left < 3 else 0.60
    dist_pct = abs(current - target) / target * 100 if target else None
    too_close = bool(dist_pct is not None and dist_pct < CALL_TOO_CLOSE_PCT)
    momentum_up = r3 > 0 and r5 > 0
    momentum_down = r3 < 0 and r5 < 0
    mom_ok = momentum_up if direction == "up" else momentum_down
    ask_here = up_ask if direction == "up" else down_ask
    bid_here = up_bid if direction == "up" else down_bid
    spread = (ask_here - bid_here) if (ask_here is not None and bid_here is not None) else None
    quotes_ok = all(v is not None for v in (up_ask, up_bid, down_ask, down_bid))
    gates = [
        {"label": "Quotes available", "ok": bool(quotes_ok),
         "detail": "YES/NO bid+ask present" if quotes_ok else "Waiting on Kalshi quotes"},
        {"label": f"Spread ≤ {CALL_MAX_SPREAD_C:.0f}¢",
         "ok": (spread is None or spread <= CALL_MAX_SPREAD_C),
         "detail": f"Spread {spread:.0f}¢" if spread is not None else "Spread n/a"},
        {"label": "Probability beyond bar", "ok": prob_side >= prob_bar,
         "detail": f"{prob_side:.1%} vs {prob_bar:.0%} needed"},
        {"label": "Net edge ≥ threshold (after Kalshi fee)",
         "ok": (edge_here is not None and edge_here >= min_edge),
         "detail": (f"{edge_here:+.1f}¢ net vs {min_edge:.0f}¢ needed"
                    if edge_here is not None else "Ask unavailable")},
        {"label": "Momentum agrees (3m & 5m)", "ok": bool(mom_ok),
         "detail": f"3m {r3:+.2f}% · 5m {r5:+.2f}%"},
        {"label": "≥ 2 min left", "ok": minutes_left >= CALL_MIN_MINUTES_LEFT,
         "detail": f"{minutes_left:.1f} min left"},
        {"label": "Not too close to target", "ok": not too_close,
         "detail": (f"{dist_pct:.3f}% from target (need ≥ {CALL_TOO_CLOSE_PCT:.2f}%)"
                    if dist_pct is not None else "Distance unknown")},
    ]
    gates_pass = all(g["ok"] for g in gates)
    prob_align = max(0.0, min(1.0, abs(blended_up - 0.5) * 2))
    edge_align = max(0.0, min(1.0, edge_here / (2 * min_edge))) if edge_here is not None else 0.0
    mom_align = 1.0 if mom_ok else 0.0
    conviction = 0.5 * prob_align + 0.3 * edge_align + 0.2 * mom_align
    if too_close:
        reason = f"Too close to call — BTC within {CALL_TOO_CLOSE_PCT:.2f}% of the target."
    elif gates_pass:
        reason = (f"Contract blend {blended_up:.0%} favors "
                  f"{'UP' if direction == 'up' else 'DOWN'}; "
                  f"net edge {edge_here:+.1f}¢ after fee; momentum agrees.")
    else:
        failed = [g["label"] for g in gates if not g["ok"]]
        reason = "SIT OUT — waiting on: " + ", ".join(failed) + "."
    return {
        "direction": direction, "prob_side": prob_side, "prob_bar": prob_bar,
        "up_ask": up_ask, "up_bid": up_bid, "down_ask": down_ask, "down_bid": down_bid,
        "up_net": up_net, "down_net": down_net, "edge_here": edge_here,
        "min_edge": min_edge, "too_close": too_close, "dist_pct": dist_pct,
        "momentum_up": momentum_up, "momentum_down": momentum_down,
        "gates": gates, "gates_pass": gates_pass, "conviction": conviction,
        "reason": reason, "blended_up": blended_up, "target_p": target_p,
        "param_p": param_p, "target": target,
    }


def _grade_previous_call(old_ticker, btc):
    """Grade the sealed call for old_ticker (if any) and append it to the
    public record. Caller must hold _call_lock. Returns the row or None."""
    st = _call_state.pop(old_ticker, None)
    if not st or st["status"] not in ("active", "sold"):
        return None
    side = st["side"]
    target = st["seal_target"]
    result = None
    source = None
    try:
        m = kalshi_get(f"/markets/{old_ticker}").get("market", {})
        res = (m.get("result") or "").lower()
        if res in ("yes", "no"):
            result = "UP" if res == "yes" else "DOWN"
            source = "kalshi"
    except Exception:
        pass
    if result is None and target:
        try:
            close_ms = st.get("close_epoch_ms")
            if close_ms:
                close_ts = pd.to_datetime(close_ms, unit="ms", utc=True)
                prior = btc[btc.index <= close_ts]
                if len(prior):
                    last_close = float(prior["close"].iloc[-1])
                    result = "UP" if last_close >= target else "DOWN"
                    source = "candle"
        except Exception:
            pass
    if result is None:
        return None  # cannot grade honestly; drop rather than fabricate
    won = (side == "up") == (result == "UP")
    row = {
        "ticker": old_ticker,
        "close_epoch_ms": st.get("close_epoch_ms"),
        "side": "UP" if side == "up" else "DOWN",
        "sealed_at": st.get("sealed_at"),
        "seal_ask_c": st.get("seal_ask"),
        "seal_blended": st.get("seal_blended"),
        "seal_btc_price": st.get("seal_price"),
        "target": target,
        "result": result,
        "won": bool(won),
        "strong": bool(st.get("strong")),
        "graded_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "grade_source": source,
    }
    call_record.append_call(row)
    return row


def _machine_step(st, ev, now_iso, close_epoch_ms, current, minutes_left):
    """One state-machine transition for the live ticker (lock held)."""
    if st["status"] == "none":
        if ev["gates_pass"]:
            st["confirm"] += 1
        else:
            st["confirm"] = 0
        if st["confirm"] >= CALL_CONFIRM_POLLS:
            direction = ev["direction"]
            ask = ev["up_ask"] if direction == "up" else ev["down_ask"]
            st["status"] = "active"
            st["side"] = direction
            st["sealed_at"] = now_iso
            st["seal_ask"] = ask
            st["seal_blended"] = ev["blended_up"]
            st["seal_price"] = current
            st["seal_target"] = ev["target"]
            st["close_epoch_ms"] = close_epoch_ms
            st["seal_gates"] = list(ev["gates"])
            st["band_lo"] = (ask - CALL_BAND_HALF_C) if ask is not None else None
            st["band_hi"] = min(100.0, ask + CALL_BAND_HALF_C) if ask is not None else None
            st["strong"] = bool(ask is not None and ask >= CALL_STRONG_ASK_C)
            st["sell_status"] = "hold"
            st["sell_reason"] = None
            st["flip_confirm"] = 0
            st["edge_confirm"] = 0
    elif st["status"] == "active":
        side = st["side"]
        ask_now = ev["up_ask"] if side == "up" else ev["down_ask"]
        edge_now = ev["up_net"] if side == "up" else ev["down_net"]
        triggers = []
        time_fired = minutes_left < CALL_SELL_MINUTES_LEFT
        triggers.append({"label": "Under 60s left — settlement lottery",
                         "fired": bool(time_fired),
                         "detail": f"{minutes_left:.1f} min left"})
        bank_fired = bool(ask_now is not None and ask_now >= CALL_BANK_ASK_C)
        triggers.append({"label": "Deep in profit (ask ≥ 93¢) — bank it",
                         "fired": bank_fired,
                         "detail": (f"Ask {ask_now:.0f}¢" if ask_now is not None
                                    else "Ask unavailable")})
        if edge_now is not None and edge_now < 0:
            st["edge_confirm"] += 1
        else:
            st["edge_confirm"] = 0
        edge_fired = st["edge_confirm"] >= CALL_CONFIRM_POLLS
        triggers.append({"label": "Edge evaporated (net edge below zero)",
                         "fired": bool(edge_fired),
                         "detail": (f"Net edge {edge_now:+.1f}¢ "
                                    f"({st['edge_confirm']}/{CALL_CONFIRM_POLLS})"
                                    if edge_now is not None else "Ask unavailable")})
        flipped = ev["direction"] != side and ev["gates_pass"]
        if flipped:
            st["flip_confirm"] += 1
        else:
            st["flip_confirm"] = 0
        flip_fired = st["flip_confirm"] >= CALL_CONFIRM_POLLS
        triggers.append({"label": "Signal flipped with confirmation",
                         "fired": bool(flip_fired),
                         "detail": (f"Blend now favors "
                                    f"{'UP' if ev['direction'] == 'up' else 'DOWN'} "
                                    f"({st['flip_confirm']}/{CALL_CONFIRM_POLLS})"
                                    if flipped else "No flip")})
        st["sell_triggers"] = triggers
        fired = [t for t in triggers if t["fired"]]
        if fired:
            st["status"] = "sold"
            st["sell_status"] = "sell"
            st["sell_reason"] = fired[0]["label"]
    # status "sold": frozen until the window rolls over


def compute_scale_plan(side, seal_ask, seal_blended, size=10):
    """v26.3 Scale Plan, v26.5 fee fix (display-only guidance; Threaded
    never places trades).

    Splits a sealed call into entry tranches + an exit ladder so the user
    scales in/out instead of going all-in at one price. The plan is
    shown for a position of `size` contracts (default 10) with WHOLE-
    CONTRACT tranche counts — contracts don't divide, so a 50/30/20
    split needs >= 10 contracts to execute as shown.

    Fees are honest per-order fees: Kalshi rounds EACH order's fee UP to
    the next cent, so every tranche/exit pays its own rounded fee.
    Slicing can therefore cost slightly more (or less, when a tranche
    lands at a cheaper price) than a single fill — the summary shows
    both totals, per contract, so the comparison is real.

    Net edge is conservative: it charges the full round-trip fees (entry
    + exit) against the hold-to-settlement payoff (100c on win). Holding
    to expiry actually charges no exit fee, so the true net edge is
    slightly better than shown when an exit holds to expiry.
    Returns None when there is no sealed call (no side / no seal ask).
    """
    if side not in ("up", "down") or seal_ask is None or seal_blended is None:
        return None
    try:
        seal_ask = float(seal_ask)
        seal_blended = float(seal_blended)
        size = int(size)
    except (TypeError, ValueError):
        return None
    if not (0.0 < seal_ask < 100.0) or not (0.0 <= seal_blended <= 1.0):
        return None
    if size <= 0:
        return None
    p_side = seal_blended if side == "up" else 1.0 - seal_blended
    side_word = "UP" if side == "up" else "DOWN"

    # Whole-contract counts (round-half-up, remainder to the last tranche).
    t1_n = int(size * 0.5 + 0.5)
    t2_n = int(size * 0.3 + 0.5)
    t3_n = size - t1_n - t2_n
    e1_n = size // 2
    e2_n = size - e1_n

    t1_price = seal_ask
    t2_price = max(1.0, seal_ask - 3.0)  # dip limit; never below 1c
    t3_price = seal_ask  # confirmation tranche: only if still valid after 2 polls
    if seal_ask >= CALL_BANK_ASK_C:
        e1_price = seal_ask  # already at bank-it — sell now
        e1_note = "Already at bank-it — sell now"
    else:
        e1_price = min(seal_ask + 10.0, CALL_BANK_ASK_C)
        e1_note = ("Bank-it level (93¢)" if e1_price >= CALL_BANK_ASK_C
                   else "Partial target (seal ask + 10¢)")
    e2_price = seal_ask  # reference for the remaining exit (est.; actual exit price varies)

    def fill_fee(price, n):
        """Total fee (cents) for one order of n contracts at price."""
        if n <= 0:
            return 0.0
        return (kalshi_taker_fee_cents(price, contracts=n) or 0.0) * n

    def fill(price, n, pct, note, fid):
        total = fill_fee(price, n)
        return {"id": fid, "pct": pct, "contracts": n, "price": price,
                "fee": (total / n) if n else 0.0, "fee_total": total,
                "note": note if n else (note + " (0 contracts at this size — skipped)")}

    tranches = [
        fill(t1_price, t1_n, 50, "At seal ask — market, immediate", "T1"),
        fill(t2_price, t2_n, 30, "Dip limit — only if it fills, else skip", "T2"),
        fill(t3_price, t3_n, 20, "Confirmation — only if call still valid after 2 more polls", "T3"),
    ]
    exits = [
        fill(e1_price, e1_n, 50, e1_note, "E1"),
        fill(e2_price, e2_n, 50, "Remaining — at expiry or on a sell trigger (fee est. at seal ask)", "E2"),
    ]
    entry_total = sum(t["fee_total"] for t in tranches)
    exit_total = sum(e["fee_total"] for e in exits)
    entry_fee = entry_total / size  # per contract
    exit_fee = exit_total / size  # per contract
    plan_fee_total = entry_fee + exit_fee
    # Single fill: 1 entry + 1 exit, whole position, at the seal ask.
    single_fee_total = 2.0 * (kalshi_taker_fee_cents(seal_ask, contracts=size) or 0.0)
    weighted_entry = sum(t["contracts"] / size * t["price"] for t in tranches)
    net_edge_plan = p_side * 100.0 - weighted_entry - plan_fee_total
    net_edge_single = p_side * 100.0 - seal_ask - single_fee_total
    return {
        "side": side, "side_word": side_word, "seal_ask": seal_ask,
        "p_side": p_side, "size": size,
        "note": ("Plan shown for a %d-contract position — needs ≥10 contracts "
                 "to execute as shown (contracts don't divide)." % size),
        "tranches": tranches, "exits": exits,
        "entry_fee": entry_fee, "exit_fee": exit_fee,
        "plan_fee_total": plan_fee_total, "single_fee_total": single_fee_total,
        "weighted_entry": weighted_entry,
        "net_edge_plan": net_edge_plan, "net_edge_single": net_edge_single,
    }


def _call_public(st, ev, current, minutes_left):
    """Public call dict for state['call'] — same shape for every status."""
    status = st["status"]
    if status == "none":
        if ev["too_close"]:
            reason = ("Too close to call — BTC is within "
                      f"{CALL_TOO_CLOSE_PCT:.2f}% of the target. No bet.")
        elif ev["gates_pass"]:
            reason = (f"Gates clear — confirming "
                      f"({st['confirm']}/{CALL_CONFIRM_POLLS}).")
        else:
            failed = [g["label"] for g in ev["gates"] if not g["ok"]]
            reason = "SIT OUT — waiting on: " + ", ".join(failed) + "."
        return {
            "status": "none", "side": None, "label": "SIT OUT", "cls": "wait",
            "strong": False, "sealed_at": None, "seal_ask": None,
            "seal_blended": None, "band_lo": None, "band_hi": None,
            "band_status": None, "sell_status": "hold", "sell_reason": None,
            "confirm": st["confirm"], "confirm_needed": CALL_CONFIRM_POLLS,
            "reason": reason, "gates": ev["gates"],
            "sell_triggers": [], "conviction": ev["conviction"],
            "blended_up": ev["blended_up"], "target_p": ev["target_p"],
            "param_p": ev["param_p"], "net_edge": ev["edge_here"],
            "min_edge": ev["min_edge"], "too_close": ev["too_close"],
            "scale_plan": None,
        }
    side = st["side"]
    side_word = "UP" if side == "up" else "DOWN"
    label = f"BET {side_word}" + (" ★" if st["strong"] else "")
    ask_now = ev["up_ask"] if side == "up" else ev["down_ask"]
    band_status = None
    if st["band_lo"] is not None and ask_now is not None:
        band_status = ("in-band" if st["band_lo"] <= ask_now <= st["band_hi"]
                       else "out-of-band")
    if status == "active":
        reason = (f"Call sealed {st['sealed_at']} at {st['seal_ask']:.0f}¢ ask. "
                  f"Blended P {st['seal_blended']:.0%}.")
        if band_status == "out-of-band":
            reason += " Price moved out of the entry band — do not chase a fresh entry."
    else:  # sold
        reason = (f"SELL — {st['sell_reason']}. Call sealed {st['sealed_at']} "
                  f"at {st['seal_ask']:.0f}¢ ask.")
    return {
        "status": status, "side": side, "label": label,
        "cls": side, "strong": bool(st["strong"]),
        "sealed_at": st["sealed_at"], "seal_ask": st["seal_ask"],
        "seal_blended": st["seal_blended"],
        "band_lo": st["band_lo"], "band_hi": st["band_hi"],
        "band_status": band_status,
        "sell_status": st["sell_status"], "sell_reason": st["sell_reason"],
        "confirm": st["confirm"], "confirm_needed": CALL_CONFIRM_POLLS,
        "reason": reason, "gates": st["seal_gates"] or ev["gates"],
        "sell_triggers": st["sell_triggers"],
        "conviction": ev["conviction"],
        "blended_up": ev["blended_up"], "target_p": ev["target_p"],
        "param_p": ev["param_p"], "net_edge": ev["edge_here"],
        "min_edge": ev["min_edge"], "too_close": ev["too_close"],
        "scale_plan": compute_scale_plan(side, st["seal_ask"], st["seal_blended"]),
    }


def call_machine_update(ticker, call_eval, close_epoch_ms, btc, current, minutes_left):
    """Advance the per-ticker call state machine (live view only).
    Grades the previous window's sealed call on rollover."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with _call_lock:
        if _LAST_TICKER["ticker"] is not None and _LAST_TICKER["ticker"] != ticker:
            _grade_previous_call(_LAST_TICKER["ticker"], btc)
        _LAST_TICKER["ticker"] = ticker
        if len(_call_state) > 60:
            for k in list(_call_state)[: len(_call_state) - 60]:
                del _call_state[k]
        st = _call_state.get(ticker)
        if st is None:
            st = _new_call_state()
            _call_state[ticker] = st
        _machine_step(st, call_eval, now_iso, close_epoch_ms, current, minutes_left)
        return _call_public(st, call_eval, current, minutes_left)


def call_machine_snapshot(ticker, call_eval, current, minutes_left):
    """Read-only view of the call state for custom views (no transitions)."""
    with _call_lock:
        st = _call_state.get(ticker)
        if st is None:
            st = _new_call_state()
        return _call_public(st, call_eval, current, minutes_left)


def sit_out_call(reason, blended_up=None, target_p=None, param_p=None):
    """SIT OUT call dict for fallbacks (no market / no target / no history)."""
    return {
        "status": "none", "side": None, "label": "SIT OUT", "cls": "wait",
        "strong": False, "sealed_at": None, "seal_ask": None,
        "seal_blended": None, "band_lo": None, "band_hi": None,
        "band_status": None, "sell_status": "hold", "sell_reason": None,
        "confirm": 0, "confirm_needed": CALL_CONFIRM_POLLS,
        "reason": reason, "gates": [], "sell_triggers": [],
        "conviction": 0.0, "blended_up": blended_up, "target_p": target_p,
        "param_p": param_p, "net_edge": None, "min_edge": None,
        "too_close": False, "scale_plan": None,
    }


def entry_exit_detail(call_eval, call):
    """Entry gates + sell triggers in the legacy entry/exit shape, so the
    existing buy/sell checklist UI keeps working on the new call logic."""
    gates = call["gates"] or call_eval["gates"]
    passed = sum(1 for g in gates if g["ok"])
    total = len(gates)
    missing = [g["label"] for g in gates if not g["ok"]]
    if call["status"] in ("active", "sold"):
        entry_status = "open"  # gates passed at seal; the call is latched
    elif total and passed == total:
        entry_status = "open"
    elif total and passed >= total - 1:
        entry_status = "almost"
    else:
        entry_status = "no"
    direction = call["side"] or call_eval["direction"]
    side_word = "UP" if direction == "up" else "DOWN"
    entry = {"side": side_word,
             "buy_label": f"YES ({side_word})" if direction == "up" else f"NO ({side_word})",
             "checks": gates, "passed": passed, "total": total,
             "status": entry_status, "missing": missing}
    triggers = call["sell_triggers"]
    fired_labels = [t["label"] for t in triggers if t["fired"]]
    exit_ = {"triggers": triggers, "fired_labels": fired_labels,
             "status": "exit" if fired_labels else "hold"}
    return entry, exit_


def compute_scalp(blended_up, up_edge, down_edge, up_ask, up_bid, down_ask, down_bid,
                  r1, r3, r5, minutes_left, current, target, proj, chop,
                  flow=None, closes=None):
    """Fast Yes(up)/No(down) scalp scores, 0-100 each, plus rough 2-min stats.

    A scalp is a quick in-and-out: it wants a real edge vs the ask, momentum
    pointing the same way, room to reach the target, a tight spread, and
    enough time left that it isn't an expiry coin-flip. Microstructure flow
    (order-book / trade / Kalshi-book) now tilts the score, and each side
    reports rough 2-minute stats: win probability, expected gain/heat in
    contract cents, and EV = (2p-1)*gain - spread - fee (Kalshi taker fee,
    ceil per order, 1 contract). Rough — research only.
    """
    # --- microstructure tilt: +1 = bullish order-flow pressure ---
    flow_raw = 0.0
    if flow:
        b = flow.get("book_imb") or 0.0
        f = flow.get("trade_flow") or 0.0
        k = flow.get("kalshi_book_imb") or 0.0
        flow_raw = max(-1.0, min(1.0, 0.4 * b + 0.4 * f + 0.2 * k))

    # --- contract sensitivity: cents gained per 1% BTC move (numeric bump
    #     on the parametric model) + 1-min sigma for the 2-min excursion ---
    sens_c_per_pct, sigma_1m = None, None
    if closes is not None and current and target and minutes_left > 0:
        try:
            c = np.asarray(closes, dtype=float)
            rets = np.diff(c) / c[:-1]
            rets = rets[np.isfinite(rets)][-60:]
            if len(rets) >= 20:
                sigma_1m = float(np.std(rets))
                p0 = parametric_up_prob(current, target, minutes_left, c)
                p1 = parametric_up_prob(current * 1.0005, target, minutes_left, c)
                if p0 is not None and p1 is not None:
                    sens_c_per_pct = (p1 - p0) * 100.0 / 0.05
        except Exception:
            sens_c_per_pct, sigma_1m = None, None

    def side(edge, ask, bid, aligned, accelerating, need_pct, covers, flow_tilt):
        score = 50.0
        reasons = []
        if edge is not None:
            e = max(-15.0, min(15.0, edge))
            score += e * 3.0
            if edge >= 6:
                reasons.append(f"+{edge:.0f}c edge vs ask")
            elif edge >= 2:
                reasons.append(f"+{edge:.0f}c edge")
            elif edge <= -3:
                reasons.append(f"{edge:.0f}c negative edge")
        if aligned:
            score += 10
            reasons.append("momentum aligned")
        else:
            score -= 8
            reasons.append("momentum against")
        if accelerating:
            score += 6
            reasons.append("accelerating")
        if need_pct is not None:
            if need_pct <= 0:
                score -= 14
                reasons.append("already past target — no room")
            elif covers:
                score += 8
                reasons.append("target within reach")
            else:
                score -= 6
                reasons.append("target looks far")
        if ask is not None and bid is not None:
            spread = ask - bid
            if spread <= 6:
                score += 5
                reasons.append("tight spread")
            elif spread > 12:
                score -= 12
                reasons.append("wide spread — expensive")
        if minutes_left < 1.5:
            score = min(score, 20)
            reasons.append("too close to expiry")
        elif minutes_left < 4:
            score -= 6
            reasons.append("little time left")
        if chop is not None and chop > 1.6:
            score -= 10
            reasons.append("choppy — fakes likely")
        if abs(flow_tilt) > 0.05:
            score += 8 * flow_tilt
            reasons.append("order flow with you" if flow_tilt > 0 else "order flow against you")

        # --- rough 2-minute scalp stats ---
        tilt = (0.10 if aligned else -0.06) + 0.12 * flow_tilt
        tilt = max(-0.30, min(0.30, tilt))
        p_2m = 0.5 + tilt * 0.7  # shrunk toward 0.5, same philosophy as parametric
        spread_c = round(ask - bid, 1) if (ask is not None and bid is not None) else None
        # v26.8: scalp EV subtracts the Kalshi taker fee (ceil per order,
        # 1 contract) in addition to the spread. ev_after_fee is the headline
        # EV; ev_c (spread-only) is kept for reference only.
        fee_c = kalshi_taker_fee_cents(ask, contracts=1) if ask is not None else None
        fee_c = round(fee_c, 2) if fee_c is not None else None
        exp_gain_c = exp_heat_c = ev_c = ev_after_fee_c = None
        if sens_c_per_pct is not None and sigma_1m:
            fav_pct = 0.8 * sigma_1m * np.sqrt(2.0) * 100.0
            g = round(min(30.0, abs(sens_c_per_pct) * fav_pct), 1)
            exp_gain_c, exp_heat_c = g, g
            if spread_c is not None:
                ev_c = round((2 * p_2m - 1) * g - spread_c, 1)
                if fee_c is not None:
                    ev_after_fee_c = round(ev_c - fee_c, 1)
        stats = {"p_2m": round(p_2m, 3), "spread_c": spread_c,
                 "exp_gain_c": exp_gain_c, "exp_heat_c": exp_heat_c, "ev_c": ev_c,
                 "fee_c": fee_c, "ev_after_fee_c": ev_after_fee_c}
        return max(0.0, min(100.0, score)), reasons[:3], stats

    req_up_pct = (target - current) / current * 100
    req_down_pct = (current - target) / current * 100
    covers_up = bool(proj and req_up_pct <= proj["up_spike_pct"])
    covers_down = bool(proj and req_down_pct <= proj["down_spike_pct"])

    up_aligned = r1 > 0 and r3 > 0
    down_aligned = r1 < 0 and r3 < 0
    up_accel = r1 > (r3 / 3) + 0.005
    down_accel = r1 < (r3 / 3) - 0.005

    up_score, up_reasons, up_stats = side(up_edge, up_ask, up_bid, up_aligned, up_accel,
                                            req_up_pct, covers_up, flow_raw)
    down_score, down_reasons, down_stats = side(down_edge, down_ask, down_bid, down_aligned,
                                                down_accel, req_down_pct, covers_down, -flow_raw)

    def label_for(s):
        if s >= 70:
            return "SCALP"
        if s >= 55:
            return "LEAN"
        return "WAIT"

    best = "up" if up_score >= down_score else "down"
    best_score = max(up_score, down_score)
    verdict = "none"
    if best_score >= 70:
        verdict = best
    elif best_score >= 55:
        verdict = best + "_lean"
    return {
        "up": {"score": round(up_score), "label": label_for(up_score), "reasons": up_reasons,
               "stats": up_stats},
        "down": {"score": round(down_score), "label": label_for(down_score), "reasons": down_reasons,
                 "stats": down_stats},
        "verdict": verdict,
        "best_score": round(best_score),
    }


def _f(x):
    """JSON-safe float (None for NaN/non-finite)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def _signal_from_call(call, horizon=None, sample_count=None, up_edge=None,
                      down_edge=None, momentum_up=None, momentum_down=None,
                      components=None, prob_bar=None):
    """state['signal'] dict from a call dict (call-card shape)."""
    tier = "sitout"
    if call["status"] != "none":
        tier = "strong" if call["strong"] else "call"
    return {
        "label": call["label"], "cls": call["cls"], "tier": tier,
        "girl": GIRL_FOR_CLS[call["cls"]],
        "blended_up": call["blended_up"], "target_p": call["target_p"],
        "param_p": call["param_p"], "horizon": horizon,
        "sample_count": sample_count, "reason": call["reason"],
        "prob_bar": prob_bar, "min_edge": call["min_edge"],
        "up_edge": up_edge, "down_edge": down_edge,
        "momentum_up": momentum_up, "momentum_down": momentum_down,
        "conviction": call["conviction"], "components": components,
    }

# --------------------------------------------------------------------------
# Full dashboard state builder
# --------------------------------------------------------------------------
def compute_state(force_ticker="", target_override=0.0, history=750, selected_ticker=""):
    # NOTE (v26): `history` is accepted but ignored — there is no ML model
    # left to train, so the training-window parameter has no effect.
    errors = []

    # --- BTC data (v26: no ML model — the model was inverted in live config) ---
    btc, data_source, source_errors = get_market_klines_cached(limit=1000)
    btc = btc.dropna(subset=["close"])
    if len(btc) < 150:
        raise RuntimeError("The price feed returned too little data to compute a signal.")
    current = float(btc["close"].iloc[-1])
    start_time = btc.index[-1]
    last_ret = float(btc["close"].pct_change().iloc[-1] * 100)

    close_series = btc["close"].astype(float)
    r1 = float(close_series.pct_change(1).iloc[-1] * 100)
    r3 = float((close_series.iloc[-1] / close_series.iloc[-4] - 1) * 100) if len(close_series) >= 4 else 0.0
    r5 = float((close_series.iloc[-1] / close_series.iloc[-6] - 1) * 100) if len(close_series) >= 6 else 0.0

    # --- v26.2 Momentum Flip Watch (display-only; never a call input) ---
    # A momentum flip happens when price crosses back over the close from 3
    # (r3) or 5 (r5) candles ago, so the flip levels ARE those closes.
    # r3_flip_price / r5_flip_price are the prices where r3 / r5 cross zero.
    # Distances are signed % from the current price to each flip level.
    r3_flip_price = float(close_series.iloc[-4]) if len(close_series) >= 4 else None
    r5_flip_price = float(close_series.iloc[-6]) if len(close_series) >= 6 else None
    r3_dist_pct = ((current - r3_flip_price) / r3_flip_price * 100) if r3_flip_price else None
    r5_dist_pct = ((current - r5_flip_price) / r5_flip_price * 100) if r5_flip_price else None
    mom_state = "up" if (r3 > 0 and r5 > 0) else ("down" if (r3 < 0 and r5 < 0) else "mixed")
    flip_watch = False
    if r3_flip_price is not None and r5_flip_price is not None:
        flip_watch = bool(
            abs(r3) < 0.05 or abs(r5) < 0.05
            or (r1 < 0 and r3 > 0 and r5 > 0)   # 1m pushing against up momentum
            or (r1 > 0 and r3 < 0 and r5 < 0))  # 1m pushing against down momentum
    with _call_lock:
        prev = _momentum_flip_state.get("BTC")
        if prev is None:
            _momentum_flip_state["BTC"] = {"state": mom_state, "last_flip": None}
            last_flip = None
        elif prev["state"] != mom_state:
            last_flip = {"from": prev["state"], "to": mom_state,
                         "at": pd.Timestamp.now(tz="UTC").isoformat()}
            _momentum_flip_state["BTC"] = {"state": mom_state, "last_flip": last_flip}
        else:
            last_flip = prev["last_flip"]

    state = {
        "ok": True,
        "btc": {
            "price": current,
            "change_1m_pct": last_ret,
            "source": data_source,
            "source_errors": source_errors,
            "candle_time_utc": start_time.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "candle_time_short": start_time.strftime("%H:%M:%S"),
        },
        "momentum": {"r1": r1, "r3": r3, "r5": r5,
                     "state": mom_state,
                     "r3_flip_price": r3_flip_price, "r5_flip_price": r5_flip_price,
                     "r3_dist_pct": r3_dist_pct, "r5_dist_pct": r5_dist_pct,
                     "flip_watch": flip_watch, "last_flip": last_flip},
        "markets": [],
        "market": None,
        "market_error": None,
        "signal": None,
        "call": None,
        "entry": None,
        "exit": None,
        "scalp": None,
        "projection": None,
        "market_split": {"up_pct": None, "down_pct": None},
        "errors": errors,
    }

    # --- Kalshi discovery ---
    try:
        markets = get_open_btc_markets_cached()
    except Exception as e:
        markets = []
        state["market_error"] = f"Could not auto-discover open Kalshi BTC markets: {e}"
    now_utc = pd.Timestamp.now(tz="UTC")
    for m in markets:
        ct = get_close_time(m)
        state["markets"].append({
            "ticker": m.get("ticker", ""),
            "title": m.get("title") or m.get("subtitle") or m.get("ticker", "Untitled market"),
            "close_epoch_ms": int(ct.timestamp() * 1000) if ct is not None else None,
            "close_label": ct.strftime("%H:%M:%S UTC") if ct is not None else "close time unknown",
            "live": bool(ct is not None and ct > now_utc),
            "source": m.get("_discovery_source", "Kalshi KXBTC15M series"),
        })

    # --- select market ---
    selected_market = None
    if force_ticker.strip():
        try:
            selected_market = get_kalshi_market(force_ticker.strip())
        except Exception as e:
            state["market_error"] = f"Could not load ticker {force_ticker.strip()}: {e}"
    elif selected_ticker.strip():
        match = next((m for m in markets if m.get("ticker") == selected_ticker.strip()), None)
        if match is not None:
            selected_market = match
        elif markets:
            selected_market = markets[0]
    elif markets:
        selected_market = markets[0]

    if selected_market is None:
        # No Kalshi market available -> SIT OUT (v26: no model-only fallback;
        # the ML model was inverted and must never be shown as a signal).
        call = sit_out_call("No Kalshi market available — no call.")
        state["call"] = call
        state["signal"] = _signal_from_call(call)
        if not state["market_error"]:
            state["market_error"] = ("No KXBTC15M market returned. Try again in a few seconds.")
        return state

    ticker_selected = selected_market.get("ticker", force_ticker.strip())
    try:
        fresh = get_kalshi_market(ticker_selected)
        selected_market = {**selected_market, **{k: v for k, v in fresh.items() if v is not None}}
    except Exception:
        pass

    # --- target ---
    target = float(target_override) if target_override and float(target_override) > 0 else extract_target_price(selected_market)
    target_source = ("manual override" if (target_override and float(target_override) > 0)
                     else ("target parsed from Kalshi market fields" if target is not None else ""))
    close_at = get_close_time(selected_market)
    if target is None and close_at is not None:
        inferred_start = close_at - pd.Timedelta(minutes=15)
        candle_times = btc.index
        nearest_idx = int(np.argmin(np.abs((candle_times - inferred_start).total_seconds())))
        nearest_time = candle_times[nearest_idx]
        if abs((nearest_time - inferred_start).total_seconds()) <= 180:
            target = float(btc.iloc[nearest_idx]["open"])
            target_source = f"estimated from BTC candle at window start ({nearest_time.strftime('%H:%M:%S UTC')})"

    minutes_left = max(0.0, (close_at - now_utc).total_seconds() / 60) if close_at is not None else 15.0
    mins = int(minutes_left)
    secs = int((minutes_left % 1) * 60)
    mins_display = f"{mins}m {secs:02d}s" if close_at is not None else "unknown"

    up_bid = quote_cents(selected_market, ("yes_bid", "yes_bid_dollars", "yes_bid_cents"))
    up_ask = quote_cents(selected_market, ("yes_ask", "yes_ask_dollars", "yes_ask_cents"))
    down_bid = quote_cents(selected_market, ("no_bid", "no_bid_dollars", "no_bid_cents"))
    down_ask = quote_cents(selected_market, ("no_ask", "no_ask_dollars", "no_ask_cents"))
    last = quote_cents(selected_market, ("last_price", "last_price_dollars", "last_price_cents"))

    state["market"] = {
        "ticker": ticker_selected,
        "title": selected_market.get("title") or selected_market.get("subtitle") or ticker_selected,
        "subtitle": selected_market.get("subtitle") or "",
        "yes_sub_title": selected_market.get("yes_sub_title") or "",
        "no_sub_title": selected_market.get("no_sub_title") or "",
        "rules_primary": selected_market.get("rules_primary") or "",
        "status": selected_market.get("status", "unknown"),
        "close_epoch_ms": int(close_at.timestamp() * 1000) if close_at is not None else None,
        "close_utc": close_at.strftime("%Y-%m-%d %H:%M:%S UTC") if close_at is not None else None,
        "minutes_left": minutes_left,
        "mins_display": mins_display,
        "target": target,
        "target_source": target_source,
        "up_bid": up_bid, "up_ask": up_ask,
        "down_bid": down_bid, "down_ask": down_ask, "last": last,
        "spot_vs_target": (current - target) if target else None,
        "spot_vs_target_pct": ((current - target) / target * 100) if target else None,
        "above": (current > target) if target else None,
        "discovery_source": selected_market.get("_discovery_source", "Kalshi KXBTC15M series"),
    }

    if target is None or target <= 0:
        state["market_error"] = ("Couldn't identify a numeric target for this contract. "
                                 "The app will not guess.")
        call = sit_out_call("Target unknown — no call.")
        state["call"] = call
        state["signal"] = _signal_from_call(call)
        return state

    # --- blended probability: pure contract votes only (v26) ---
    target_p, horizon, sample_count = estimate_target_probability(btc, target, minutes_left)
    if target_p is None:
        state["market_error"] = "Not enough completed historical windows to estimate target probability yet."
        call = sit_out_call("Historical estimate unavailable — no call.")
        state["call"] = call
        state["signal"] = _signal_from_call(call)
        return state

    # --- v26: pure contract blend + call card ---
    # blended = 0.85 * target_p (empirical P above target) + 0.15 * param_p
    # (normal-model P above target). No ML model, no timeframe vote, no
    # Engine B, no AI vote — the directional votes that poisoned the old
    # blend are gone. Flow/AI are display context only.
    param_p = parametric_up_prob(current, target, minutes_left, close_series)
    blended_up = (CALL_W_TARGET * target_p + CALL_W_PARAM * param_p
                  if param_p is not None else target_p)
    momentum_up = r3 > 0 and r5 > 0
    momentum_down = r3 < 0 and r5 < 0
    chop = chop_ratio(btc)

    # --- microstructure flow (display + scalp tilt + AI snapshot only) ---
    try:
        flow = get_flow_signals(ticker_selected)
    except Exception:
        flow = None
    if flow is not None:
        r15 = float(close_series.pct_change(15).iloc[-1] * 100) if len(close_series) >= 16 else 0.0
        flow["_r15_fallback"] = r15
    ai_snapshot = {
        "btc_price": round(current, 1), "target": round(target, 1),
        "minutes_left": round(minutes_left, 1),
        "above_target": bool(current > target),
        "r1_pct": round(r1, 3), "r3_pct": round(r3, 3), "r5_pct": round(r5, 3),
        "history_p_up": round(target_p, 3),
        "param_p_up": round(param_p, 3) if param_p is not None else None,
        "blended_p_up": round(blended_up, 3),
        "up_ask_c": up_ask, "down_ask_c": down_ask,
        "up_bid_c": up_bid, "down_bid_c": down_bid,
        "book_imbalance": _f((flow or {}).get("book_imb")),
        "trade_flow": _f((flow or {}).get("trade_flow")),
        "kalshi_book_imb": _f((flow or {}).get("kalshi_book_imb")),
        "funding_ann_pct": round((flow or {}).get("funding_ann") * 100, 2)
                            if (flow or {}).get("funding_ann") is not None else None,
        "oi_chg_15m_pct": round((flow or {}).get("oi_chg_15m") * 100, 2)
                            if (flow or {}).get("oi_chg_15m") is not None else None,
        "chop_ratio": round(chop, 2),
    }
    try:
        ai = get_ai_read(ticker_selected, ai_snapshot)
    except Exception:
        ai = None

    # --- call evaluation + state machine ---
    call_eval = compute_call_eval(
        blended_up, target_p, param_p, up_ask, up_bid, down_ask, down_bid,
        r3, r5, minutes_left, chop, current, target)
    up_edge = call_eval["up_net"]
    down_edge = call_eval["down_net"]
    # Only the live auto-discovered view advances the machine. Custom views
    # (force ticker / target override) get a read-only snapshot so they can
    # never fire, seal, or grade calls for a market they are not tracking.
    live_view = (not force_ticker.strip() and not target_override
                 and not selected_ticker.strip())
    close_epoch_ms = int(close_at.timestamp() * 1000) if close_at is not None else None
    if live_view:
        call = call_machine_update(ticker_selected, call_eval, close_epoch_ms,
                                   btc, current, minutes_left)
    else:
        call = call_machine_snapshot(ticker_selected, call_eval, current, minutes_left)

    # --- Why-panel components (display only; nothing here changes the call) ---
    comp = {
        "target_p": float(target_p),
        "param_p": float(param_p) if param_p is not None else None,
        "blended_up": float(blended_up),
        "edge_cents": float(call_eval["edge_here"]) if call_eval["edge_here"] is not None else None,
        "momentum": "up" if momentum_up else ("down" if momentum_down else "mixed"),
        "r3": float(r3), "r5": float(r5),
        "chop_ratio": float(chop), "choppy": bool(chop > 2.0),
        "min_edge": float(call_eval["min_edge"]),
        "too_close": bool(call_eval["too_close"]),
        "flow_parts": None, "funding_ann": None, "oi_chg_15m": None,
        "ai_p_up": None, "ai_confidence": None,
    }
    if flow:
        raw_parts = {}
        for k in ("book_imb", "trade_flow", "kalshi_book_imb"):
            if flow.get(k) is not None:
                raw_parts[{"book_imb": "book", "trade_flow": "flow",
                           "kalshi_book_imb": "kbook"}[k]] = float(flow[k])
        comp["flow_parts"] = raw_parts or None
        comp["funding_ann"] = _f(flow.get("funding_ann"))
        comp["oi_chg_15m"] = _f(flow.get("oi_chg_15m"))
    if ai and ai.get("p_up") is not None:
        comp["ai_p_up"] = float(ai["p_up"])
        comp["ai_confidence"] = _f(ai.get("confidence"))

    state["flow"] = {k: _f(v) for k, v in (flow or {}).items() if not k.startswith("_")}
    state["ai"] = ai
    state["ai_enabled"] = ai_configured()
    state["sentiment"] = get_sentiment()
    state["call"] = call
    state["signal"] = _signal_from_call(
        call, horizon=horizon, sample_count=sample_count,
        up_edge=up_edge, down_edge=down_edge,
        momentum_up=momentum_up, momentum_down=momentum_down,
        components=comp, prob_bar=call_eval["prob_bar"])

    # --- spike projection (rough up/down move over remaining time) ---
    proj = spike_projection(btc, minutes_left)
    req_up_pct = (target - current) / current * 100
    req_down_pct = (current - target) / current * 100
    if proj is not None:
        state["projection"] = {
            "up_spike_pct": proj["up_spike_pct"],
            "down_spike_pct": proj["down_spike_pct"],
            "up_spike_price": current * (1 + proj["up_spike_pct"] / 100),
            "down_spike_price": current * (1 - proj["down_spike_pct"] / 100),
            "req_up_pct": req_up_pct,
            "req_down_pct": req_down_pct,
            "horizon_min": proj["horizon_min"],
            "covers_up": req_up_pct <= proj["up_spike_pct"],
            "covers_down": req_down_pct <= proj["down_spike_pct"],
        }
    else:
        state["projection"] = None

    # --- entry gates / sell triggers (from the call logic) ---
    entry, exit_ = entry_exit_detail(call_eval, call)
    state["entry"] = entry
    state["exit"] = exit_

    # --- scalp signals (fast up/down; fed by the pure blend + net edge) ---
    raw_scalp = compute_scalp(
        blended_up, up_edge, down_edge, up_ask, up_bid, down_ask, down_bid,
        r1, r3, r5, minutes_left, current, target, proj, chop,
        flow=flow, closes=close_series)
    # v26.8: scalp verdict only changes after 2 consecutive polls agree.
    # Default views advance the confirmation; custom views read it without
    # advancing (read-only, like the call machine snapshot).
    _is_default_view = (force_ticker == "" and target_override == 0.0
                        and selected_ticker == "")
    if _is_default_view:
        confirmed, confirm_n, confirming = _confirm_scalp_verdict(raw_scalp["verdict"])
    else:
        with _scalp_lock:
            confirmed = _scalp_state["confirmed"]
            confirming = _scalp_state["pending"] != confirmed
            confirm_n = min(_scalp_state["confirm"], 2)
    raw_scalp["verdict"] = confirmed
    raw_scalp["confirm"] = confirm_n
    raw_scalp["confirming"] = confirming
    # v26.8: disagreement flag — scalp leans the other way from the latched
    # call. Display-only; the call card takes priority. Never changes the call.
    raw_scalp["disagrees_with_call"] = _scalp_disagrees(confirmed, call)
    if raw_scalp["disagrees_with_call"]:
        _scalp_side = "up" if confirmed.startswith("up") else "down"
        _side_word = "UP" if _scalp_side == "up" else "DOWN"
        _call_word = "UP" if call.get("side") == "up" else "DOWN"
        raw_scalp["disagree_note"] = (
            f"Scalp leans {_side_word} but the call is {_call_word} — "
            "the call takes priority.")
    else:
        raw_scalp["disagree_note"] = None
    state["scalp"] = raw_scalp
    # v26.8: live market split — what the MARKET is pricing (midpoint of the
    # Kalshi quotes), NOT Threaded's model probability. Kalshi doesn't
    # publish voter counts, so this is price-implied, not a vote count.
    if up_ask is not None and up_bid is not None:
        _up_mid = (up_bid + up_ask) / 2.0
        state["market_split"] = {
            "up_pct": round(_up_mid, 1),
            "down_pct": round(100.0 - _up_mid, 1),
        }
    else:
        state["market_split"] = {"up_pct": None, "down_pct": None}
    # live executable quotes (cents) for the paper-betting card
    state["quotes"] = {
        "yes_ask": up_ask, "yes_bid": up_bid,
        "no_ask": down_ask, "no_bid": down_bid,
    }
    return state


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
_state_cache = {"ts": 0.0, "key": None, "data": None}
_state_lock = threading.Lock()
STATE_TTL = 5  # seconds: repeat polls inside this window reuse the last result
DEFAULT_KEY = ("", 0.0, 750, "")


def _warm_loop():
    """Background refresher: recompute the default dashboard state every few
    seconds so /api/state answers instantly from cache instead of blocking
    on a ~20s model retrain. Never clobbers a user's custom-key view."""
    while True:
        try:
            data = compute_state()
            with _state_lock:
                if _state_cache["key"] in (None, DEFAULT_KEY):
                    _state_cache.update(ts=time.time(), key=DEFAULT_KEY, data=data)
        except Exception:
            pass
        time.sleep(6)


_warmer = {"started": False, "lock": threading.Lock()}


def _ensure_warmer():
    # lazy start on first request: importing this module (tests, scripts)
    # must not spawn background threads as a side effect.
    with _warmer["lock"]:
        if not _warmer["started"]:
            _warmer["started"] = True
            threading.Thread(target=_warm_loop, daemon=True).start()


@app.route("/api/state")
@subscriber_required
def api_state():
    try:
        _ensure_warmer()
        force_ticker = request.args.get("force_ticker", "")
        selected_ticker = request.args.get("ticker", "")
        try:
            target_override = float(request.args.get("target_override", "0") or 0)
        except ValueError:
            target_override = 0.0
        try:
            history = int(request.args.get("history", "750") or 750)
        except ValueError:
            history = 750
        if history not in (300, 500, 750, 1000):
            history = 750
        key = (force_ticker, target_override, history, selected_ticker)
        now = time.time()
        with _state_lock:
            if _state_cache["key"] == key and now - _state_cache["ts"] < STATE_TTL:
                return jsonify(_state_cache["data"])
        data = compute_state(force_ticker, target_override, history, selected_ticker)
        with _state_lock:
            _state_cache.update(ts=now, key=key, data=data)
        return jsonify(data)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})


@app.route("/api/quick")
@subscriber_required
def api_quick():
    """Instant price tick — own 2s TTL so 1s polling shows fresh prices."""
    try:
        price, data_source = get_spot_price()
        return jsonify({
            "ok": True,
            "price": price,
            "change_1m_pct": spot_change_1m_pct(),
            "source": data_source,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})


def kraken_ohlc_candles(interval_min):
    """Native Kraken OHLC candles at one of Kraken's own intervals."""
    r = requests.get("https://api.kraken.com/0/public/OHLC",
                     params={"pair": "XBTUSD", "interval": interval_min}, timeout=12)
    r.raise_for_status()
    payload = r.json()
    if payload.get("error"):
        raise RuntimeError("; ".join(payload["error"]))
    result = payload["result"]
    key = next(k for k in result.keys() if k != "last")
    recs = [{"t": pd.Timestamp(int(row[0]), unit="s", tz="UTC"),
             "open": float(row[1]), "high": float(row[2]),
             "low": float(row[3]), "close": float(row[4])}
            for row in result[key]]
    return pd.DataFrame(recs).set_index("t").sort_index()


@app.route("/api/candles")
@subscriber_required
def api_candles():
    try:
        history = request.args.get("history", "24h")
        interval = request.args.get("interval", "15m")
        # Kraken's native chart intervals
        kraken_native = {"1H": 60, "4H": 240, "1D": 1440, "1W": 10080}
        resample_minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30}
        period_minutes = {"1h": 60, "6h": 360, "24h": 1440, "7d": 10080,
                          "30d": 43200, "90d": 129600}
        if history not in period_minutes:
            return jsonify({"ok": False, "error": "bad history/interval"})
        if interval in kraken_native:
            im = kraken_native[interval]
            chart_df = cached(f"kraken_ohlc_{interval}", 60,
                              lambda: kraken_ohlc_candles(im))
            chart_source = "Kraken OHLC (native interval)"
            cutoff = chart_df.index.max() - pd.Timedelta(minutes=period_minutes[history])
            chart_df = chart_df.loc[chart_df.index >= cutoff].tail(500)
        elif interval in resample_minutes:
            bar_minutes = resample_minutes[interval]
            chart_df, chart_source = None, ""
            if history == "7d":
                try:
                    hist = yf.download("BTC-USD", period="7d", interval="1m", progress=False,
                                       auto_adjust=True, threads=False)
                    if hist is not None and not hist.empty:
                        if isinstance(hist.columns, pd.MultiIndex):
                            hist.columns = hist.columns.get_level_values(0)
                        hist.index = pd.to_datetime(hist.index, utc=True)
                        hist = hist.rename(columns={"Open": "open", "High": "high", "Low": "low",
                                                    "Close": "close", "Volume": "volume"})
                        chart_df = hist.dropna(subset=["open", "high", "low", "close"])
                        chart_source = "Yahoo Finance BTC-USD historical candles"
                except Exception as e:
                    chart_source = f"Yahoo unavailable ({e}); using exchange feed"
            if chart_df is None:
                btc, data_source, _ = get_market_klines_cached(limit=1000)
                chart_df = btc
                chart_source = data_source
            if chart_df.index.tz is None:
                chart_df.index = chart_df.index.tz_localize("UTC")
            chart_df = chart_df.sort_index()
            if history != "7d":
                chart_df = chart_df.loc[chart_df.index >= (chart_df.index.max()
                                                          - pd.Timedelta(minutes=period_minutes[history]))]
            if bar_minutes > 1:
                rule = f"{bar_minutes}min"
                chart_df = chart_df.resample(rule).agg(
                    {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
            tails = {"1h": 62, "6h": 362, "24h": 1442, "7d": 10082,
                     "30d": 43202, "90d": 129602}
            chart_df = chart_df.tail(max(1, tails[history] // bar_minutes + 2))
        else:
            return jsonify({"ok": False, "error": "bad history/interval"})
        candles = [{"t": int(ts.timestamp() * 1000), "o": float(r["open"]),
                    "h": float(r["high"]), "l": float(r["low"]), "c": float(r["close"])}
                   for ts, r in chart_df.iterrows()]
        return jsonify({"ok": True, "candles": candles, "source": chart_source})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})


@app.route("/api/version")
def api_version():
    return jsonify({"version": "v26.8"})


@app.route("/")
def index():
    return render_template("landing.html",
                           plan_price=config.PLAN_PRICE_DISPLAY,
                           plan_price_yearly=config.PLAN_PRICE_DISPLAY_YEARLY,
                           plan_price_daily=config.PLAN_PRICE_DISPLAY_DAILY,
                           discord_url=config.DISCORD_INVITE_URL)


@app.route("/app")
@subscriber_required
def app_page():
    return render_template("app.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


# --------------------------------------------------------------------------
# Public call record (v26) — every sealed call, graded at settlement.
# Public: the record is the trust asset, so no login/paywall here.
# --------------------------------------------------------------------------
@app.route("/record")
def record_page():
    calls = call_record.load_calls()
    summary = call_record.record_summary(calls)
    envelope = call_record.export_envelope()
    return render_template("record.html", summary=summary,
                           calls=list(reversed(calls[-40:])),
                           envelope=envelope,
                           public_key=call_record.public_key_b64(),
                           archived_public_key=call_record.archive_public_key())


@app.route("/api/record/export.jsonl")
def record_export_jsonl():
    # ONE JSONL: all rows, oldest first. The archived (pre-rotation)
    # rows come first because they are the oldest; the live rows
    # follow. Each part hashes to its own digest in export.json — the
    # stable key's signature covers the live part only, never the
    # archived rows.
    calls = call_record.load_calls()
    archived = call_record.load_archived_calls(calls)
    live = call_record.load_live_calls(calls)
    body = (call_record.canonical_jsonl(archived)
            + call_record.canonical_jsonl(live)).decode("utf-8")
    return Response(body, mimetype="application/x-ndjson",
                    headers={"Content-Disposition": "attachment; filename=threaded_record.jsonl"})


@app.route("/api/record/export.json")
def record_export_json():
    return jsonify(call_record.export_envelope())


@app.route("/api/record/public-key")
def record_pubkey():
    pk = call_record.public_key_b64()
    if not pk:
        return jsonify({"ok": False, "error": "signing not configured"}), 404
    return Response(pk, mimetype="text/plain")


@app.route("/api/record/summary")
def record_summary_api():
    return jsonify(call_record.record_summary())


@app.route("/api/discord/teaser")
def discord_teaser():
    secret = request.args.get("secret", "")
    if not config.TEASER_SECRET or config.TEASER_SECRET == "change-me" or secret != config.TEASER_SECRET:
        return jsonify({"ok": False, "error": "bad secret"}), 403
    from discord_integration import send_teaser
    try:
        state = compute_state()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": send_teaser(state)})


@app.after_request
def _no_cache_html(resp):
    # The HTML shell must never be cached: it carries the ?v= asset URLs,
    # so a stale shell is the #1 cause of "my changes didn't show up".
    if request.path == "/" or request.path.endswith(".html"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp


if __name__ == "__main__":
    import os
    # On a host (Render etc.) PORT is set -> bind all interfaces.
    # Locally it isn't -> loopback only, same as before.
    port = int(os.environ.get("PORT", "5000"))
    host = "0.0.0.0" if "PORT" in os.environ else "127.0.0.1"
    print(f"Threaded v26.8 - serving on http://{host}:{port}")
    app.run(host=host, port=port, threaded=True)
