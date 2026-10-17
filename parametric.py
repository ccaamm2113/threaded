"""Parametric 15-minute target-probability vote (merged from the standalone
15m engine, fixed for Threaded).

The standalone engine's idea was sound — estimate P(BTC finishes above a level)
from a normal model of remaining volatility — but it had three flaws:
  1. it measured vs the 15m candle OPEN, while Kalshi KXBTC15M settles vs the
     contract TARGET;
  2. it pulled klines from Binance, which is geo-blocked from US servers;
  3. it never compared against the actual Kalshi ask (no real edge).

This module keeps the parametric idea and fixes all three: it prices vs the
real Kalshi target using Threaded's own 1-minute candle volatility, and it is
pure arithmetic (microseconds) so it doubles as a nearly-free extra vote.

It also provides a cached Fear & Greed sentiment read for display context.
"""
import math
import time

import numpy as np
import requests

UA = {"User-Agent": "Threaded/1.0 (+https://threaded-btc-analyzer.onrender.com)"}


def normal_cdf(x):
    """Hart-style approximation of the standard normal CDF."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.5
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    d = 0.3989423 * math.exp((-x * x) / 2.0)
    p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))))
    return 1.0 - p if x > 0 else p


def parametric_up_prob(current, target, minutes_left, closes):
    """P(close >= target at expiry) under a zero-drift normal model.

    sigma = std of recent 1-minute simple returns; remaining std scales with
    sqrt(minutes_left). z = (current - target) / (current * sigma_rem);
    P(above) = Phi(z). Returns None when there isn't enough data.
    """
    try:
        c = np.asarray(closes, dtype=float)
        if len(c) < 25 or not current or not target or current <= 0 or target <= 0:
            return None
        rets = np.diff(c) / c[:-1]
        rets = rets[np.isfinite(rets)][-60:]
        if len(rets) < 20:
            return None
        sigma = float(np.std(rets))
        h = max(float(minutes_left), 0.25)
        sigma_rem = sigma * math.sqrt(h)
        if sigma_rem <= 0 or not np.isfinite(sigma_rem):
            return 1.0 if current >= target else 0.0
        z = (float(current) - float(target)) / (float(current) * sigma_rem)
        z = max(-6.0, min(6.0, z))
        p = float(normal_cdf(z))
        # Shrink 30% toward 0.5: tail estimates from a 60-sample vol window
        # claim false precision (99.9%). Keeps decisive votes decisive
        # without letting the crude model shout.
        return 0.5 + (p - 0.5) * 0.7
    except Exception:
        return None


# --------------------------------------------------------------------------
# Fear & Greed sentiment (display context only — not part of scoring)
# --------------------------------------------------------------------------
_sent = {"ts": 0.0, "data": None}
_SENT_TTL = 600


def get_sentiment():
    now = time.time()
    if _sent["data"] is not None and now - _sent["ts"] < _SENT_TTL:
        return _sent["data"]
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=6, headers=UA)
        r.raise_for_status()
        d = (r.json().get("data") or [{}])[0]
        out = {"score": int(d.get("value", 50)),
               "label": str(d.get("value_classification", "Neutral"))}
    except Exception:
        out = _sent["data"]  # stale or None
    else:
        _sent.update(ts=now, data=out)
    return out
