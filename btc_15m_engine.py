"""
BTC 15-Minute Market Intelligence & Kalshi UP/DOWN Betting Engine
Runs queries across internet sources (Binance, Coinbase, Mempool.space, Alternative.me)
Computes real-time 15m Candlestick OHLCV, RSI, EMA stack, and Kalshi UP vs DOWN betting probability.
"""

import time
import math
import json
import urllib.request
from typing import Dict, Any, List, Optional

CACHE_TTL_SECONDS = 6
_cached_data: Optional[Dict[str, Any]] = None
_last_fetch_time: float = 0.0

def fetch_json(url: str, timeout: float = 5.0) -> Optional[Any]:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "BTC-Kalshi-15M-Engine/1.0",
                "Accept": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

def calculate_ema(data: List[float], period: int) -> float:
    if not data:
        return 0.0
    k = 2.0 / (period + 1.0)
    ema = data[0]
    for val in data[1:]:
        ema = val * k + ema * (1.0 - k)
    return round(ema, 2)

def calculate_rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses += abs(diff)
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = abs(diff) if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)

def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if len(closes) < 2:
        return 150.0
    tr_list = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        tr_list.append(tr)
    recent = tr_list[-period:] if len(tr_list) >= period else tr_list
    return round(sum(recent) / len(recent), 2) if recent else 150.0

def normal_cdf(x: float) -> float:
    """Approximation of standard normal cumulative distribution function."""
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    d = 0.3989423 * math.exp((-x * x) / 2.0)
    p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))))
    if x > 0:
        p = 1.0 - p
    return p

