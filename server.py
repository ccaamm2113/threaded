"""BTC 15-Minute Kalshi Analyzer — standalone Flask website (no Streamlit).

Run:  python server.py        ->  http://127.0.0.1:5000
Logic ported faithfully from the Streamlit app (~/workspace/btc_kalshi_realtime/app.py).
"""
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
from flask import Flask, jsonify, request, redirect, render_template, send_from_directory
from sklearn.calibration import CalibratedClassifierCV
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import config
from models import db, User
from flow_signals import get_flow_signals, flow_score_direction_relative
from ai_reader import get_ai_read, configured as ai_configured
from parametric import parametric_up_prob, get_sentiment
from engine_b import engine_b_vote

BASE_DIR = Path(__file__).parent
KALSHI = "https://external-api.kalshi.com/trade-api/v2"

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["SQLALCHEMY_DATABASE_URI"] = config.DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

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


# Per-ticker signal memory for the entry/exit logic:
#   ticker -> {"leans": [last up to 3 signal classes], "peak_edge": float|None, "peak_side": str|None}
_signal_mem: dict = {}


def signal_memory(ticker):
    # Cap memory so rotating 15-min tickers can't grow this dict forever.
    if len(_signal_mem) > 60:
        for k in list(_signal_mem)[: len(_signal_mem) - 60]:
            del _signal_mem[k]
    mem = _signal_mem.get(ticker)
    if mem is None:
        mem = {"leans": [], "peak_edge": None, "peak_side": None}
        _signal_mem[ticker] = mem
    return mem


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
        df["taker_buy_base"] = np.nan
        if len(df) < 100:
            raise ValueError(f"Coinbase returned only {len(df)} candles")
        return df[["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base"]].tail(limit), "Coinbase", errors
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
        df["taker_buy_base"] = np.nan
        if len(df) < 100:
            raise ValueError(f"Kraken returned only {len(df)} candles")
        return df[["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base"]].tail(limit), "Kraken (fallback)", errors
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


def get_yahoo_returns(ticker, period="5d", interval="1m"):
    def _fetch():
        # Bound each download so a blocked/slow Yahoo can't stall /api/state
        # for minutes; on timeout we fall back to an empty series (same as the
        # Streamlit app's except path).
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
        def _dl():
            return yf.download(ticker, period=period, interval=interval, progress=False,
                               auto_adjust=True, threads=False)
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                data = ex.submit(_dl).result(timeout=15)
        except (FuturesTimeout, Exception):
            return pd.Series(dtype=float, name=ticker)
        if data is None or data.empty:
            return pd.Series(dtype=float, name=ticker)
        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close.index = pd.to_datetime(close.index, utc=True)
        out = close.pct_change()
        out.name = ticker
        return out
    return cached(("yahoo", ticker, period, interval), 90, _fetch)


# --------------------------------------------------------------------------
# Features (23 + cross-asset proxies)
# --------------------------------------------------------------------------
def rsi(series, n=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = -delta.clip(upper=0).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd_line(series, fast=12, slow=26):
    return series.ewm(span=fast, adjust=False).mean() - series.ewm(span=slow, adjust=False).mean()


def build_features(df):
    x = pd.DataFrame(index=df.index)
    close = df["close"].astype(float)
    ret = close.pct_change()
    x["ret_1m"] = ret
    x["ret_3m"] = close.pct_change(3)
    x["ret_5m"] = close.pct_change(5)
    x["ret_15m"] = close.pct_change(15)
    x["volatility_5m"] = ret.rolling(5).std()
    x["volatility_15m"] = ret.rolling(15).std()
    x["volume_change"] = df["volume"].pct_change().replace([np.inf, -np.inf], np.nan)
    x["volume_z_30m"] = (df["volume"] - df["volume"].rolling(30).mean()) / df["volume"].rolling(30).std()
    x["distance_sma_5"] = close / close.rolling(5).mean() - 1
    x["distance_sma_15"] = close / close.rolling(15).mean() - 1
    x["distance_sma_60"] = close / close.rolling(60).mean() - 1
    x["range_5m"] = (df["high"].rolling(5).max() - df["low"].rolling(5).min()) / close
    x["taker_buy_share"] = df["taker_buy_base"] / df["volume"].replace(0, np.nan)
    x["rsi_14"] = rsi(close, 14)
    x["rsi_7"] = rsi(close, 7)
    m = macd_line(close)
    x["macd"] = m
    x["macd_signal"] = m.ewm(span=9, adjust=False).mean()
    x["macd_hist"] = x["macd"] - x["macd_signal"]
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    x["bollinger_pctb"] = (close - (sma20 - 2 * std20)) / (4 * std20.replace(0, np.nan))
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - close.shift()).abs(),
                    (df["low"] - close.shift()).abs()], axis=1).max(axis=1)
    x["atr_14"] = tr.rolling(14).mean() / close
    x["ema12_dist"] = close / close.ewm(span=12, adjust=False).mean() - 1
    x["ema26_dist"] = close / close.ewm(span=26, adjust=False).mean() - 1
    x["accel_3v5"] = x["ret_3m"] - x["ret_5m"] * 0.6
    return x.replace([np.inf, -np.inf], np.nan)


