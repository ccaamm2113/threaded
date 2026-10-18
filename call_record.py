"""Threaded public call record — every sealed call, graded at settlement.

Design (modeled on the *format* of a verifiable public record, with
Threaded's OWN calls only — never another product's numbers):

- One row per sealed call, stored in the DB (RecordedCall) so it survives
  Render redeploys. The JSONL file is kept as a local backup only.
  Every sealed call counts; there is no "practice" concept — a call
  that fires is graded, win or lose.
- TWO SECTIONS. (a) ARCHIVED: the 46 pre-rotation calls graded before
  the v26.5 deploy, imported verbatim from the bundled archive asset
  (threaded_record_archive.jsonl + .meta.json — public data, already
  published on /record). They are NEVER re-signed, NEVER backdated,
  and NEVER recomputed under new fee math. Their authenticity is
  verified against the OLD public key + the ORIGINAL digest +
  signature in the archive meta, not the stable key. (b) LIVE: calls
  graded after the v26.5 deploy, signed with the stable key.
- The export is hash-anchored per section: export.json publishes the
  SHA-256 digest of the LIVE canonical JSONL (signed by the stable
  key) AND the archived digest + original signature + old public key.
  Anyone can verify each section against its own key. The stable key's
  signature covers ONLY the live rows — it never appears to cover
  the archived rows.
- The hex digest is signed with an Ed25519 keypair. In production the
  private key comes from the RECORD_SIGNING_KEY env var (base64 PEM) —
  a deploy without it fails loud at startup, so calls are never graded
  under a silently-rotated ephemeral key. In local dev (DEV_MODE=1) the
  key falls back to the instance/ file.

The record starts empty at v26 and fills from live calls. No win rate is
claimed until the record itself earns one.
"""
import base64
import hashlib
import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path

import config

BASE_DIR = Path(__file__).parent
RECORD_PATH = BASE_DIR / "instance" / "threaded_record.jsonl"
KEY_PATH = BASE_DIR / "instance" / "threaded_record_key.pem"
# Archive asset (bundled, PUBLIC data — already published on /record).
# The JSONL holds the 46 pre-rotation calls in canonical form, so its
# SHA-256 equals the original digest in the meta file. The meta holds
# the old public key + original digest + original signature — the
# proof that authenticates the archived rows without re-signing them.
ARCHIVE_PATH = BASE_DIR / "threaded_record_archive.jsonl"
ARCHIVE_META_PATH = BASE_DIR / "threaded_record_archive.meta.json"

_lock = threading.Lock()

# Fields of a graded-call row, in canonical order. The DB model
# (RecordedCall) carries exactly these 13 columns.
ROW_FIELDS = (
    "ticker", "close_epoch_ms", "side", "sealed_at", "seal_ask_c",
    "seal_blended", "seal_btc_price", "target", "result", "won",
    "strong", "graded_at", "grade_source",
)

# Flask app, set once by server.py at startup (needed for DB access).
_app = None


def set_app(app):
    global _app
    _app = app


def kalshi_taker_fee_cents(ask_c, contracts=1):
    """Kalshi taker fee in cents PER CONTRACT for an order of `contracts`
    contracts priced at ask_c cents.

    Kalshi fee schedule: fee = 0.07 * C * (1 - C) per contract, where C is
    the contract price in dollars — rounded UP to the next cent PER ORDER,
    not per contract. So the per-contract fee for an order of N contracts
    is ceil(0.07 * N * C * (1 - C) * 100) / N. Max raw 1.75c at C = $0.50.
    Default contracts=1 is the conservative case (1-contract fill):
    e.g. 1 contract at 77c costs 2c, not the raw 1.24c.
    Returns None when ask is None.
    """
    if ask_c is None:
        return None
    try:
        c = float(ask_c) / 100.0
    except (TypeError, ValueError):
        return None
    if not (0.0 <= c <= 1.0):
        return None
    try:
        n = int(contracts)
    except (TypeError, ValueError):
        n = 1
    if n <= 0:
        n = 1
    raw_total = 0.07 * n * c * (1.0 - c) * 100.0
    # Tiny epsilon so a float like 2.0000000001 does not ceil to 3.
    return math.ceil(raw_total - 1e-9) / n


