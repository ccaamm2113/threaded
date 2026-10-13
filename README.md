# BTC 15-Minute Kalshi Trading Platform (Integrated Edition)

A high-performance Bitcoin 15-minute trading platform and prediction terminal powered by a native **15-Minute Internet Intelligence Engine**.

---

## ⚡ What's New in this Version:
1. **Built-in 15M Internet Intelligence Engine** (`btc_15m_engine.py`):
   - Crawls Binance, Coinbase, and Mempool in real time.
   - Computes 15m Candlestick changes, RSI (14), EMA stack (9, 21, 50), ATR (14) stop loss & take profit levels, and order book flow.
   - Synchronized countdown ring to the exact second when 15m contracts close.
2. **Direct REST API Endpoints on your Render Website**:
   - `GET /api/btc-15m-stats` — Returns complete 15m stats payload
   - `GET /api/btc-15m-summary` — Compact summary for headers & tickers
   - `POST /api/btc-stats` — Webhook receiver for automated external pushes
3. **Live 15M Scalping Terminal UI** (`/app`):
   - Real-time tick flashes (Green / Red)
   - Visual probability confidence score for UP/YES vs DOWN/NO contract choices
   - Triggering signal explanation badges

---

## 🚀 Running Locally:

### On Windows:
Double-click `START_WEBSITE.bat` or run:
```bash
pip install -r requirements.txt
python server.py
```
Open your browser at `http://localhost:5000/app`

### On Mac / Linux:
```bash
pip install -r requirements.txt
python3 server.py
```

---

## 🌐 Deploying to Render.com:

1. Upload or push this folder to your GitHub or GitLab repository.
2. In your Render Dashboard, click **New +** $\rightarrow$ **Blueprint** (or **Web Service**):
   - If using Blueprint: Render will automatically detect `render.yaml`.
   - If using Web Service:
     - **Runtime**: `Python 3`
     - **Build Command**: `pip install -r requirements.txt`
     - **Start Command**: `gunicorn server:app`
     - **Plan**: `Free`
3. Click **Create Web Service**. Your 15m BTC Trading Platform will be live at `https://your-app.onrender.com/app`!