def fit_predict(btc, n):
    features = build_features(btc)
    # Cross-asset proxies fetched in parallel; each is individually cached and
    # time-bounded inside get_yahoo_returns, so a slow Yahoo can't stall us.
    from concurrent.futures import ThreadPoolExecutor
    proxy_tickers = ["ETH-USD", "SPY", "QQQ"]
    proxies = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(get_yahoo_returns, t): t for t in proxy_tickers}
        for fut, ticker in futs.items():
            try:
                s = fut.result()
                if not s.empty:
                    proxies[ticker.lower().replace("-", "_")] = s
            except Exception:
                pass
    for name, s in proxies.items():
        aligned = s.reindex(features.index, method="ffill")
        features[f"{name}_ret_5m"] = aligned.rolling(5).sum()
    future_return = btc["close"].shift(-15) / btc["close"] - 1
    target = (future_return > 0).astype(float)
    target[future_return.isna()] = np.nan
    valid = features.notna().sum(axis=1) >= max(5, int(features.shape[1] * 0.5))
    train_mask = valid & target.notna()
    X_train = features.loc[train_mask].tail(n)
    y_train = target.loc[X_train.index].astype(int)
    latest = features.iloc[[-1]]
    if len(X_train) < 100 or y_train.nunique() < 2:
        raise ValueError("Not enough usable training rows or only one outcome class. Try again later.")
    base = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("model", LogisticRegression(max_iter=2000, class_weight="balanced", C=0.8)),
    ])
    model = CalibratedClassifierCV(base, method="sigmoid", cv=3)
    model.fit(X_train, y_train)
    if latest.notna().sum(axis=1).iloc[0] < max(5, int(features.shape[1] * 0.5)):
        raise ValueError("Latest candle does not have enough usable features.")
    p_up = float(model.predict_proba(latest)[0][list(model.classes_).index(1)])
    split = int(len(X_train) * 0.8)
    accuracy = float("nan")
    brier = float("nan")
    if split >= 80 and len(X_train) - split >= 20:
        eval_base = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(max_iter=2000, class_weight="balanced", C=0.8)),
        ])
        eval_model = CalibratedClassifierCV(eval_base, method="sigmoid", cv=3)
        eval_model.fit(X_train.iloc[:split], y_train.iloc[:split])
        probs = eval_model.predict_proba(X_train.iloc[split:])[:, list(eval_model.classes_).index(1)]
        preds = (probs >= 0.5).astype(int)
        accuracy = float((preds == y_train.iloc[split:].to_numpy()).mean())
        brier = float(np.mean((probs - y_train.iloc[split:].to_numpy()) ** 2))
    return p_up, accuracy, brier, len(X_train)


# --------------------------------------------------------------------------
# Model cache: fit_predict is ~17s and must NOT retrain on every 3s poll.
# Retrain only when a new 1-minute candle arrives (or every 10 min safety).
# --------------------------------------------------------------------------
_model_cache = {"ts": 0.0, "key": None, "result": None}
_model_lock = threading.Lock()
MODEL_TTL = 600


