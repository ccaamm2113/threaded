"""
Kalshi Flow Signals & Order Flow Analytics Module.
Calculates order book imbalances and probability shifts for 15-minute contracts.
"""

from typing import Dict, Any
from btc_15m_engine import get_15m_btc_stats

def get_current_flow_signals() -> Dict[str, Any]:
    stats = get_15m_btc_stats()
    kalshi = stats.get("kalshi_betting", {})
    
    return {
        "status": "active",
        "contract": "KXBTCD-15M",
        "current_spot_price": stats["price"],
        "strike_open_price": stats.get("open_strike_15m", stats["price"]),
        "delta_usd": stats["price_change_15m"],
        "kalshi_recommendation": kalshi.get("recommendation", "PASS_NO_TRADE"),
        "kalshi_side": kalshi.get("recommended_side", "NEUTRAL"),
        "win_probability_pct": kalshi.get("active_win_prob_pct", 50.0),
        "up_win_prob_pct": kalshi.get("up_win_probability_pct", 50.0),
        "down_win_prob_pct": kalshi.get("down_win_probability_pct", 50.0),
        "max_contract_cents": kalshi.get("recommended_max_contract_cents", 50),
        "expected_value_edge_pct": kalshi.get("expected_value_edge_pct", 0.0),
        "conviction": kalshi.get("conviction", "MODERATE"),
        "seconds_to_close": stats["seconds_remaining_in_candle"],
        "time_formatted": kalshi.get("time_formatted", "15:00"),
        "reasons": kalshi.get("reasons", [])
    }
