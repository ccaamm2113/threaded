"""Extra market-data sources for Threaded's prediction engine.

All sources are free, keyless and polled with short timeouts + per-source TTLs
so a slow vendor can never stall /api/state. Every fetcher returns None on any
failure; the signal engine treats missing as neutral.

Sources:
  - Coinbase L2 order book  -> bid/ask depth imbalance (near-term pressure)
  - Coinbase recent trades  -> taker buy/sell flow imbalance
  - Hyperliquid perps       -> BTC funding rate (crowding, contrarian) + open
                               interest (leverage build-up)
  - Kalshi contract book    -> resting YES vs NO depth on the actual contract
"""
import time
from concurrent.futures import ThreadPoolExecutor

import requests

UA = {"User-Agent": "Threaded/1.0 (+https://threaded-btc-analyzer.onrender.com)"}

_cache = {}


def _cached(key, ttl, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception:
        # serve stale on error rather than dropping the signal entirely
        return hit[1] if hit else None
    _cache[key] = (now, val)
    return val


def _get(url, timeout=5, **kw):
    r = requests.get(url, timeout=timeout, headers=UA, **kw)
    r.raise_for_status()
    return r.json()


def _post(url, payload, timeout=10):
    r = requests.post(url, json=payload, timeout=timeout, headers=UA)
    r.raise_for_status()
    return r.json()


# --------------------------------------------------------------------------
# 1. Coinbase L2 order book imbalance (top 10 levels, dollar-weighted)
# --------------------------------------------------------------------------
def _book_imbalance():
    book = _get("https://api.exchange.coinbase.com/products/BTC-USD/book?level=2")
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return None
    mid = (float(bids[0][0]) + float(asks[0][0])) / 2
    band = mid * 0.001  # only depth within ~10 bps of touch counts

    def depth(side):
        tot = 0.0
        for px_s, sz_s, *_ in side[:10]:
            px, sz = float(px_s), float(sz_s)
            if abs(px - mid) <= band:
                tot += px * sz
        return tot

    b, a = depth(bids), depth(asks)
    if b + a <= 0:
        return None
    return (b - a) / (b + a)  # [-1, 1]; + = bid-heavy = upward pressure


def book_imbalance():
    return _cached("book_imb", 8, _book_imbalance)


# --------------------------------------------------------------------------
# 2. Coinbase recent-trades taker flow (last ~100 prints)
# --------------------------------------------------------------------------
def _trade_flow():
    trades = _get("https://api.exchange.coinbase.com/products/BTC-USD/trades")
    if not trades:
        return None
    buys = sells = 0.0
    for t in trades[:100]:
        try:
            sz = float(t.get("size") or 0)
        except (TypeError, ValueError):
            continue
        side = (t.get("side") or "").lower()
        if side in ("s", "sell"):
            sells += sz
        elif side in ("b", "buy"):
            buys += sz
    tot = buys + sells
    if tot <= 0:
        return None
    return (buys - sells) / tot  # [-1, 1]; + = buyers lifting offers


def trade_flow():
    return _cached("trade_flow", 8, _trade_flow)


# --------------------------------------------------------------------------
# 3. Hyperliquid BTC perps: funding (annualized) + open interest
# --------------------------------------------------------------------------
_oi_hist = []  # (ts, oi_btc)


def _hl_derivs():
    d = _post("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
    uni, ctxs = d[0]["universe"], d[1]
    i = next(i for i, u in enumerate(uni) if u.get("name") == "BTC")
    c = ctxs[i]
    funding_hr = float(c.get("funding") or 0.0)          # per-hour rate
    oi = float(c.get("openInterest") or 0.0)             # BTC
    funding_ann = funding_hr * 24 * 365
    now = time.time()
    _oi_hist.append((now, oi))
    while _oi_hist and now - _oi_hist[0][0] > 3600:
        _oi_hist.pop(0)
    oi_chg_15m = None
    for ts, old_oi in reversed(_oi_hist):
        if now - ts >= 900:
            oi_chg_15m = (oi - old_oi) / old_oi if old_oi else 0.0
            break
    return {"funding_ann": funding_ann, "oi_btc": oi, "oi_chg_15m": oi_chg_15m}


def hl_derivs():
    return _cached("hl_derivs", 45, _hl_derivs)


# --------------------------------------------------------------------------
# 4. Kalshi contract order book: resting YES vs NO depth
# --------------------------------------------------------------------------
def _kalshi_book(ticker):
    d = _get(f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}/orderbook",
             params={"depth": 25}, timeout=6)
    ob = d.get("orderbook_fp") or d.get("orderbook") or {}

    def depth(levels):
        tot = 0.0
        for px_s, sz_s in (levels or [])[:8]:
            try:
                tot += float(px_s) * float(sz_s)
            except (TypeError, ValueError):
                continue
        return tot

    yes = depth(ob.get("yes_dollars") or ob.get("yes"))
    no = depth(ob.get("no_dollars") or ob.get("no"))
    if yes + no <= 0:
        return None
    return (yes - no) / (yes + no)  # [-1, 1]; + = more YES resting demand


def kalshi_book_imbalance(ticker):
    return _cached(("kb_book", ticker), 10, lambda: _kalshi_book(ticker))


# --------------------------------------------------------------------------
# Combined snapshot (parallel, bounded)
# --------------------------------------------------------------------------
def get_flow_signals(ticker):
    """Return dict of microstructure/sentiment signals, each None if unavailable."""
    out = {"book_imb": None, "trade_flow": None, "funding_ann": None,
           "oi_btc": None, "oi_chg_15m": None, "kalshi_book_imb": None}
    jobs = {
        "book": book_imbalance,
        "flow": trade_flow,
        "hl": hl_derivs,
    }
    if ticker:
        jobs["kb"] = lambda: kalshi_book_imbalance(ticker)
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futs = {ex.submit(fn): k for k, fn in jobs.items()}
        for fut, k in futs.items():
            try:
                v = fut.result(timeout=12)
            except Exception:
                v = None
            if k == "hl" and isinstance(v, dict):
                out["funding_ann"] = v.get("funding_ann")
                out["oi_btc"] = v.get("oi_btc")
                out["oi_chg_15m"] = v.get("oi_chg_15m")
            elif k == "book":
                out["book_imb"] = v
            elif k == "flow":
                out["trade_flow"] = v
            elif k == "kb":
                out["kalshi_book_imb"] = v
    return out


def flow_score_direction_relative(signals, direction):
    """Blend available flow signals into one [-1, 1] score, signed so that
    positive supports `direction` (+1 up / -1 down). Returns (score, parts)."""
    parts = {}
    b = signals.get("book_imb")
    if b is not None:
        parts["book"] = max(-1.0, min(1.0, b)) * direction
    f = signals.get("trade_flow")
    if f is not None:
        parts["flow"] = max(-1.0, min(1.0, f)) * direction
    k = signals.get("kalshi_book_imb")
    if k is not None:
        parts["kbook"] = max(-1.0, min(1.0, k)) * direction
    fa = signals.get("funding_ann")
    if fa is not None:
        # contrarian: extreme positive funding = crowded longs = headwind
        parts["funding"] = max(-1.0, min(1.0, -fa / 0.50)) * direction
    oi = signals.get("oi_chg_15m")
    r15 = signals.get("_r15_fallback")
    if oi is not None and r15 is not None:
        # leverage building *against* the recent move is fuel; with it is crowding
        lean = -1.0 if (r15 > 0) == (direction > 0) else 1.0
        parts["oi"] = max(-1.0, min(1.0, oi / 0.03)) * lean * direction
    if not parts:
        return 0.0, {}
    weights = {"book": 0.30, "flow": 0.30, "kbook": 0.20, "funding": 0.12, "oi": 0.08}
    wsum = sum(weights[k] for k in parts)
    score = sum(parts[k] * weights[k] for k in parts) / wsum
    return max(-1.0, min(1.0, score)), parts
