# Threaded Discord — setup blueprint

Create the server on your Discord account (2 min), then follow this layout.
The website automates the rest (see "Automation" below).

## Roles
| Role | Color | How you get it |
|---|---|---|
| @everyone | — | default |
| Subscriber | gold 🟡 | **automatic** — granted when your website subscription activates |
| Mod | red | manual |

Give the Subscriber role access to `#subscriber-chat` (deny @everyone).

## Channels

**📌 START HERE**
- `#welcome` — rules + what Threaded is (paste below)
- `#get-access` — subscription link (paste below)
- `#faq` — paste below

**💬 COMMUNITY (free)**
- `#general` — chat
- `#teasers` — **read-only**; the website auto-posts signal teasers here
- `#yuki-memes` — fun

**🔒 SUBSCRIBERS ONLY**
- `#subscriber-chat` — only the Subscriber role can see/post

## Paste-ready copy

### #welcome
```
🧵 Welcome to Threaded!

Threaded is a live BTC 15-minute Kalshi signal dashboard — direction
conviction, buy/sell timing, scalp meters, spike projections, refreshed
every few seconds. Featuring Yuki, your trading copilot. 💜

📖 Rules:
1. Be kind. No spam, no hate.
2. No financial advice — share ideas, not instructions.
3. Don't share subscriber-only signals outside this server.
4. Never trade money you can't afford to lose.

👉 Full signals live here: <YOUR WEBSITE URL>
👉 Questions? Ask in #general.
```

### #get-access
```
🔓 Get Threaded Pro

The free Discord gets teasers. The full live dashboard — direction
conviction, buy/sell timing, both scalp meters, projections — is for
subscribers.

👉 Subscribe here: <YOUR WEBSITE URL>/#pricing

After you subscribe (use the same Discord username at signup!), the
Subscriber role lands on you automatically. ✨
```

### #faq (pin this)
```
Q: Is this financial advice?
A: No — Threaded is a research tool showing probabilities, not advice.

Q: Does it place trades for me?
A: Never. You always trade yourself on Kalshi.

Q: How do I get the Subscriber role?
A: Subscribe on the website with your Discord username in your profile.
It's automatic — give it ~1 minute.

Q: Can I cancel?
A: Anytime from the Billing page on the site. No lock-in.
```

## Automation (already built into the website)

1. **Subscriber role** — set these env vars on Render (or locally):
   - `DISCORD_BOT_TOKEN` — from https://discord.com/developers/applications
     (create app → Bot → Reset Token → **copy it once**; keep it secret!)
   - Invite the bot to your server with this URL (replace CLIENT_ID):
     `https://discord.com/oauth2/authorize?client_id=CLIENT_ID&permissions=268435456&scope=bot`
     (permission = Manage Roles; then drag the bot's role ABOVE Subscriber
     in Server Settings → Roles, or it can't assign it)
   - `DISCORD_GUILD_ID` — right-click server → Copy Server ID (enable
     Developer Mode in Discord settings first)
   - `DISCORD_SUBSCRIBER_ROLE_ID` — right-click the Subscriber role → Copy ID
2. **Teaser posts** — in `#teasers`: Channel Settings → Integrations →
   Webhooks → New Webhook → Copy URL → set `DISCORD_TEASER_WEBHOOK`.
   Then schedule (free): https://cron-job.org → every 15 min → GET
   `https://YOUR-SITE/api/discord/teaser?secret=YOUR_TEASER_SECRET`
   (set `TEASER_SECRET` env var to something random).

That's it — new subscriber pays → webhook fires → role appears → teasers
keep the free channel hungry. 🧵
