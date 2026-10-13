"""Multi-timeframe trend reader for Threaded.

The 1-minute model can't see the bigger picture, and 15-minute binaries live
or die by the higher-timeframe trend. This module reads 5m, 15m, 1H and 4H
BTC candles (Kraken native OHLC) and scores each timeframe from three
sub-signals: EMA-20 position, RSI-14 regime, MACD histogram sign.

Each TF scores in [-1, 1] (+ = bullish). The weighted vote becomes a small,
shrunk probability input: p_up = 0.5 + 0.30 * vote (never more extreme than
80/20 on its own).

Free, keyless, cached 60s per timeframe, fetched in parallel. Any TF that
fails is skipped; if none load, the vote is None (neutral).
"""
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

UA = {"User-Agent": "Threaded/1.0 (+https://threaded-btc-analyzer.onrender.com)"}

TF_MINUTES = {"5m": 5, "15m": 15, "1H": 60, "4H": 240}
TF_WEIGHTS = {"5m": 0.15, "15m": 0.25, "1H": 0.30, "4H": 0.30}

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


def _fetch_tf(tf):
    def _dl():
        r = requests.get("https://api.kraken.com/0/public/OHLC",
                         params={"pair": "XBTUSD", "interval": TF_MINUTES[tf]},
                         timeout=12, headers=UA)
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
        df = pd.DataFrame(recs).set_index("t").sort_index()
        return df if len(df) >= 40 else None
    return _cached(("htf", tf), 60, _dl)


def _rsi(close, n=14):
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = g / l.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def tf_score(df):
    """Score one timeframe's trend in [-1, 1] from EMA20, RSI14, MACD hist."""
    try:
        close = df["close"].astype(float)
        if len(close) < 40:
            return None
        ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
        last = float(close.iloc[-1])
        ema_s = max(-1.0, min(1.0, (last / ema20 - 1) / 0.02)) if ema20 else 0.0
        r = float(_rsi(close).iloc[-1])
        rsi_s = max(-1.0, min(1.0, (r - 50) / 15.0)) if np.isfinite(r) else 0.0
        macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
        hist = float((macd - macd.ewm(span=9, adjust=False).mean()).iloc[-1])
        hist_s = 1.0 if hist > 0 else (-1.0 if hist < 0 else 0.0)
        return float(np.mean([ema_s, rsi_s, hist_s])), {
            "ema20": round(ema_s, 2), "rsi": round(rsi_s, 2),
            "macd": int(hist_s), "rsi_val": round(r, 1)}
    except Exception:
        return None


def htf_vote():
    """Return {'p_up', 'tfs': {tf: score}, 'detail': {...}, 'n'} or None."""
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_fetch_tf, tf): tf for tf in TF_MINUTES}
        dfs = {}
        for fut, tf in futs.items():
            try:
                df = fut.result(timeout=14)
            except Exception:
                df = None
            if df is not None:
                dfs[tf] = df
    scores, details = {}, {}
    for tf, df in dfs.items():
        res = tf_score(df)
        if res is None:
            continue
        s, d = res
        scores[tf] = round(s, 3)
        details[tf] = d
    if not scores:
        return None
    ws = sum(TF_WEIGHTS[tf] for tf in scores)
    vote = sum(scores[tf] * TF_WEIGHTS[tf] for tf in scores) / ws
    p_up = 0.5 + 0.30 * max(-1.0, min(1.0, vote))
    return {"p_up": round(p_up, 4), "tfs": scores, "details": details, "n": len(scores)}
