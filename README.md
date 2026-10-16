# ₿ BTC 15-Minute Kalshi Analyzer — Standalone Website

No Streamlit here — this is a plain **Flask backend** serving a hand-rolled
**HTML/CSS/JS frontend**. Same research dashboard, same Yuki.

## Run it (Windows)

1. Extract this folder anywhere.
2. Make sure **Python 3.11+** is installed and on PATH: https://www.python.org/downloads/
3. Double-click **`START_WEBSITE.bat`**.
   - It installs dependencies from `requirements.txt` (first run only),
     starts the server, and opens http://127.0.0.1:5000 in your browser.
4. Keep the black terminal window open while you use the dashboard.
   Close it (or press Ctrl+C) to stop the server.

## Run it manually (any OS)

```bash
pip install -r requirements.txt
python server.py
# open http://127.0.0.1:5000
```

## What's inside

- `server.py` — Flask app. All analyzer logic lives here: BTC candle loading
  (Coinbase → Kraken fallback), 23-feature calibrated logistic model, Kalshi
  KXBTC15M market discovery (4 strategies), blended signal, and the buy/sell
  entry & exit checklists. JSON API: `GET /api/state`, `GET /api/candles`.
- `static/index.html`, `static/styles.css`, `static/app.js` — the single-page
  frontend. Tabs: Dashboard · Chart · Kalshi Market · Model Info. The
  candlestick chart is hand-drawn on `<canvas>` (no chart library).
  The page polls `/api/state` every 15 s (configurable) and updates in place.
- `static/img/` — Yuki artwork (hero, chibi mascot, bullish / bearish / chill).
- `requirements.txt` — flask, pandas, numpy, requests, yfinance, cryptography.

## Notes

- Read-only: the app never places trades and never asks for Kalshi credentials.
- Research only, not financial advice. Kalshi settles KXBTC15M on a 60-second
  CF Benchmarks index average, which can differ from spot candles near the target.
  A contract can lose its full purchase cost. Verify everything on Kalshi.

## v5 — Projected spikes tied into sell timing (Oct 2026)
- New **📊 Projected spike** panel in the Kalshi tab: rough expected up/down move over the
  remaining minutes, from one-sided realized volatility (upside and downside estimated
  separately), shown as both % and projected price, plus the move still required to clear
  the target on each side.
- The projection feeds the trade logic:
  - Entry checklist gained "Expected spike reaches the target" (blocks entries when the
    target needs a bigger-than-normal spike).
  - Two new sell triggers: "Cushion thinner than expected adverse spike — target at risk"
    and "Cushion 2× expected adverse spike — consider banking".
- Verdict line explains in plain words whether a normal spike would clear the target.

## v6 — Header cleanup + faster refresh (Oct 2026)
- Removed the hero image from the top-right header (cleaner look; Yuki still appears
  as the chibi mascot, next to signals, and in the Model tab gallery).
- Faster refresh: new lightweight `/api/quick` endpoint updates the BTC price every
  5 seconds without retraining the model; the full analysis poll defaults to 10s
  (5/10/15/30s selectable) with a 10s server-side cache so rapid polls never pile up.

## v7 — Animated Tokyo background (Oct 2026)
- The site background is now a looping anime video of a neon Tokyo night drive
  (10s seamless loop, muted/autoplay). A dark gradient overlay keeps all text readable.

- v7.1: footer version badge (v7 · Tokyo nights) so you can confirm you're on the latest build.

## v8 — Smarter direction check (Oct 2026)
- Conviction scoring: direction = 50% probability + 25% momentum + 25% edge vs ask.
  Tiers: STRONG LEAN (>= 0.55), LEAN (>= 0.28), else WAIT.
- Adaptive blend: history weight grows from 40% to 75% as expiry nears (the model is
  trained for a 15-min horizon).
- Chop filter: choppy markets (5-min range > 2x hourly norm) need 0.45 conviction.
- Model/history disagreement caps at LEAN; negative edge vs the ask is always a veto.
- New "Why this signal" panel in the Kalshi tab shows the conviction meter and the
  component breakdown (model vote, history vote, blend, momentum, edge, regime).

## v8.1 — Launcher fix: stale servers (Oct 2026)
- Fixed the most common "my changes didn't show up" cause: START_WEBSITE.bat now kills
  any stale analyzer still listening on port 5000 before starting, waits until the new
  server is actually up, and only then opens the browser. Closing the launcher window
  now reliably stops the server too.
- If the footer badge doesn't read v8, you're looking at an old copy: re-extract the
  zip to a fresh folder and Ctrl+Shift+R in the browser.