def fit_predict_cached(btc, n):
    key = (n, btc.index[-1])
    now = time.time()
    with _model_lock:
        hit = _model_cache
        if hit["key"] == key and hit["result"] is not None and now - hit["ts"] < MODEL_TTL:
            return hit["result"]
    result = fit_predict(btc, n)
    with _model_lock:
        _model_cache.update(ts=now, key=key, result=result)
    return result

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
GIRL_FOR_CLS = {"up": "img/yuki_bullish.webp",
                "down": "img/yuki_bearish.webp",
                "wait": "img/yuki_chill.webp"}


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


def compute_direction(blended_up, p_up, target_p, momentum_up, momentum_down,
                      r3, r5, up_edge, down_edge, minutes_left, chop, w_model, w_hist,
                      flow=None, ai=None, param_p=None, b_vote=None):
    """Tiered direction check with conviction scoring.

    conviction in [-1, 1] = 0.45*probability + 0.20*momentum + 0.20*edge
                          + 0.15*extra, where extra blends microstructure flow
                          and the AI reader vote (whichever is available).
    Tiers: |conv| >= 0.55 -> STRONG LEAN, >= 0.28 (0.45 when choppy) -> LEAN, else WAIT.
    Hard vetos: negative edge vs the ask, <30s to expiry. Model/history
    disagreement caps conviction at LEAN level; strong AI disagreement does too.
    `flow` is a dict from flow_signals.get_flow_signals (may be None).
    `ai` is a dict from ai_reader.get_ai_read (may be None).
    Returns (label, cls, tier, reason, min_edge, conviction, components).
    """
    min_edge = 8.0 if minutes_left < 3 else (5.0 if minutes_left < 8 else 3.0)
    comp = {
        "model_p_up": float(p_up), "target_p": float(target_p),
        "w_model": float(w_model), "w_hist": float(w_hist),
        "momentum": "up" if momentum_up else ("down" if momentum_down else "mixed"),
        "r3": float(r3), "r5": float(r5),
        "chop_ratio": float(chop), "choppy": bool(chop > 2.0),
        "min_edge": float(min_edge), "disagree": False,
    }
    # surfaced before ANY veto return so the Why panel stays complete
    comp["param_p"] = float(param_p) if param_p is not None else None
    b_p = (b_vote or {}).get("p_up")
    comp["engine_b_p"] = float(b_p) if b_p is not None else None
    if minutes_left < 0.5:
        return ("WAIT", "wait", "wait",
                "Less than 30 seconds remain; settlement noise dominates.",
                min_edge, 0.0, comp)

    direction = 1 if blended_up >= 0.5 else -1
    # all component scores are direction-relative: positive = supports the lean
    prob_align = max(0.0, min(1.0, abs(blended_up - 0.5) * 2 / 0.6))
    mom = 1.0 if momentum_up else (-1.0 if momentum_down else 0.0)
    mom_align = mom * direction
    edge_here = up_edge if direction > 0 else down_edge
    edge_align = max(-1.0, min(1.0, edge_here / (2 * min_edge))) if edge_here is not None else 0.0
    comp["edge_cents"] = float(edge_here) if edge_here is not None else None
    comp["prob_score"] = float(prob_align)
    comp["mom_score"] = float(mom_align)
    comp["edge_score"] = float(edge_align)

    # --- extra vote: microstructure flow + AI reader (direction-relative) ---
    extra_parts = {}
    flow_score, flow_parts = (0.0, {})
    if flow:
        flow_score, flow_parts = flow_score_direction_relative(flow, direction)
        if flow_parts:
            extra_parts["flow"] = float(flow_score)
    comp["flow_score"] = float(flow_score) if flow_parts else None
    # raw (not direction-relative) parts for honest display in the Why panel
    raw_parts = {}
    for k in ("book_imb", "trade_flow", "kalshi_book_imb"):
        if flow and flow.get(k) is not None:
            raw_parts[{"book_imb": "book", "trade_flow": "flow",
                       "kalshi_book_imb": "kbook"}[k]] = float(flow[k])
    comp["flow_parts"] = raw_parts or None
    comp["funding_ann"] = _f((flow or {}).get("funding_ann"))
    comp["oi_chg_15m"] = _f((flow or {}).get("oi_chg_15m"))

    ai_align = 0.0
    ai_vote = None
    if ai and ai.get("p_up") is not None:
        ai_vote = float(ai["p_up"])
        raw = max(-1.0, min(1.0, (ai_vote - 0.5) * 2 / 0.6))
        ai_align = raw * direction * (0.4 + 0.6 * float(ai.get("confidence", 0.5)))
        extra_parts["ai"] = float(ai_align)
    comp["ai_p_up"] = ai_vote
    comp["ai_confidence"] = _f(ai.get("confidence")) if ai else None
    comp["ai_align"] = float(ai_align) if ai_vote is not None else None

    extra = sum(extra_parts.values()) / len(extra_parts) if extra_parts else 0.0
    comp["extra_score"] = float(extra) if extra_parts else None

    # hard veto: never lean into a negative edge vs the Kalshi ask
    if edge_here is not None and edge_here < 0:
        reason = (f"Negative edge ({edge_here:+.1f}c) vs the Kalshi ask - no lean "
                  f"even though probability is {blended_up:.0%}.")
        return ("WAIT / NO CLEAR EDGE", "wait", "wait", reason, min_edge, 0.0, comp)

    conviction = max(0.0, 0.45 * prob_align + 0.20 * mom_align + 0.20 * edge_align
                       + 0.15 * extra)

    # model/history disagreement caps conviction at LEAN level
    disagree = (p_up - 0.5) * (target_p - 0.5) < 0 and abs(p_up - target_p) > 0.10
    comp["disagree"] = bool(disagree)
    cap_note = ""
    if disagree:
        conviction = min(0.45, conviction)
        cap_note = " Model and history disagree - sized down from STRONG."

    # strong AI disagreement with the quant lean also caps at LEAN
    ai_disagree = (ai_vote is not None and (ai_vote - 0.5) * direction < 0
                   and abs(ai_vote - 0.5) > 0.12)
    comp["ai_disagree"] = bool(ai_disagree)
    if ai_disagree:
        conviction = min(0.45, conviction)
        cap_note += " AI reader disagrees with the quant lean - sized down from STRONG."

    # Engine B agreement: independent second opinion. Agreement adds a small
    # bonus; disagreement is noted but never overrides Engine A.
    agree_b = b_p is not None and (b_p - 0.5) * direction > 0 and abs(b_p - 0.5) > 0.06
    disagree_b = b_p is not None and (b_p - 0.5) * direction < 0 and abs(b_p - 0.5) > 0.10
    comp["engines_agree"] = bool(agree_b)
    comp["engine_b_disagree"] = bool(disagree_b)
    if agree_b:
        conviction = min(1.0, conviction + 0.05)
        cap_note += " Engine B agrees (+0.05)."
    if disagree_b:
        cap_note += " Engine B disagrees with the quant lean."

    # chop filter: demand stronger conviction when the market is whipping
    need = 0.45 if chop > 2.0 else 0.28
    comp["need"] = float(need)
    conviction = float(conviction)
    signed = direction * conviction  # signed for display: + favors up, - favors down

    side_word = "UP" if direction > 0 else "DOWN"
    if conviction >= 0.55 and conviction >= need:
        tier = "strong"
        label = f"STRONG LEAN {side_word}"
    elif conviction >= need:
        tier = "lean"
        label = f"LEAN {side_word}"
    else:
        if chop > 2.0 and conviction >= 0.28:
            reason = (f"Market is choppy (5-min range {chop:.1f}x its hourly norm) - "
                      f"Yuki stands aside despite a {signed:+.2f} lean.")
        else:
            reason = (f"Conviction {signed:+.2f} is below the {need:.2f} bar - "
                      f"probability, momentum, or edge too weak for a lean.")
        return ("WAIT / NO CLEAR EDGE", "wait", "wait", reason + cap_note,
                min_edge, signed, comp)

    extras_txt = ""
    if comp.get("flow_score") is not None:
        extras_txt += f", flow {comp['flow_score']:+.2f}"
    if ai_vote is not None:
        extras_txt += f", AI {ai_vote:.0%} up"
    if param_p is not None:
        extras_txt += f", parametric {param_p:.0%}"
    reason = (f"Model {p_up:.0%} vs history {target_p:.0%} "
              f"(blend {w_model:.0%}/{w_hist:.0%}), momentum {comp['momentum']}, "
              f"edge {edge_here:+.1f}c vs {min_edge:.0f}c needed{extras_txt}, "
              f"conviction {signed:+.2f}.{cap_note}")
    cls = "up" if direction > 0 else "down"
    return (label, cls, tier, reason, min_edge, signed, comp)


