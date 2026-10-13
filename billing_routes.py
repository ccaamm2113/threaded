"""Stripe subscriptions + billing portal + webhook."""
import stripe
from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from flask_login import login_required, current_user

import config
from models import db, User

billing_bp = Blueprint("billing", __name__)


def _stripe():
    stripe.api_key = config.STRIPE_SECRET_KEY
    return stripe


def _plans():
    """plan_key -> (stripe price id, display price, blurb)."""
    return {
        "daily": (config.STRIPE_PRICE_ID_DAILY, config.PLAN_PRICE_DISPLAY_DAILY,
                  "per day · cancel anytime"),
        "monthly": (config.STRIPE_PRICE_ID, config.PLAN_PRICE_DISPLAY,
                    "per month · cancel anytime"),
        "yearly": (config.STRIPE_PRICE_ID_YEARLY, config.PLAN_PRICE_DISPLAY_YEARLY,
                   "per year · cancel anytime"),
    }


@billing_bp.route("/billing")
@login_required
def billing():
    # Opportunistic sync: if Stripe shows an active sub, reflect it now
    # (webhooks can lag a few seconds after checkout).
    if config.STRIPE_SECRET_KEY and current_user.stripe_customer_id and not current_user.is_subscriber:
        try:
            _stripe()
            subs = stripe.Subscription.list(customer=current_user.stripe_customer_id,
                                            status="active", limit=1)
            if subs.data:
                current_user.stripe_subscription_id = subs.data[0].id
                current_user.is_subscriber = True
                db.session.commit()
                from discord_integration import handle_new_subscriber
                try:
                    handle_new_subscriber(current_user)
                except Exception:
                    pass
        except Exception:
            pass
    return render_template("billing.html", error=request.args.get("error"),
                           plans=[{"key": k, "price": v[1], "blurb": v[2],
                                   "ready": bool(v[0])}
                                  for k, v in _plans().items()])


@billing_bp.route("/subscribe", methods=["POST"])
@login_required
def subscribe():
    plan = request.form.get("plan", "monthly")
    price_id = _plans().get(plan, (None,))[0]
    if not config.STRIPE_SECRET_KEY or not price_id:
        return redirect(url_for("billing.billing", error="That plan isn't configured yet."))
    _stripe()
    if not current_user.stripe_customer_id:
        cust = stripe.Customer.create(email=current_user.email,
                                      metadata={"user_id": current_user.id})
        current_user.stripe_customer_id = cust.id
        db.session.commit()
    session = stripe.checkout.Session.create(
        customer=current_user.stripe_customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=config.APP_URL.rstrip("/") + "/billing?subscribed=1",
        cancel_url=config.APP_URL.rstrip("/") + "/billing?cancelled=1",
        metadata={"user_id": current_user.id},
    )
    return redirect(session.url, code=303)


@billing_bp.route("/billing/portal", methods=["POST"])
@login_required
def portal():
    if not config.STRIPE_SECRET_KEY or not current_user.stripe_customer_id:
        return redirect(url_for("billing.billing"))
    _stripe()
    ps = stripe.billing_portal.Session.create(
        customer=current_user.stripe_customer_id,
        return_url=config.APP_URL.rstrip("/") + "/billing")
    return redirect(ps.url, code=303)


def _activate(user, subscription_id):
    user.stripe_subscription_id = subscription_id
    user.is_subscriber = True
    db.session.commit()
    from discord_integration import handle_new_subscriber
    try:
        handle_new_subscriber(user)
    except Exception:
        pass


@billing_bp.route("/api/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(
            payload, sig, config.STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        return jsonify({"error": f"bad signature: {e}"}), 400
    et = event["type"]
    data = event["data"]["object"]
    if et == "checkout.session.completed":
        user = User.query.filter_by(stripe_customer_id=data.get("customer")).first()
        if not user and (data.get("metadata") or {}).get("user_id"):
            user = User.query.get(int(data["metadata"]["user_id"]))
        if user:
            _activate(user, data.get("subscription"))
    elif et == "customer.subscription.updated":
        user = User.query.filter_by(stripe_subscription_id=data["id"]).first()
        if user:
            user.is_subscriber = data.get("status") in ("active", "trialing")
            db.session.commit()
    elif et == "customer.subscription.deleted":
        user = User.query.filter_by(stripe_subscription_id=data["id"]).first()
        if user:
            user.is_subscriber = False
            db.session.commit()
    return jsonify({"ok": True})
