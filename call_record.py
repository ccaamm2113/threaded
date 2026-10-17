"""Threaded public call record — every sealed call, graded at settlement.

Design (modeled on the *format* of a verifiable public record, with
Threaded's OWN calls only — never another product's numbers):

- One JSONL row per sealed call. Every sealed call counts; there is no
  "practice" concept — a call that fires is graded, win or lose.
- The export is hash-anchored: export.json publishes the SHA-256 digest of
  the JSONL file. Anyone can re-hash the download and compare.
- When the `cryptography` package is available, the hex digest is also
  signed with an Ed25519 keypair (private key in instance/, public key
  published at /api/record/public-key). When it is not available, the
  signature is null and verification falls back to hash + cross-checking
  rows against Kalshi's own API — which is the strongest check anyway,
  since Kalshi settles every round.

The record starts empty at v26 and fills from live calls. No win rate is
claimed until the record itself earns one.
"""
import base64
import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
RECORD_PATH = BASE_DIR / "instance" / "threaded_record.jsonl"
KEY_PATH = BASE_DIR / "instance" / "threaded_record_key.pem"

_lock = threading.Lock()


def kalshi_taker_fee_cents(ask_c):
    """Kalshi taker fee in cents for a contract priced at ask_c cents.

    Kalshi fee schedule: fee = 0.07 * C * (1 - C) where C is the contract
    price in dollars. Max 1.75c at C = $0.50. Returns None when ask is None.
    """
    if ask_c is None:
        return None
    try:
        c = float(ask_c) / 100.0
    except (TypeError, ValueError):
        return None
    if not (0.0 <= c <= 1.0):
        return None
    return 0.07 * c * (1.0 - c) * 100.0


def _ensure_parent():
    RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)


def append_call(row):
    """Append one graded call row to the JSONL record. Returns the row."""
    _ensure_parent()
    line = json.dumps(row, sort_keys=True, separators=(",", ":"))
    with _lock:
        with open(RECORD_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    return row


def load_calls():
    """Return all record rows (oldest first). Bad lines are skipped."""
    if not RECORD_PATH.exists():
        return []
    out = []
    with open(RECORD_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def record_summary(calls=None):
    """Aggregate stats. EV assumes buying the called side at the seal ask
    and holding to settlement, in contract cents. Gross is before fees;
    net subtracts the Kalshi taker fee at the seal ask."""
    if calls is None:
        calls = load_calls()
    graded = [c for c in calls if c.get("won") is not None]
    wins = [c for c in graded if c.get("won")]
    strong = [c for c in graded if c.get("strong")]
    strong_wins = [c for c in strong if c.get("won")]

    def ev(rows, net):
        if not rows:
            return None
        tot = 0.0
        for c in rows:
            ask = c.get("seal_ask_c")
            if ask is None:
                continue
            pnl = (100.0 - ask) if c.get("won") else (-ask)
            if net:
                fee = kalshi_taker_fee_cents(ask) or 0.0
                pnl -= fee
            tot += pnl
        return tot / len(rows)

    return {
        "total": len(graded),
        "wins": len(wins),
        "losses": len(graded) - len(wins),
        "win_rate": (len(wins) / len(graded)) if graded else None,
        "strong_total": len(strong),
        "strong_wins": len(strong_wins),
        "strong_win_rate": (len(strong_wins) / len(strong)) if strong else None,
        "gross_ev_c": ev(graded, net=False),
        "net_ev_c": ev(graded, net=True),
        "since": graded[0].get("graded_at") if graded else None,
        "last_graded_at": graded[-1].get("graded_at") if graded else None,
    }


# --------------------------------------------------------------------------
# Signing (Ed25519 over the hex digest, as text, no trailing newline)
# --------------------------------------------------------------------------
_keypair = {"tried": False, "private": None, "public": None}


def _get_keypair():
    if _keypair["tried"]:
        return _keypair["private"], _keypair["public"]
    _keypair["tried"] = True
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except Exception:
        return None, None
    try:
        _ensure_parent()
        if KEY_PATH.exists():
            private = serialization.load_pem_private_key(
                KEY_PATH.read_bytes(), password=None
            )
        else:
            private = Ed25519PrivateKey.generate()
            KEY_PATH.write_bytes(
                private.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            try:
                KEY_PATH.chmod(0o600)
            except Exception:
                pass
        _keypair["private"] = private
        _keypair["public"] = private.public_key()
    except Exception:
        return None, None
    return _keypair["private"], _keypair["public"]


def public_key_b64():
    """Base64 SPKI DER public key, or None when signing is unavailable."""
    _, public = _get_keypair()
    if public is None:
        return None
    try:
        from cryptography.hazmat.primitives import serialization
        der = public.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return base64.b64encode(der).decode("ascii")
    except Exception:
        return None


def export_envelope():
    """{digestSha256, signature, public_key, count, generated_at}.

    signature is base64 Ed25519 over the hex digest (as text, no trailing
    newline), or None when signing is unavailable — the hash + Kalshi
    cross-check still verify the file in that case."""
    data = RECORD_PATH.read_bytes() if RECORD_PATH.exists() else b""
    digest_hex = hashlib.sha256(data).hexdigest()
    calls = load_calls()
    signature = None
    private, _ = _get_keypair()
    if private is not None:
        try:
            sig = private.sign(digest_hex.encode("ascii"))
            signature = base64.b64encode(sig).decode("ascii")
        except Exception:
            signature = None
    return {
        "digestSha256": digest_hex,
        "signature": signature,
        "public_key": public_key_b64(),
        "count": len(calls),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
