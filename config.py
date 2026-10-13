"""
Configuration module for BTC 15M Kalshi Platform.
Reads environment variables for Render and local development.
"""

import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "btc-kalshi-15m-secret-key-2026")
    PORT = int(os.getenv("PORT", 5000))
    DEBUG = os.getenv("FLASK_ENV") == "development"
    
    # Engine Settings
    KALSHI_CONTRACT = "KXBTCD-15M"
    ENGINE_REFRESH_SECONDS = 10
    
    # External APIs (No API keys required for public feeds)
    BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=50"
    COINBASE_SPOT_URL = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
    MEMPOOL_FEES_URL = "https://mempool.space/api/v1/fees/recommended"
    ALTERNATIVE_ME_FNG_URL = "https://api.alternative.me/fng/?limit=1"

config = Config()