def compute_scalp(blended_up, up_edge, down_edge, up_ask, up_bid, down_ask, down_bid,
                  r1, r3, r5, minutes_left, current, target, proj, chop,
                  flow=None, closes=None):
    """Fast Yes(up)/No(down) scalp scores, 0-100 each, plus rough 2-min stats.

    A scalp is a quick in-and-out: it wants a real edge vs the ask, momentum
    pointing the same way, room to reach the target, a tight spread, and
    enough time left that it isn't an expiry coin-flip. Microstructure flow
    (order-book / trade / Kalshi-book) now tilts the score, and each side
    reports rough 2-minute stats: win probability, expected gain/heat in
    contract cents, and EV = (2p-1)*gain - spread. Rough — research only.
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
        exp_gain_c = exp_heat_c = ev_c = None
        if sens_c_per_pct is not None and sigma_1m:
            fav_pct = 0.8 * sigma_1m * np.sqrt(2.0) * 100.0
            g = round(min(30.0, abs(sens_c_per_pct) * fav_pct), 1)
            exp_gain_c, exp_heat_c = g, g
            if spread_c is not None:
                ev_c = round((2 * p_2m - 1) * g - spread_c, 1)
        stats = {"p_2m": round(p_2m, 3), "spread_c": spread_c,
                 "exp_gain_c": exp_gain_c, "exp_heat_c": exp_heat_c, "ev_c": ev_c}
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


def compute_entry_exit(ticker, cls, blended_up, up_edge, down_edge, up_ask, down_ask,
                       up_bid, down_bid, momentum_up, momentum_down,
                       r3, r5, minutes_left, min_edge, mins_display,
                       proj=None, req_up_pct=None, req_down_pct=None):
    """Entry/exit checklists with the website improvements:
    1. confirmation across 2 consecutive polls (per-ticker memory)
    2. trailing peak-edge exit
    3. spread filter on entry
    4. adaptive probability bar (65% under 3 min, else 60%)
    5. entry strength meter: open / almost / no
    6. exit triggers incl. spike-projection-aware ones:
       cushion thinner than expected adverse spike -> target at risk;
       cushion > 2x expected adverse spike -> bank it
    """
    mem = signal_memory(ticker)
    mem["leans"] = (mem["leans"] + [cls])[-3:]

    side = "UP" if cls == "up" else ("DOWN" if cls == "down" else None)
    if side is None:
        return None, None

    edge_here = up_edge if side == "UP" else down_edge
    ask_here = up_ask if side == "UP" else down_ask
    bid_here = up_bid if side == "UP" else down_bid
    spread = (ask_here - bid_here) if (ask_here is not None and bid_here is not None) else None
    mom_ok = momentum_up if side == "UP" else momentum_down
    mom_flip = momentum_down if side == "UP" else momentum_up
    prob_side = blended_up if side == "UP" else (1 - blended_up)
    buy_label = "YES (UP)" if side == "UP" else "NO (DOWN)"

    # --- trailing peak edge (per ticker+side) ---
    if mem["peak_side"] != side:
        mem["peak_side"] = side
        mem["peak_edge"] = edge_here
    elif edge_here is not None and (mem["peak_edge"] is None or edge_here > mem["peak_edge"]):
        mem["peak_edge"] = edge_here
    peak_edge = mem["peak_edge"]

    # --- 1. confirmation: same lean on 2 consecutive polls ---
    confirmed = len(mem["leans"]) >= 2 and all(s == cls for s in mem["leans"][-2:])
    n_conf = sum(1 for s in mem["leans"][-2:] if s == cls)

    # --- 4. adaptive probability bar ---
    prob_bar = 0.65 if minutes_left < 3 else 0.60

    # --- spike projection context (rough) ---
    up_spike = (proj or {}).get("up_spike_pct")
    down_spike = (proj or {}).get("down_spike_pct")
    if side == "UP":
        req_pct, spike_pct = req_up_pct, up_spike
    else:
        req_pct, spike_pct = req_down_pct, down_spike
    spike_ok = bool(req_pct is not None and spike_pct is not None and req_pct <= spike_pct)
    spike_detail = (f"Need {req_pct:+.2f}% · spike ≈ {spike_pct:+.2f}%"
                    if req_pct is not None and spike_pct is not None else "Projection unavailable")

    entry_checks = [
        {"label": "Clear directional lean", "ok": True,
         "detail": f"Signal: LEAN {side}"},
        {"label": "Confirmed on 2 consecutive polls", "ok": confirmed,
         "detail": "Confirmed ✓" if confirmed else f"Confirming… ({n_conf}/2)"},
        {"label": "Blended probability beyond bar", "ok": prob_side >= prob_bar,
         "detail": f"{prob_side:.1%} vs {prob_bar:.0%} needed"},
        {"label": "Momentum agrees (3m & 5m)", "ok": mom_ok,
         "detail": f"3m {r3:+.2f}% · 5m {r5:+.2f}%"},
        {"label": "Edge vs ask ≥ threshold", "ok": (edge_here is not None and edge_here >= min_edge),
         "detail": (f"{edge_here:+.1f}¢ vs {min_edge:.0f}¢ needed" if edge_here is not None else "Ask unavailable")},
        {"label": "Tradeable: spread ≤ 10¢, ≥ 2 min left",
         "ok": ((spread is None or spread <= 10) and minutes_left >= 2.0),
         "detail": (f"Spread {spread:.0f}¢ · {mins_display} left" if spread is not None
                    else f"Spread n/a · {mins_display} left")},
        {"label": "Expected spike reaches the target", "ok": spike_ok, "detail": spike_detail},
    ]
    passed = sum(1 for c in entry_checks if c["ok"])
    total = len(entry_checks)
    if passed == total:
        entry_status = "open"
    elif passed >= 4:
        entry_status = "almost"
    else:
        entry_status = "no"
    missing = [c["label"] for c in entry_checks if not c["ok"]]

    entry = {"side": side, "buy_label": buy_label, "checks": entry_checks,
             "passed": passed, "total": total, "status": entry_status, "missing": missing}

    # --- 2/6. exit triggers ---
    faded = (peak_edge is not None and peak_edge >= min_edge
             and edge_here is not None and edge_here < peak_edge * 0.4)
    # --- spike-projection-aware exits: cushion vs expected adverse spike ---
    cushion_pct = (-req_pct) if req_pct is not None else None
    adverse_spike = down_spike if side == "UP" else up_spike
    thin_cushion = bool(cushion_pct is not None and adverse_spike is not None
                        and 0 < cushion_pct < adverse_spike)
    fat_cushion = bool(cushion_pct is not None and adverse_spike is not None
                       and cushion_pct >= 2 * adverse_spike > 0)
    exit_triggers = [
        {"label": "Edge evaporated (< 2¢)", "fired": (edge_here is not None and edge_here < 2.0),
         "detail": (f"Edge now {edge_here:+.1f}¢" if edge_here is not None else "Ask unavailable")},
        {"label": "Edge faded 60%+ from peak", "fired": faded,
         "detail": (f"Peak {peak_edge:+.1f}¢ → now {edge_here:+.1f}¢"
                    if faded and peak_edge is not None and edge_here is not None else "No fade")},
        {"label": "Momentum flipped against you", "fired": mom_flip,
         "detail": f"3m {r3:+.2f}% · 5m {r5:+.2f}%"},
        {"label": "Probability crossed back through 50%", "fired": prob_side < 0.50,
         "detail": f"Now {prob_side:.1%}"},
        {"label": "Under 60s left — settlement lottery", "fired": minutes_left < 1.0,
         "detail": f"{mins_display} left"},
        {"label": "Deep in profit (ask ≥ 93¢) — bank it", "fired": (ask_here is not None and ask_here >= 93),
         "detail": (f"Ask {ask_here:.0f}¢" if ask_here is not None else "Ask unavailable")},
        {"label": "Cushion thinner than expected adverse spike — target at risk", "fired": thin_cushion,
         "detail": (f"Cushion +{cushion_pct:.2f}% vs adverse spike ≈ {adverse_spike:.2f}%"
                    if thin_cushion else "Cushion covers normal wiggles")},
        {"label": "Cushion 2× expected adverse spike — consider banking", "fired": fat_cushion,
         "detail": (f"Cushion +{cushion_pct:.2f}% vs adverse spike ≈ {adverse_spike:.2f}%"
                    if fat_cushion else "No excess cushion")},
    ]
    fired_labels = [t["label"] for t in exit_triggers if t["fired"]]
    exit_ = {"triggers": exit_triggers, "fired_labels": fired_labels,
             "status": "exit" if fired_labels else "hold"}
    return entry, exit_


def _f(x):
    """JSON-safe float (None for NaN/non-finite)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None

