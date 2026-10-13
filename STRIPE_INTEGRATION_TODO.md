# STRIPE_INTEGRATION_TODO.md — Threaded embedded checkout

Single source of truth for the Stripe Checkout integration
(embedded custom payment form, configured via Checkout Studio).

## Values to Replace

The following values are placeholders or environment-dependent and must be
verified before going live.

**Files containing placeholders:**
- [billing_routes.py](billing_routes.py)
- [templates/billing.html](templates/billing.html)
- [config.py](config.py)

| Field | Current Value | What to Set |
|-------|--------------|-------------|
| `STRIPE_SECRET_KEY` (env) | unset | Secret key from Stripe Dashboard → Developers → API keys (`sk_test_…` for testing, `sk_live_…` for production). |
| `STRIPE_PUBLISHABLE_KEY` (env) | unset | Publishable key from the same page (`pk_test_…` / `pk_live_…`). Now loaded by `billing.html` for the embedded form — previously unused. |
| `STRIPE_PRICE_ID` (env) | unset | Monthly recurring Price ID (`price_…`) from Product catalogue → Threaded Pro. |
| `STRIPE_PRICE_ID_YEARLY` (env) | unset | Yearly recurring Price ID (`price_…`). |
| `STRIPE_PRICE_ID_DAILY` (env) | unset | Daily recurring Price ID (`price_…`). |
| `STRIPE_WEBHOOK_SECRET` (env) | unset | Signing secret (`whsec_…`) from Developers → Webhooks → the `…/api/stripe/webhook` endpoint. |
| `ui_mode` | `"form"` | Defaulted to `"form"` because the Studio versioning rule references Node SDK 21.0.0, which has no Python-SDK equivalent — the Python SDK version could not be mapped. If the embedded form fails to initialize, try `"custom"` here. |
| API version pin | `2026-03-25.dahlia; custom_checkout_payment_form_preview=v1` | Beta preview flag required by the dahlia checkout form. Re-verify against current Stripe docs periodically — beta flags can expire or change. |

## Configured Parameters

These parameters were configured in Checkout Studio and are already set correctly.

**Files containing these parameters:**
- [billing_routes.py](billing_routes.py) (`/subscribe` → `stripe.checkout.Session.create`)

| Parameter | Value |
|-----------|-------|
| ui_mode | form |
| mode | subscription |
| billing_address_collection | auto |
| phone_number_collection | {"enabled": false} |
| automatic_tax | {"enabled": false} |
| payment_method_collection | always |
| submit_type | auto |
| integration_identifier | custom_embedded_web_0001 |
| line_items | app's real Price IDs per plan (daily / monthly / yearly) |

## Setup and next steps

### Environment variables (Render → Environment)
`STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_PRICE_ID`,
`STRIPE_PRICE_ID_YEARLY`, `STRIPE_PRICE_ID_DAILY`, `STRIPE_WEBHOOK_SECRET`.
Optional display overrides: `PLAN_PRICE_DISPLAY`, `PLAN_PRICE_DISPLAY_YEARLY`,
`PLAN_PRICE_DISPLAY_DAILY`, `PLAN_NAME`.

### How the integration works
1. Logged-in user opens `/billing`, picks a plan → `startCheckout(plan)` in
   `billing.html` POSTs JSON to `/subscribe`.
2. `/subscribe` creates a Checkout Session with `ui_mode="form"` and returns
   `{client_secret}` as JSON (no redirect).
3. The page initializes `stripe.initCheckoutFormSdk` (dahlia build of Stripe.js)
   and mounts the secure payment form inline in `#checkout-form`.
4. On confirm, the SDK processes the payment; the page redirects to
   `/billing?subscribed=1`.
5. Fulfillment is via webhook: `checkout.session.completed` →
   `billing_routes.stripe_webhook` activates the subscription and grants the
   Discord Subscriber role. `/billing` also opportunistically syncs from Stripe
   in case the webhook lags.

### Project structure
- `billing_routes.py` — `/billing` page, `/subscribe` (JSON session endpoint),
  `/billing/portal` (customer portal), `/api/stripe/webhook` (fulfillment).
- `templates/billing.html` — plan cards + embedded Stripe form + confirm wiring.
- `config.py` — all Stripe settings from env vars.

### Testing
- Use **test mode** keys/prices/webhook first. Test card: `4242 4242 4242 4242`
  (any future expiry, any CVC).
- Flow: sign up → `/billing` → pick Daily → complete the embedded form →
  redirected to `/billing?subscribed=1` with Pro active; dashboard unlocks.
- Check Stripe Dashboard → Developers → Webhooks that
  `checkout.session.completed` was delivered (200).
- Repeat the whole setup with **live** keys/prices/webhook before taking real
  payments. Test and live Price IDs are different.

### Next steps
- Decide the monthly price (yearly = $300, daily = $10 already chosen) and set
  `PLAN_PRICE_DISPLAY` to match.
- If the dahlia beta ends, remove the `betas` flag and the API-version pin and
  re-test the form.
- Fulfillment for plan changes/upgrades is handled by
  `customer.subscription.updated`; downgrades/cancels via the customer portal.

### Resources
- https://support.stripe.com and https://docs.stripe.com/mcp
- Embedded checkout docs: https://docs.stripe.com/checkout/embedded
