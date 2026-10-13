"""Discord funnel automation (webhook-based, no persistent bot needed).

1. handle_new_subscriber(user): called after a Stripe subscription activates.
   Looks the member up by their Discord username and grants the Subscriber role.
2. send_teaser(state): posts a FOMO teaser embed to the free channel via an
   Incoming Webhook. Hit /api/discord/teaser?secret=... on a schedule
   (e.g. cron-job.org every 15 min).

See DISCORD.md for the server setup guide.
"""
import requests

import config

API = "https://discord.com/api/v10"


def _bot_headers():
    return {"Authorization": f"Bot {config.DISCORD_BOT_TOKEN}"}


def _discord_configured():
    return bool(config.DISCORD_BOT_TOKEN and config.DISCORD_GUILD_ID
                and config.DISCORD_SUBSCRIBER_ROLE_ID)


def find_member_id(username):
    q = (username or "").lstrip("@").strip()
    if not q:
        return None
    r = requests.get(f"{API}/guilds/{config.DISCORD_GUILD_ID}/members/search",
                     params={"query": q, "limit": 5},
                     headers=_bot_headers(), timeout=10)
    r.raise_for_status()
    members = r.json() or []
    # prefer an exact (case-insensitive) username match
    for m in members:
        if (m.get("user", {}).get("username", "").lower() == q.lower()):
            return m["user"]["id"]
    return members[0]["user"]["id"] if members else None


def assign_subscriber_role(discord_user_id):
    r = requests.put(
        f"{API}/guilds/{config.DISCORD_GUILD_ID}/members/{discord_user_id}"
        f"/roles/{config.DISCORD_SUBSCRIBER_ROLE_ID}",
        headers=_bot_headers(), timeout=10)
    r.raise_for_status()
    return True


def handle_new_subscriber(user):
    """Grant the Subscriber role. Returns True/False/None (None = not configured)."""
    if not _discord_configured():
        return None
    if not user.discord_username:
        return False
    uid = find_member_id(user.discord_username)
    if not uid:
        return False
    return assign_subscriber_role(uid)


def build_teaser(state):
    """Compose a teaser embed from a compute_state() result."""
    try:
        sig = state.get("signal") or {}
        scalp = state.get("scalp") or {}
        price = (state.get("btc") or {}).get("price")
        label = sig.get("label", "—")
        verdict = scalp.get("verdict", "none")
        if verdict == "up":
            call = "⚡ Scalp meter is flashing **UP**"
        elif verdict == "down":
            call = "⚡ Scalp meter is flashing **DOWN**"
        elif verdict in ("up_lean", "down_lean"):
            call = "👀 Leaning " + verdict.split("_")[0].upper()
        else:
            call = "⏸ No scalp right now — patience"
        desc = (f"**BTC** ${price:,.0f}\n"
                f"**Signal:** {label}\n"
                f"{call}\n\n"
                f"Subscribers see the full playbook: buy/sell timing, both scalp meters, "
                f"spike projections — live.")
        return {"embeds": [{
            "title": "🧵 Threaded teaser — BTC 15m",
            "description": desc,
            "color": 0x818cf8,
            "footer": {"text": "Research only, not financial advice."},
        }]}
    except Exception:
        return {"content": "🧵 Threaded teaser: the desk is live — full signals for subscribers."}


def send_teaser(state):
    if not config.DISCORD_TEASER_WEBHOOK:
        return False
    r = requests.post(config.DISCORD_TEASER_WEBHOOK, json=build_teaser(state), timeout=10)
    r.raise_for_status()
    return True