def get_15m_btc_stats(force_refresh: bool = False) -> Dict[str, Any]:
    global _cached_data, _last_fetch_time
    now = time.time()
    
    # Return cache if still fresh
    if not force_refresh and _cached_data and (now - _last_fetch_time < CACHE_TTL_SECONDS):
        candle_close = math.ceil(now / 900.0) * 900.0
        seconds_left = max(0, int(candle_close - now))
        elapsed = 900.0 - seconds_left
        progress = round((elapsed / 900.0) * 100.0, 1)
        _cached_data["seconds_remaining_in_candle"] = seconds_left
        _cached_data["candle_progress_percent"] = progress
        if "kalshi_betting" in _cached_data:
            _cached_data["kalshi_betting"]["seconds_remaining"] = seconds_left
            m = seconds_left // 60
            s = seconds_left % 60
            _cached_data["kalshi_betting"]["time_formatted"] = f"{m:02d}:{s:02d}"
        return _cached_data

    # Default fallback
    base_price = 83500.0
    open_price = 83450.0
    rsi14 = 55.0
    ema9 = base_price
    ema21 = base_price - 50.0
    ema50 = base_price - 120.0
    atr14 = 280.0
    spread_usd = 0.5
    bid_imbalance = 0.52
    sources_synced = {"binance": False, "coinbase": False, "mempool": False, "alternative_me": False}

    # 1. Fetch Binance 15m klines
    klines = fetch_json("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=50")
    if klines and isinstance(klines, list) and len(klines) > 0:
        sources_synced["binance"] = True
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        curr = klines[-1]
        open_price = float(curr[1])
        base_price = float(curr[4])
        rsi14 = calculate_rsi(closes, 14)
        ema9 = calculate_ema(closes, 9)
        ema21 = calculate_ema(closes, 21)
        ema50 = calculate_ema(closes, 50)
        atr14 = calculate_atr(highs, lows, closes, 14)
    else:
        # Fallback to Coinbase
        cb = fetch_json("https://api.coinbase.com/v2/prices/BTC-USD/spot")
        if cb and "data" in cb and "amount" in cb["data"]:
            sources_synced["coinbase"] = True
            base_price = float(cb["data"]["amount"])
            open_price = base_price - 20.0
            ema9 = base_price
            ema21 = base_price - 30.0

    # 2. Mempool fees
    mempool_fee = 14
    mp = fetch_json("https://mempool.space/api/v1/fees/recommended")
    if mp and "fastestFee" in mp:
        sources_synced["mempool"] = True
        mempool_fee = int(mp["fastestFee"])

    # 3. Fear & Greed
    fear_greed_score = 62
    fear_greed_class = "Greed"
    fng = fetch_json("https://api.alternative.me/fng/?limit=1")
    if fng and "data" in fng and len(fng["data"]) > 0:
        sources_synced["alternative_me"] = True
        fear_greed_score = int(fng["data"][0].get("value", 62))
        fear_greed_class = fng["data"][0].get("value_classification", "Greed")

    # 4. 15-Minute Candle Calculations
    price_change_15m = round(base_price - open_price, 2)
    price_change_pct_15m = round((price_change_15m / open_price) * 100.0, 2) if open_price > 0 else 0.0

    candle_close = math.ceil(now / 900.0) * 900.0
    seconds_left = max(0, int(candle_close - now))
    elapsed = 900.0 - seconds_left
    progress = round((elapsed / 900.0) * 100.0, 1)
    m_left = seconds_left // 60
    s_left = seconds_left % 60
    time_formatted = f"{m_left:02d}:{s_left:02d}"

    # 5. KALSHI 15M UP / DOWN BETTING ENGINE ALGORITHM
    # Measures the probability that the 15m candle closes HIGHER than open_price (UP) or LOWER (DOWN)
    time_fraction = max(0.02, seconds_left / 900.0)
    remaining_expected_vol = atr14 * math.sqrt(time_fraction)
    z_score = price_change_15m / remaining_expected_vol if remaining_expected_vol > 0 else (3.0 if price_change_15m > 0 else -3.0)
    
    raw_up_prob = normal_cdf(z_score)

    # Momentum adjustments
    if rsi14 > 60:
        raw_up_prob += 0.05
    elif rsi14 < 40:
        raw_up_prob -= 0.05

    if ema9 > ema21:
        raw_up_prob += 0.04
    else:
        raw_up_prob -= 0.04

    # Win probability for Kalshi UP contract
    up_prob_clamped = min(0.95, max(0.05, raw_up_prob))
    up_prob_pct = round(up_prob_clamped * 100.0, 1)
    down_prob_pct = round((1.0 - up_prob_clamped) * 100.0, 1)

    # Betting Recommendation
    kalshi_reasons = []
    kalshi_reasons.append(f"Spot is {'+' if price_change_15m >= 0 else ''}${price_change_15m:.2f} ({price_change_pct_15m}%) vs 15m open strike (${open_price:,.2f})")

    if up_prob_pct >= 62.0:
        kalshi_rec = "BET_UP"
        kalshi_side = "UP"
        win_prob = up_prob_pct
        max_cents = int(min(88, win_prob - 6))
        edge_pct = round(win_prob - 50.0, 1)
        conviction = "HIGH" if win_prob >= 75.0 else "MODERATE"
        kalshi_reasons.append("Bullish momentum favors 15m contract closing ABOVE strike.")
    elif up_prob_pct <= 38.0:
        kalshi_rec = "BET_DOWN"
        kalshi_side = "DOWN"
        win_prob = down_prob_pct
        max_cents = int(min(88, win_prob - 6))
        edge_pct = round(win_prob - 50.0, 1)
        conviction = "HIGH" if win_prob >= 75.0 else "MODERATE"
        kalshi_reasons.append("Bearish momentum favors 15m contract closing BELOW strike.")
    else:
        kalshi_rec = "PASS_NO_TRADE"
        kalshi_side = "NEUTRAL"
        win_prob = max(up_prob_pct, down_prob_pct)
        max_cents = 50
        edge_pct = 0.0
        conviction = "LOW (CHOP)"
        kalshi_reasons.append("Chop zone near strike. Edge < 12%; wait for breakout.")

    if seconds_left < 180:
        kalshi_reasons.append(f"Late contract ({seconds_left}s left): rapid time-decay favors holding current trajectory.")
    else:
        kalshi_reasons.append(f"Mid-candle ({time_formatted} left): watch EMA9 (${ema9:,.2f}) for direction confirmation.")

    # Scalping Stop Loss & Take Profit
    is_bull = up_prob_pct >= 50.0
    stop_loss = round(base_price - 1.5 * atr14 if is_bull else base_price + 1.5 * atr14, 2)
    take_profit = round(base_price + 2.5 * atr14 if is_bull else base_price - 2.5 * atr14, 2)

    payload = {
        "status": "success",
        "pair": "BTC/USDT",
        "price": base_price,
        "open_strike_15m": open_price,
        "price_change_15m": price_change_15m,
        "price_change_percent_15m": price_change_pct_15m,
        "seconds_remaining_in_candle": seconds_left,
        "candle_progress_percent": progress,
        "candle_close_timestamp": int(candle_close * 1000),
        
        # Dedicated Kalshi 15M Betting Feed
        "kalshi_betting": {
            "contract": "KXBTCD-15M",
            "recommendation": kalshi_rec,
            "recommended_side": kalshi_side,
            "up_win_probability_pct": up_prob_pct,
            "down_win_probability_pct": down_prob_pct,
            "active_win_prob_pct": win_prob,
            "recommended_max_contract_cents": max_cents,
            "expected_value_edge_pct": edge_pct,
            "conviction": conviction,
            "time_formatted": time_formatted,
            "seconds_remaining": seconds_left,
            "strike_open_price": open_price,
            "current_price": base_price,
            "delta_usd": price_change_15m,
            "reasons": kalshi_reasons
        },
        
        "indicators": {
            "rsi14": rsi14,
            "ema9": ema9,
            "ema21": ema21,
            "ema50": ema50,
            "atr14": atr14,
        },
        "verdict": {
            "signal": "BULLISH" if up_prob_pct >= 55 else "BEARISH" if up_prob_pct <= 45 else "NEUTRAL",
            "confidence_score": int((up_prob_pct - 50.0) * 2),
            "action": kalshi_reasons[1] if len(kalshi_reasons) > 1 else "Watch candle progression",
            "suggested_stop_loss": stop_loss,
            "suggested_take_profit": take_profit,
            "key_factors": kalshi_reasons,
        },
        "macro": {
            "mempool_fastest_fee": mempool_fee,
            "fear_greed_score": fear_greed_score,
            "fear_greed_classification": fear_greed_class,
        },
        "sources": sources_synced,
        "timestamp": int(now * 1000),
        "iso_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }

    _cached_data = payload
    _last_fetch_time = now
    return payload
