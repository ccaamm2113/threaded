# Threaded — pricing & launch playbook

Companion to `DISCORD.md` (server setup) and `DEPLOY.md` (hosting + Stripe setup).
This covers the business side: what to charge, how to launch, how to grow.

## Recommended pricing

| Tier | Price | Notes |
|---|---|---|
| **Founding members** (first 25) | **$29/mo**, locked for life | Creates urgency; rewards early believers |
| Standard monthly | **$39/mo** | Kicks in after the first 25 |
| Annual | **$290/yr** ("$24.17/mo") | Showing the monthly equivalent converts better |
| Trial | **7 days free** | Standard for trading communities |

**Why these numbers:** comparable crypto/signal Discords cluster at $20–50/mo for
standard tiers (AltSignals $40–48/mo, Jacob's Crypto Clan ~$39/mo tier), with
premium tiers running $99–115+/mo. Threaded is a niche tool (BTC 15-min Kalshi),
not a broad altcoin casino — $29–39 sits in the sweet spot: cheap relative to a
single Kalshi trade's risk, but high enough to filter out freeloaders.

To change the price: create the Price in Stripe Dashboard → Products, copy the
Price ID into the Render env var `STRIPE_PRICE_ID`, and set `PLAN_PRICE_DISPLAY`
(e.g. `$29/mo`) so the landing page matches.

## Launch week checklist

1. **Deploy to Render** (`DEPLOY.md`), Stripe in **test mode** first — run a full
   fake checkout, confirm the Subscriber role lands, then swap to **live keys**.
2. **Create the Discord server** (`DISCORD.md`), plus one extra channel:
   `#results` (read-only) — timestamped signal recaps, wins **and** losses.
   A track record with losses is what separates signal sellers from scammers.
3. **Seed the server** before inviting anyone: rules, FAQ, a few days of teaser
   posts. Nobody joins an empty room.
4. **Announce the founding-member offer** — "first 25 lock in $29/mo forever."
5. **Post daily** for the first two weeks: one morning market brief, evening
   results recap. Consistency is the product as much as the signals.
6. Paper-trade the dashboard's signals yourself for 1–2 weeks and post the
   honest P&L in `#results` before asking strangers to pay.

## Where the first 100 members come from

- **X/Twitter**: daily screenshots of the dashboard + one-line signal recap.
  Trading Twitter converts better than ads for this niche.
- **Reddit**: r/Kalshi and crypto trading subs — contribute first, mention
  Threaded only where it's genuinely relevant (don't spam; you'll get banned).
- **Whop marketplace** (optional, later): listing Threaded there buys
  discoverability but costs ~3% + processing on every sale. Direct Stripe keeps
  ~97%. Start direct; add Whop when you want reach, not at launch.
- **Word of mouth**: the #results track record is your best marketing asset.
  Make it public-facing if you dare — nothing sells signals like receipts.

## Free vs paid split (don't mess this up)

Keep the free Discord genuinely useful (general chat, teasers, memes, help) —
an empty free tier converts nobody. Paid gets: the live dashboard, both scalp
meters, `#subscriber-chat`, and direct Q&A. The worst mistake is paywalling
everything on day one; you lose the community that would have become customers.

## Guardrails

- **No guaranteed returns, ever.** The landing page already carries the risk
  disclaimer — keep it on every page that mentions performance, and never
  promise a win rate in marketing. (`DEPLOY.md`: get Terms/Privacy lawyer-reviewed.)
- **Check Kalshi's terms** on commercial use of their market data before you
  scale — the dashboard republishes Kalshi prices and contract info.
- **Chargebacks**: digital signal subs attract disputes. Stripe's billing portal
  + a clear cancel path (already built) keep disputes low. Keep receipts of
  everything posted in #results.

## Numbers to watch (weekly)

- Teaser viewers → signups (conversion %)
- Trial → paid conversion (aim 20–40%)
- Monthly churn (under 10% is healthy for signal groups)
- Which posts drive the most signups — double down on those
