"""Engine B — second prediction engine for Threaded.

Structurally different from Engine A (price-action ML + microstructure flow):
  - Macro regime ..... SPY trend, DXY trend, VIX level (risk-on/off for BTC)
  - Positioning ....... Hyperliquid open-interest change + funding (contrarian)
  - Kalshi tape ....... dollar-weighted taker-side imbalance on the contract
                        itself — real money, not resting quotes
  - Sentiment ......... Fear & Greed index (contrarian)

Each sub-vote is in [-1, 1] (+ = bullish BTC). Combined:
    p_up = 0.5 + 0.35 * weighted_mean(available votes)
so a lone engine can never claim more than 65%/35% on its own.

All sources are free and keyless, individually cached, and every fetcher
degrades to None (neutral) on failure. Nothing here raises.
"""
import time

import numpy as np
import requests
import yfinance as yf

UA = {"User-Agent": "Threaded/1.0 (+https://threaded-btc-analyzer.onrender.com)"}
KALSHI_TRADES = "https://api.elections.kalshi.com/trade-api/v2/markets/trades"

_cache = {}


def _cached(key, ttl, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception:
        return hit[1] if hit else None
    _cache[key] = (now, val)
    return val


def _clip(x, lo=-1.0, hi=1.0):
    try:
        return max(lo, min(hi, float(x)))
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------
# Macro regime (Yahoo daily bars, cached 5 min)
# --------------------------------------------------------------------------
def _daily(ticker):
    def _dl():
        df = yf.download(ticker, period="1mo", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or len(df) < 21:
            return None
        close = df["Close"].iloc[:, 0] if hasattr(df["Close"], "iloc") else df["Close"]
        return close.astype(float)
    return _cached(("macro", ticker), 300, _dl)


def macro_vote():
    """Risk-on/off for BTC: SPY trend (+), DXY trend (−, inverse), VIX (−)."""
    try:
        spy = _daily("SPY")
        dxy = _daily("DX-Y.NYB")
        vix = _daily("^VIX")
        parts = {}
        if spy is not None and len(spy) >= 21:
            parts["spy"] = _clip((float(spy.iloc[-1] / spy.tail(20).mean()) - 1) / 0.05)
        if dxy is not None and len(dxy) >= 21:
            parts["dxy"] = -_clip((float(dxy.iloc[-1] / dxy.tail(20).mean()) - 1) / 0.03)
        if vix is not None and len(vix) >= 5:
            lvl = float(vix.iloc[-1])
            parts["vix"] = -_clip((lvl - 15.0) / 25.0, 0.0, 1.0)
        if not parts:
            return None
        w = {"spy": 0.5, "dxy": 0.3, "vix": 0.2}
        ws = sum(w[k] for k in parts)
        return sum(parts[k] * w[k] for k in parts) / ws, parts
    except Exception:
        return None


# --------------------------------------------------------------------------
# Positioning (contrarian): OI change + funding, from Engine A's flow dict
# --------------------------------------------------------------------------
def positioning_vote(flow, r15_pct):
    try:
        flow = flow or {}
        parts = {}
        oi = flow.get("oi_chg_15m")
        if oi is not None and r15_pct is not None and r15_pct != 0:
            # leverage building *with* the move = crowding = fade it
            parts["oi"] = -np.sign(r15_pct) * _clip(oi / 0.03)
        fa = flow.get("funding_ann")
        if fa is not None:
            parts["funding"] = -_clip(fa / 0.50)
        if not parts:
            return None
        w = {"oi": 0.6, "funding": 0.4}
        ws = sum(w[k] for k in parts)
        return sum(parts[k] * w[k] for k in parts) / ws, parts
    except Exception:
        return None


# --------------------------------------------------------------------------
# Kalshi tape: dollar-weighted taker-side imbalance on this contract
# --------------------------------------------------------------------------
def _tape(ticker):
    def _fetch():
        r = requests.get(KALSHI_TRADES, params={"ticker": ticker, "limit": 40},
                         timeout=8, headers=UA)
        r.raise_for_status()
        return r.json().get("trades") or []
    return _cached(("tape", ticker), 15, _fetch)


def tape_vote(ticker):
    """+1 = takers aggressively buying YES; −1 = buying NO (real money)."""
    try:
        if not ticker:
            return None
        trades = _tape(ticker)
        if not trades:
            return None
        yes_d = no_d = 0.0
        for t in trades:
            try:
                n = float(t.get("count_fp") or 0)
            except (TypeError, ValueError):
                continue
            side = (t.get("taker_side") or "").lower()
            if side == "yes":
                try:
                    yes_d += n * float(t.get("yes_price_dollars") or 0)
                except (TypeError, ValueError):
                    pass
            elif side == "no":
                try:
                    no_d += n * float(t.get("no_price_dollars") or 0)
                except (TypeError, ValueError):
                    pass
        tot = yes_d + no_d
        if tot <= 0:
            return None
        return _clip((yes_d - no_d) / tot), {"yes_d": round(yes_d, 1), "no_d": round(no_d, 1),
                                             "n": len(trades)}
    except Exception:
        return None


# --------------------------------------------------------------------------
# Sentiment (contrarian Fear & Greed)
# --------------------------------------------------------------------------
def sentiment_vote():
    try:
        from parametric import get_sentiment
        s = get_sentiment()
        if not s or s.get("score") is None:
            return None
        # extreme fear (0) -> bullish +1; extreme greed (100) -> bearish −1
        return _clip((50 - float(s["score"])) / 50.0), {"fg": s["score"], "label": s.get("label")}
    except Exception:
        return None


# --------------------------------------------------------------------------
# Combined Engine B vote
# --------------------------------------------------------------------------
WEIGHTS = {"tape": 0.40, "macro": 0.30, "positioning": 0.20, "sentiment": 0.10}


def engine_b_vote(ticker, flow=None, r15_pct=None):
    """Return {'p_up', 'votes', 'details', 'n'} or None when nothing is available."""
    votes, details = {}, {}
    for name, fn in (("macro", macro_vote),
                     ("positioning", lambda: positioning_vote(flow, r15_pct)),
                     ("tape", lambda: tape_vote(ticker)),
                     ("sentiment", sentiment_vote)):
        try:
            res = fn()
        except Exception:
            res = None
        if res is None:
            continue
        val, det = res
        votes[name] = float(val)
        details[name] = det
    if not votes:
        return None
    ws = sum(WEIGHTS[k] for k in votes)
    raw = sum(votes[k] * WEIGHTS[k] for k in votes) / ws
    p_up = 0.5 + 0.35 * _clip(raw)
    return {"p_up": round(p_up, 4),
            "votes": {k: round(v, 3) for k, v in votes.items()},
            "details": details,
            "n": len(votes)}
