"""
BTC 15-Minute Kalshi Trading Platform — Integrated Server
Equipped with the native Kalshi 15M UP / DOWN Betting Probability Engine.
"""

import os
import time
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

# Import the 15m BTC & Kalshi Engine
from btc_15m_engine import get_15m_btc_stats

load_dotenv()

app = Flask(
    __name__,
    static_folder="static",
    template_folder="templates"
)
app.secret_key = os.getenv("SECRET_KEY", "btc-kalshi-15m-secret-key-2026")

# -------------------------------------------------------------
# DEDICATED KALSHI 15M UP / DOWN BETTING FEEDS
# -------------------------------------------------------------
@app.route("/api/kalshi/up-down-feed", methods=["GET"])
@app.route("/api/kalshi/15m-signal", methods=["GET"])
def api_kalshi_up_down_feed():
    """
    Direct feed for Kalshi 'BTC Up or Down in 15 Minutes' betting contracts.
    Returns:
    - Contract side (UP or DOWN)
    - Win probability %
    - Recommended maximum contract purchase price in cents (e.g. 62¢)
    - EV Edge %
    - Seconds to expiry
    - Current distance from strike (candle open) in dollars
    """
    fresh = request.args.get("fresh") == "true"
    full_stats = get_15m_btc_stats(force_refresh=fresh)
    return jsonify({
        "status": "success",
        "kalshi": full_stats.get("kalshi_betting", {}),
        "price": full_stats["price"],
        "open_strike": full_stats.get("open_strike_15m", full_stats["price"]),
        "seconds_to_close": full_stats["seconds_remaining_in_candle"],
        "timestamp": full_stats["timestamp"],
    })

# Full statistics payload
@app.route("/api/btc-15m-stats", methods=["GET"])
def api_btc_15m_stats():
    fresh = request.args.get("fresh") == "true"
    data = get_15m_btc_stats(force_refresh=fresh)
    return jsonify(data)

# Lightweight summary
@app.route("/api/btc-15m-summary", methods=["GET"])
def api_btc_15m_summary():
    full = get_15m_btc_stats()
    kb = full.get("kalshi_betting", {})
    return jsonify({
        "status": "success",
        "price": full["price"],
        "change15m": full["price_change_15m"],
        "secondsToClose": full["seconds_remaining_in_candle"],
        "kalshi_recommendation": kb.get("recommendation", "PASS"),
        "kalshi_side": kb.get("recommended_side", "NEUTRAL"),
        "win_probability_pct": kb.get("active_win_prob_pct", 50),
        "max_contract_cents": kb.get("recommended_max_contract_cents", 50),
        "timestamp": full["timestamp"],
    })

# Webhook Receiver for external pushes
@app.route("/api/btc-stats", methods=["POST"])
def receive_external_engine_webhook():
    body = request.get_json(silent=True) or {}
    data = body.get("data", body)
    print(f"[Render Server] Received 15m BTC Engine Push: ${data.get('price')} | Kalshi Side: {data.get('kalshi_betting', {}).get('recommended_side')}")
    return jsonify({"status": "received", "timestamp": time.time()})

# -------------------------------------------------------------
# Website Pages
# -------------------------------------------------------------
@app.route("/")
def index():
    return render_template("landing.html")

@app.route("/app")
def app_view():
    stats = get_15m_btc_stats()
    return render_template("app.html", stats=stats, kalshi=stats.get("kalshi_betting", {}))

@app.route("/login")
def login_view():
    return render_template("login.html")

@app.route("/signup")
def signup_view():
    return render_template("signup.html")

@app.route("/billing")
def billing_view():
    return render_template("billing.html")

@app.route("/terms")
def terms_view():
    return render_template("terms.html")

@app.route("/privacy")
def privacy_view():
    return render_template("privacy.html")

@app.route("/account")
def account_view():
    return render_template("account.html")

@app.route("/health")
def health():
    return jsonify({"status": "healthy", "service": "btc-kalshi-15m-platform", "engine": "active"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
