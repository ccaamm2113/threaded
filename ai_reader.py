"""AI market reader for Threaded's prediction engine.

An LLM acts as one more voter: it reads the full quant snapshot (model,
history, momentum, microstructure, derivatives, Kalshi quotes) and returns a
directional probability plus short written reasoning.

Design rules (cost + latency + honesty):
  - Only runs when AI_API_KEY is set; otherwise the engine is 100% quant and
    nothing changes. Any error -> silent fallback to None.
  - OpenAI-compatible chat-completions API, so any provider works via
    AI_BASE_URL / AI_MODEL (OpenAI, xAI, DeepSeek, OpenRouter, ...).
  - NEVER blocks /api/state: refreshes happen in a daemon thread per ticker;
    callers always get the latest cached read (stale up to a few minutes) or
    None while the first read is in flight.
  - Cached ~90s per ticker -> about 10 calls per 15-min window. With a mini
    model that's a few cents a day.
  - The vote is deliberately modest: it can nudge conviction, never single-
    handedly flip the quant direction, and strong disagreement caps the tier
    at LEAN (same philosophy as the model/history disagreement cap).
"""
import json
import os
import re
import threading
import time

import requests

TTL = 90          # seconds between AI reads per ticker
MAX_STALE = 300   # serve a cached read up to this old; then None
TIMEOUT = 25      # hard cap on the LLM HTTP call

_lock = threading.Lock()
_state = {}       # ticker -> {"ts": float, "read": dict|None, "refreshing": bool}


def configured():
    return bool(os.environ.get("AI_API_KEY", "").strip())


def _build_prompt(snap):
    return (
        "You are a short-horizon crypto analyst. A binary market pays $1 if BTC "
        "closes ABOVE the target at expiry, $0 otherwise. Current snapshot:\n"
        + json.dumps(snap, indent=1)
        + "\nReply with ONLY a JSON object: {\"p_up\": 0.0-1.0 probability BTC "
        "finishes above target, \"confidence\": 0.0-1.0, \"reasoning\": one or two "
        "sentences, \"bull\": [up to 3 short strings], \"bear\": [up to 3 short "
        "strings]}. Be calibrated: near 0.5 when signals conflict. Ignore noise; "
        "weigh order flow and positioning over narrative."
    )


def _parse_json(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    try:
        p = float(d.get("p_up"))
        c = float(d.get("confidence", 0.5))
    except (TypeError, ValueError):
        return None
    if not (0.0 <= p <= 1.0):
        return None
    bull = [str(x)[:120] for x in (d.get("bull") or [])][:3]
    bear = [str(x)[:120] for x in (d.get("bear") or [])][:3]
    return {
        "p_up": p,
        "confidence": max(0.0, min(1.0, c)),
        "reasoning": str(d.get("reasoning") or "")[:400],
        "bull": bull,
        "bear": bear,
        "model": os.environ.get("AI_MODEL", "gpt-4o-mini"),
        "ts": time.time(),
    }


def _fetch(ticker, snap):
    key = os.environ["AI_API_KEY"].strip()
    base = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("AI_MODEL", "gpt-4o-mini")
    try:
        r = requests.post(
            base + "/chat/completions",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system",
                     "content": "You are a calibrated short-horizon trading analyst. "
                                "Reply with only the requested JSON."},
                    {"role": "user", "content": _build_prompt(snap)},
                ],
                "temperature": 0.2,
                "max_tokens": 350,
                "response_format": {"type": "json_object"},
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        text = (data["choices"][0]["message"]["content"] or "")
        return _parse_json(text)
    except Exception:
        return None


def _refresh(ticker, snap):
    read = _fetch(ticker, snap) if configured() else None
    with _lock:
        st = _state.get(ticker, {})
        st["refreshing"] = False
        if read is not None:
            st["ts"] = time.time()
            st["read"] = read
        _state[ticker] = st


def get_ai_read(ticker, snapshot):
    """Non-blocking: returns the latest AI read for ticker (dict) or None.

    Kicks off a background refresh when the cached read is older than TTL.
    `snapshot` is the quant state dict captured at call time.
    """
    if not ticker or not configured():
        return None
    now = time.time()
    with _lock:
        st = _state.get(ticker)
        if st and st.get("read") and now - st.get("ts", 0) < MAX_STALE:
            fresh = now - st["ts"] < TTL
            if fresh or st.get("refreshing"):
                return st["read"]
            st["refreshing"] = True  # fall through to spawn refresh
        elif st and st.get("refreshing"):
            return st.get("read")
        else:
            _state[ticker] = st = {"ts": 0, "read": None, "refreshing": True}
    # spawn refresh outside the lock
    snap_copy = dict(snapshot)
    t = threading.Thread(target=_refresh, args=(ticker, snap_copy), daemon=True)
    t.start()
    with _lock:
        return _state.get(ticker, {}).get("read")