def _legacy_raw_fee_cents(ask_c):
    """The PRE-v26.5 taker fee: 0.07 * C * (1 - C) * 100, UNROUNDED.

    Used ONLY for archived rows, so their summary reproduces exactly
    the net EV values originally published under the old math.
    Recomputing archived rows under the ceil fee would rewrite
    history. Live rows always use kalshi_taker_fee_cents (ceil).
    Returns None when ask is None.
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


_archive_meta = {"tried": False, "meta": None}


def archive_meta():
    """The archive asset's meta (old key, original digest + signature,
    count, original published summary) — or None when the asset is
    missing. Read once per process; the asset is committed, so it
    does not change at runtime."""
    if _archive_meta["tried"]:
        return _archive_meta["meta"]
    _archive_meta["tried"] = True
    try:
        if ARCHIVE_META_PATH.exists():
            _archive_meta["meta"] = json.loads(
                ARCHIVE_META_PATH.read_text(encoding="utf-8"))
    except Exception:
        _archive_meta["meta"] = None
    return _archive_meta["meta"]


def archive_public_key():
    """The OLD public key that signed the archived rows, or None."""
    meta = archive_meta()
    return meta.get("public_key") if meta else None


def _ensure_parent():
    RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)


def _row_to_dict(row):
    """A DB RecordedCall (or dict) -> plain dict with exactly ROW_FIELDS.

    The `_archived` metadata flag is deliberately NOT included: the
    canonical serialization (and therefore every digest) covers only
    the 13 canonical fields."""
    if isinstance(row, dict):
        return {k: row.get(k) for k in ROW_FIELDS}
    return {k: getattr(row, k) for k in ROW_FIELDS}


def _row_with_flag(row):
    """A DB RecordedCall -> plain dict with the 13 fields + `_archived`.

    `_archived` is metadata for section splits/summaries; it never
    enters canonical JSONL because _row_to_dict strips it."""
    d = _row_to_dict(row)
    d["_archived"] = bool(getattr(row, "archived", False))
    return d


def _db_rows():
    """All DB rows as plain dicts (oldest first), or None if DB unusable.

    Each dict carries `_archived` (True = pre-rotation archive row,
    False = live row graded under the stable key)."""
    if _app is None:
        return None
    try:
        from models import RecordedCall

        with _app.app_context():
            rows = RecordedCall.query.all()
        rows = sorted(
            rows,
            key=lambda r: (r.graded_at or "", r.close_epoch_ms or 0),
        )
        return [_row_with_flag(r) for r in rows]
    except Exception:
        return None


_archive_imported = False


def _import_archive_once():
    """One-time archive-asset -> DB import, only when no archived rows
    exist yet.

    Imports the bundled archive JSONL verbatim as archived=True. The
    rows are never re-signed or altered: they go in exactly as the
    archive file holds them, and their authenticity lives in the
    archive meta (old key + original digest + signature). Idempotent:
    dedup by ticker within the archived section."""
    global _archive_imported
    if _archive_imported or _app is None:
        return
    _archive_imported = True
    try:
        from models import RecordedCall, db

        with _app.app_context():
            if RecordedCall.query.filter_by(archived=True).first() is not None:
                return
            if not ARCHIVE_PATH.exists():
                return
            with open(ARCHIVE_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if not row.get("ticker"):
                        continue
                    if RecordedCall.query.filter_by(
                        ticker=row["ticker"], archived=True
                    ).first() is not None:
                        continue
                    db.session.add(RecordedCall(
                        **{k: row.get(k) for k in ROW_FIELDS},
                        archived=True))
            db.session.commit()
    except Exception:
        try:
            from models import db

            with _app.app_context():
                db.session.rollback()
        except Exception:
            pass


_jsonl_imported = False


def _import_jsonl_once():
    """One-time legacy JSONL -> DB import (live rows only).

    Runs on first boot so a local JSONL written by an older version is
    not lost. Rows whose ticker is already present — in EITHER section
    — are skipped, so an archived row is never duplicated as a live
    row. In production the JSONL is empty (ephemeral instance/), so
    this is a no-op there.
    """
    global _jsonl_imported
    if _jsonl_imported or _app is None:
        return
    _jsonl_imported = True
    try:
        from models import RecordedCall, db

        with _app.app_context():
            if not RECORD_PATH.exists():
                return
            with open(RECORD_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if not row.get("ticker"):
                        continue
                    if RecordedCall.query.filter_by(
                        ticker=row["ticker"]
                    ).first() is not None:
                        continue
                    db.session.add(RecordedCall(
                        **{k: row.get(k) for k in ROW_FIELDS},
                        archived=False))
            db.session.commit()
    except Exception:
        try:
            from models import db

            with _app.app_context():
                db.session.rollback()
        except Exception:
            pass


def _append_jsonl_backup(row):
    """Best-effort JSONL backup. The DB is primary; a backup failure
    must never break grading."""
    try:
        _ensure_parent()
        line = json.dumps(row, sort_keys=True, separators=(",", ":"))
        with _lock:
            with open(RECORD_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def append_call(row):
    """Append one graded call row to the record (DB-primary, LIVE
    section). Returns row.

    Skips the write entirely when a LIVE row with the ticker is
    already present (one sealed call per window) — so a double-grade
    can never duplicate a row in the DB or in the JSONL backup. The
    dedup is section-aware: an archived row with the same ticker does
    NOT block a live insert (tickers are unique per window, so this
    should never happen, but the archive must never swallow a live
    call). Writes the JSONL backup only when the DB insert actually
    happened. If the DB is unavailable, falls back to JSONL-only so
    the call is not lost.
    """
    row = _row_to_dict(row)
    if _app is not None:
        try:
            from models import RecordedCall, db

            with _app.app_context():
                if RecordedCall.query.filter_by(
                    ticker=row["ticker"], archived=False
                ).first() is not None:
                    return row  # already recorded — do not write again
                db.session.add(RecordedCall(**row, archived=False))
                db.session.commit()
            _append_jsonl_backup(row)
            return row
        except Exception:
            try:
                from models import db

                with _app.app_context():
                    db.session.rollback()
            except Exception:
                pass
    # DB unavailable or insert failed: JSONL is the only copy.
    _ensure_parent()
    line = json.dumps(row, sort_keys=True, separators=(",", ":"))
    with _lock:
        with open(RECORD_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    return row


def load_calls():
    """Return all record rows (oldest first). DB-primary; JSONL fallback
    when no app/DB context is available.

    Each row carries `_archived` (True = pre-rotation archive row,
    False = live row). The archive import runs first so a fresh DB
    shows the archived section immediately."""
    _import_archive_once()
    _import_jsonl_once()
    rows = _db_rows()
    if rows is not None:
        return rows
    if not RECORD_PATH.exists():
        return []
    out = []
    with open(RECORD_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = _row_to_dict(json.loads(line))
                d["_archived"] = False
                out.append(d)
            except Exception:
                continue
    return out


def load_live_calls(calls=None):
    """Live-section rows only (graded under the stable key)."""
    if calls is None:
        calls = load_calls()
    return [c for c in calls if not c.get("_archived")]


def load_archived_calls(calls=None):
    """Archived-section rows only (pre-rotation, original key)."""
    if calls is None:
        calls = load_calls()
    return [c for c in calls if c.get("_archived")]


def canonical_jsonl(calls):
    """Canonical JSONL bytes for a list of rows — the exact bytes the
    published digest is computed over. The export download uses this
    same serialization so `sha256sum` of the download matches."""
    return "".join(
        json.dumps(_row_to_dict(c), sort_keys=True, separators=(",", ":")) + "\n"
        for c in calls
    ).encode("utf-8")


def _section_summary(rows, archived_section):
    """Aggregate stats for one section.

    EV assumes buying the called side at the seal ask and holding to
    settlement, in contract cents. Gross is before fees. Net subtracts
    the Kalshi taker fee at the seal ask — ARCHIVED rows use the
    legacy UNROUNDED fee (the math their published net EV was computed
    under), LIVE rows use the ceil-per-order fee (1-contract case, the
    conservative case). Mixing the two would rewrite the archive's
    history, so the fee rule is per-section, never blended."""
    graded = [c for c in rows if c.get("won") is not None]
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
                if archived_section:
                    fee = _legacy_raw_fee_cents(ask) or 0.0
                else:
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


def record_summary(calls=None):
    """Aggregate stats: COMBINED totals plus per-section breakdowns.

    Top-level fields (total, wins, win_rate, gross/net EV, since,
    last_graded_at) are the COMBINED record — archived + live — so
    existing consumers keep working. `archived` and `live` carry the
    per-section summaries. Combined EV is the count-weighted mean of
    the two sections' EVs (each computed under its own section's fee
    rule), so the combined number never silently re-prices the archive.
    """
    if calls is None:
        calls = load_calls()
    archived = load_archived_calls(calls)
    live = load_live_calls(calls)
    a = _section_summary(archived, archived_section=True)
    l = _section_summary(live, archived_section=False)

    total = a["total"] + l["total"]
    wins = a["wins"] + l["wins"]
    strong_total = a["strong_total"] + l["strong_total"]
    strong_wins = a["strong_wins"] + l["strong_wins"]

    def weighted(a_ev, a_n, l_ev, l_n):
        if a_n + l_n == 0:
            return None
        a_tot = (a_ev * a_n) if (a_ev is not None and a_n) else 0.0
        l_tot = (l_ev * l_n) if (l_ev is not None and l_n) else 0.0
        return (a_tot + l_tot) / (a_n + l_n)

    since = a["since"] or l["since"]
    last = l["last_graded_at"] or a["last_graded_at"]
    return {
        "total": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": (wins / total) if total else None,
        "strong_total": strong_total,
        "strong_wins": strong_wins,
        "strong_win_rate": (strong_wins / strong_total) if strong_total else None,
        "gross_ev_c": weighted(a["gross_ev_c"], a["total"],
                               l["gross_ev_c"], l["total"]),
        "net_ev_c": weighted(a["net_ev_c"], a["total"],
                             l["net_ev_c"], l["total"]),
        "since": since,
        "last_graded_at": last,
        "archived": a,
        "live": l,
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
    env_key = (getattr(config, "RECORD_SIGNING_KEY", "") or "").strip()
    if env_key:
        try:
            private = serialization.load_pem_private_key(
                base64.b64decode(env_key), password=None
            )
        except Exception as exc:
            raise RuntimeError(
                "RECORD_SIGNING_KEY is set but is not a valid base64 PEM "
                f"Ed25519 private key: {exc}"
            ) from exc
        _keypair["private"] = private
        _keypair["public"] = private.public_key()
        return _keypair["private"], _keypair["public"]
    if not config.DEV_MODE:
        raise RuntimeError(
            "RECORD_SIGNING_KEY is not set. Generate a key with: "
            "python3 -c \"from cryptography.hazmat.primitives.asymmetric.ed25519 "
            "import Ed25519PrivateKey; from cryptography.hazmat.primitives import "
            "serialization; import base64; "
            "k=Ed25519PrivateKey.generate(); "
            "pem=k.private_bytes(serialization.Encoding.PEM, "
            "serialization.PrivateFormat.PKCS8, serialization.NoEncryption()); "
            "print(base64.b64encode(pem).decode())\" "
            "and paste it into Render env vars as RECORD_SIGNING_KEY. "
            "A production deploy without it must never grade calls with an "
            "ephemeral key."
        )
    # Local dev only: fall back to the instance/ file.
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


def require_signing_key():
    """Fail loud at startup when production has no signing key.

    Call once at boot. In production (not DEV_MODE) this raises unless
    RECORD_SIGNING_KEY is set and valid, so the app refuses to start
    rather than grading calls under an ephemeral key. In dev it loads
    (or generates) the local instance/ key."""
    _get_keypair()


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
    """Two-part envelope: {digestSha256, signature, public_key,
    count, live_count, archived_count, archived: {...}, generated_at}.

    The export.jsonl download is the ARCHIVED canonical JSONL
    (first `archived_count` lines) followed by the LIVE canonical
    JSONL (remaining lines). Verification is per-section:
    - LIVE: `digestSha256` is the SHA-256 of the live section only,
      and `signature` is the STABLE key's Ed25519 over that hex digest
      (as text, no trailing newline). The stable key NEVER signs the
      archived rows — its signature must not be read as covering them.
    - ARCHIVED: `archived.digestSha256` is the SHA-256 of the archived
      section (equal to the original published digest), and
      `archived.signature` is the ORIGINAL signature over it, verified
      against `archived.public_key` (the old key). The archived rows
      are never re-signed.
    `signature` is None when signing is unavailable — the hashes +
    Kalshi cross-check still verify the file in that case."""
    calls = load_calls()
    archived = load_archived_calls(calls)
    live = load_live_calls(calls)
    live_data = canonical_jsonl(live)
    live_digest = hashlib.sha256(live_data).hexdigest()
    signature = None
    private, _ = _get_keypair()
    if private is not None:
        try:
            sig = private.sign(live_digest.encode("ascii"))
            signature = base64.b64encode(sig).decode("ascii")
        except Exception:
            signature = None
    meta = archive_meta() or {}
    archived_block = {
        "public_key": meta.get("public_key"),
        "digestSha256": meta.get("digestSha256"),
        "signature": meta.get("signature"),
        "count": len(archived),
        "archived_at": meta.get("archived_at"),
    }
    return {
        "digestSha256": live_digest,
        "signature": signature,
        "public_key": public_key_b64(),
        "count": len(calls),
        "live_count": len(live),
        "archived_count": len(archived),
        "archived": archived_block,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
