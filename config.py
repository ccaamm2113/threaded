"""Threaded — central configuration. Everything secret comes from env vars."""
import os


def _req(name, default=None):
    v = os.environ.get(name, default)
    return v


SECRET_KEY = _req("SECRET_KEY", "dev-only-change-me")
DATABASE_URL = _req("DATABASE_URL", "sqlite:///threaded.db")

# Stripe (get these from your Stripe dashboard; test keys first)
STRIPE_SECRET_KEY = _req("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = _req("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_PRICE_ID = _req("STRIPE_PRICE_ID", "")          # the monthly subscription Price
STRIPE_PRICE_ID_YEARLY = _req("STRIPE_PRICE_ID_YEARLY", "")  # yearly subscription Price
STRIPE_PRICE_ID_DAILY = _req("STRIPE_PRICE_ID_DAILY", "")    # daily subscription Price
STRIPE_WEBHOOK_SECRET = _req("STRIPE_WEBHOOK_SECRET", "")

# What the landing page advertises (change freely)
PLAN_NAME = _req("PLAN_NAME", "Threaded Pro")
PLAN_PRICE_DISPLAY = _req("PLAN_PRICE_DISPLAY", "$29/mo")
PLAN_PRICE_DISPLAY_YEARLY = _req("PLAN_PRICE_DISPLAY_YEARLY", "$300/yr")
PLAN_PRICE_DISPLAY_DAILY = _req("PLAN_PRICE_DISPLAY_DAILY", "$10/day")

# Local dev: START_WEBSITE.bat sets DEV_MODE=1 to skip login/paywall locally
DEV_MODE = _req("DEV_MODE", "0") == "1"

# Discord automation (see DISCORD.md)
DISCORD_BOT_TOKEN = _req("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID = _req("DISCORD_GUILD_ID", "")
DISCORD_SUBSCRIBER_ROLE_ID = _req("DISCORD_SUBSCRIBER_ROLE_ID", "")
DISCORD_TEASER_WEBHOOK = _req("DISCORD_TEASER_WEBHOOK", "")
TEASER_SECRET = _req("TEASER_SECRET", "change-me")
# Public invite link shown on the landing page (create one in your server:
# Server Settings -> Invites -> Create Invite, set to never expire)
DISCORD_INVITE_URL = _req("DISCORD_INVITE_URL", "")

# Email (verification + password reset). Resend is the easiest:
# sign up at resend.com, verify your domain (or use their test domain),
# paste the API key. MAIL_PROVIDER=console just prints links to the logs
# (handy for local dev).
MAIL_PROVIDER = _req("MAIL_PROVIDER", "console")
RESEND_API_KEY = _req("RESEND_API_KEY", "")
MAIL_FROM = _req("MAIL_FROM", "Threaded <noreply@threaded.com>")

# Admin panel: comma-separated emails, e.g. "you@mail.com,friend@mail.com"
ADMIN_EMAILS = [e.strip().lower() for e in _req("ADMIN_EMAILS", "").split(",") if e.strip()]

APP_URL = _req("APP_URL", "http://127.0.0.1:5000")  # set to your Render URL in prod