# --------------------------------------------------------------------------
# Full dashboard state builder
# --------------------------------------------------------------------------
def compute_state(force_ticker="", target_override=0.0, history=750, selected_ticker=""):
    errors = []

    # --- BTC data + model ---
    btc, data_source, source_errors = get_market_klines_cached(limit=1000)
    btc = btc.dropna(subset=["close"])
    if len(btc) < 150:
        raise RuntimeError("The price feed returned too little data to train a model.")
    current = float(btc["close"].iloc[-1])
    start_time = btc.index[-1]
    p_up, accuracy, brier, n_train = fit_predict_cached(btc, history)
    p_down = 1 - p_up
    last_ret = float(btc["close"].pct_change().iloc[-1] * 100)

    close_series = btc["close"].astype(float)
    r1 = float(close_series.pct_change(1).iloc[-1] * 100)
    r3 = float((close_series.iloc[-1] / close_series.iloc[-4] - 1) * 100) if len(close_series) >= 4 else 0.0
    r5 = float((close_series.iloc[-1] / close_series.iloc[-6] - 1) * 100) if len(close_series) >= 6 else 0.0

    if p_up >= 0.60:
        model_lean, model_cls = "UPWARD LEAN", "up"
    elif p_up <= 0.40:
        model_lean, model_cls = "DOWNWARD LEAN", "down"
    else:
        model_lean, model_cls = "NO CLEAR EDGE", "wait"

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
        "model": {
            "p_up": p_up, "p_down": p_down,
            "accuracy": _f(accuracy), "brier": _f(brier), "n_train": n_train,
            "lean_label": model_lean, "lean_cls": model_cls,
        },
        "momentum": {"r1": r1, "r3": r3, "r5": r5},
        "markets": [],
        "market": None,
        "market_error": None,
        "signal": None,
        "entry": None,
        "exit": None,
        "scalp": None,
        "projection": None,
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
        # Model-only fallback signal (no Kalshi market available)
        state["signal"] = {
            "label": f"MODEL {model_lean}", "cls": model_cls,
            "girl": GIRL_FOR_CLS[model_cls], "blended_up": p_up,
            "reason": "No Kalshi market selected — showing the BTC model lean only.",
            "prob_bar": None, "min_edge": None,
        }
        if not state["market_error"]:
            state["market_error"] = ("No KXBTC15M market returned. Try again in a few seconds, "
                                     "or paste the exact ticker into the force-ticker field.")
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
                                 "Enter it in the target override field — the app will not guess.")
        state["signal"] = {
            "label": f"MODEL {model_lean}", "cls": model_cls,
            "girl": GIRL_FOR_CLS[model_cls], "blended_up": p_up,
            "reason": "Target unknown — showing the BTC model lean only.",
            "prob_bar": None, "min_edge": None,
        }
        return state

    # --- blended probability + direction signal ---
    target_p, horizon, sample_count = estimate_target_probability(btc, target, minutes_left)
    if target_p is None:
        state["market_error"] = "Not enough completed historical windows to estimate target probability yet."
        state["signal"] = {
            "label": f"MODEL {model_lean}", "cls": model_cls,
            "girl": GIRL_FOR_CLS[model_cls], "blended_up": p_up,
            "reason": "Historical estimate unavailable — showing the BTC model lean only.",
            "prob_bar": None, "min_edge": None,
        }
        return state

    # adaptive blend: the model is trained for a 15-min horizon, so as expiry
    # nears the horizon-matched historical frequency earns more weight
    w_hist = 0.40 + 0.35 * (1 - max(minutes_left, 0) / 15)
    w_model = 1 - w_hist
    blended_up = w_model * p_up + w_hist * target_p
    # parametric vote (merged standalone engine, fixed): zero-drift normal
    # model vs the REAL Kalshi target, from our own 1m candle volatility.
    # Cheap (microseconds) and complementary to the empirical history vote.
    param_p = parametric_up_prob(current, target, minutes_left, close_series)
    if param_p is not None:
        blended_up = 0.85 * blended_up + 0.15 * param_p
    up_edge = blended_up * 100 - up_ask if up_ask is not None else None
    down_edge = (1 - blended_up) * 100 - down_ask if down_ask is not None else None
    momentum_up = r3 > 0 and r5 > 0
    momentum_down = r3 < 0 and r5 < 0
    chop = chop_ratio(btc)

    # --- extra prediction sources: microstructure flow + AI reader ---
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
        "model_p_up": round(p_up, 3), "history_p_up": round(target_p, 3),
        "blended_p_up": round(blended_up, 3),
        "up_edge_c": round(up_edge, 1) if up_edge is not None else None,
        "down_edge_c": round(down_edge, 1) if down_edge is not None else None,
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

    # --- Engine B: independent second opinion (macro + positioning + tape) ---
    try:
        eng_b = engine_b_vote(ticker_selected, flow, (flow or {}).get("_r15_fallback"))
    except Exception:
        eng_b = None

    label, cls, tier, reason, min_edge, conviction, comp = compute_direction(
        blended_up, p_up, target_p, momentum_up, momentum_down,
        r3, r5, up_edge, down_edge, minutes_left, chop, w_model, w_hist,
        flow=flow, ai=ai, param_p=param_p, b_vote=eng_b)
    state["engine_b"] = eng_b
    state["flow"] = {k: _f(v) for k, v in (flow or {}).items() if not k.startswith("_")}
    state["ai"] = ai
    state["ai_enabled"] = ai_configured()
    state["sentiment"] = get_sentiment()
    prob_bar = 0.65 if minutes_left < 3 else 0.60
    state["signal"] = {
        "label": label, "cls": cls, "tier": tier, "girl": GIRL_FOR_CLS[cls],
        "blended_up": blended_up, "model_p_up": p_up, "target_p": target_p,
        "param_p": param_p,
        "horizon": horizon, "sample_count": sample_count,
        "reason": reason, "prob_bar": prob_bar, "min_edge": min_edge,
        "up_edge": up_edge, "down_edge": down_edge,
        "momentum_up": momentum_up, "momentum_down": momentum_down,
        "conviction": conviction, "w_model": w_model, "w_hist": w_hist,
        "components": comp,
    }

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

    # --- entry / exit ---
    entry, exit_ = compute_entry_exit(
        ticker_selected, cls, blended_up, up_edge, down_edge, up_ask, down_ask,
        up_bid, down_bid, momentum_up, momentum_down, r3, r5,
        minutes_left, min_edge, mins_display,
        proj=proj, req_up_pct=req_up_pct, req_down_pct=req_down_pct)
    state["entry"] = entry
    state["exit"] = exit_

    # --- scalp signals (fast up/down) ---
    state["scalp"] = compute_scalp(
        blended_up, up_edge, down_edge, up_ask, up_bid, down_ask, down_bid,
        r1, r3, r5, minutes_left, current, target, proj, chop,
        flow=flow, closes=close_series)
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
    return jsonify({"version": "v22"})


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
    print(f"Threaded v18 - serving on http://{host}:{port}")
    app.run(host=host, port=port, threaded=True)
