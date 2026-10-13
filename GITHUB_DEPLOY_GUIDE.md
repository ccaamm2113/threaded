# 🚀 How to Extract and Deploy to GitHub & Render

This zip file contains your complete **BTC 15-Minute Kalshi Trading Platform** with the **15M Internet Intelligence & Up/Down Betting Engine** fully integrated.

---

### Step 1: Download & Extract into Your GitHub Repository

1. **Download the ZIP**: Click the download link or download button.
2. **Extract**:
   - Open your local GitHub repository folder on your computer.
   - Extract the contents of this zip directly into the **root** of your repository so that `server.py`, `render.yaml`, `requirements.txt`, and `templates/` sit in your main repo folder.

---

### Step 2: Push to GitHub

Open your terminal or Git Bash inside your repo folder:
```bash
git add .
git commit -m "Integrate 15M BTC Kalshi Up/Down betting engine"
git push origin main
```

---

### Step 3: Render Deployment (Zero Configuration)

If your repo is already linked to Render:
- Render will automatically trigger a new deployment upon `git push`!
- It will read `render.yaml` or `Procfile`, run `pip install -r requirements.txt`, and launch `gunicorn server:app`.
- Your live 15m betting terminal will immediately be running at `https://your-app.onrender.com/app`!

---

### 📡 New Live API Endpoints Available on Your Render App:
- `GET /api/kalshi/up-down-feed` — Real-time UP / DOWN contract recommendation, win probability %, max purchase price in cents, and delta vs strike.
- `GET /api/btc-15m-stats` — Complete 15m technical indicators (RSI, EMA stack, ATR scalp levels, Mempool fees).
- `GET /api/btc-15m-summary` — Fast summary for ribbons and banners.
