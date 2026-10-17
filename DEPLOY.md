# Putting Threaded online (Render, free)

Once this is done you just open a link — always the latest version, no local
server, no stale copies. About 10 minutes, all in your browser.

## Step 1 — Put the code on GitHub (3 min, no git needed)
1. Go to https://github.com/new and create a free account if you don't have one.
2. Name the repo `threaded-btc-analyzer`, make it **Public**, click Create.
3. On the repo page click **"uploading an existing file"**, then drag in the
   *contents* of the `btc_kalshi_website` folder (server.py, render.yaml,
   requirements.txt, static/, etc. — the files, not the folder itself).
4. Click **Commit changes**.

## Step 2 — Deploy on Render (5 min, free)
1. Go to https://render.com and sign up (the free tier is fine).
2. Dashboard → **New +** → **Web Service**.
3. Choose **Public Git repository** and paste your repo URL, e.g.
   `https://github.com/YOURNAME/threaded-btc-analyzer` → Continue.
4. Render auto-detects `render.yaml` — just click **Deploy**.
5. Wait a few minutes for the build. When it's live, Render gives you a URL like
   `https://threaded-btc-analyzer.onrender.com`. Bookmark it. Done.

## Step 3 — Updating later
Whenever you get a new zip from Muse: upload the changed files to the GitHub
repo (same drag-and-drop), and Render redeploys automatically. The link never
changes and you're always on the latest.

## Notes
- Free Render services **sleep after 15 minutes** with no visitors. The first
  visit after a nap takes ~30 seconds to wake up; while the dashboard is open
  (it polls every 10s) it stays awake.
- No API keys or secrets needed — the app only uses public price feeds.
- This is still a research tool, not financial advice.

## Selling it (accounts + subscriptions + Discord)

The app now has user accounts, Stripe subscriptions and a paywall. Extra
one-time setup:

### 1. Database (important)
Render's disk is wiped on every deploy, so the default SQLite file would
lose all user accounts. Get a free Postgres instead:
- https://neon.tech (or supabase.com) → new project → copy the connection string
- In Render: your service → Environment → add `DATABASE_URL` = that string
  (starts with `postgresql://`).

### 2. Stripe
1. https://stripe.com → create account → get **test** keys first.
2. Products → create a monthly product (e.g. Threaded Pro, $29/mo) → copy the **Price ID**.
3. Render env vars: `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_PRICE_ID`.
4. Webhook: Stripe Dashboard → Developers → Webhooks → Add endpoint:
   `https://YOUR-SITE/api/stripe/webhook`, event `checkout.session.completed`
   (plus `customer.subscription.updated` and `customer.subscription.deleted`
   for cancellations) → copy the **signing secret** → `STRIPE_WEBHOOK_SECRET`.
5. Set `APP_URL` to your real site URL.

### 3. Discord (see DISCORD.md)
Set `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `DISCORD_SUBSCRIBER_ROLE_ID`,
`DISCORD_TEASER_WEBHOOK`, and note the generated `TEASER_SECRET` for your
cron-job.org teaser schedule.

Test the whole flow with Stripe test cards before going live, then swap in
live keys. And get the Terms/Privacy pages reviewed by a lawyer — they're
drafts.

## Email + admin (v12)

**Email verification & password reset** need a mail sender:
1. Sign up at https://resend.com (free tier is plenty), verify your domain
   (or use Resend's test domain while trying it out).
2. Create an API key → Render env var `RESEND_API_KEY`.
3. Set `MAIL_PROVIDER=resend` and `MAIL_FROM` (e.g. `Threaded <noreply@yourdomain.com>`).
   Without these, the app logs email links to the server console instead of
   sending (fine for local dev, not for production).

**Admin panel** (`/admin`): set `ADMIN_EMAILS` to your email address
(comma-separated for multiple). You get a user table with counts, manual
grant/revoke of subscriptions (handy for comped founding members), resend
verification, and delete. Logged-in non-admins get a 403.
