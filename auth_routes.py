"""Signup / login / logout / verification / password reset / account."""
from flask import Blueprint, render_template, request, redirect, url_for
from flask_login import login_user, logout_user, login_required, current_user

import config
from models import db, User
from email_service import send_verification, send_password_reset

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("billing.billing"))
    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        pw = request.form.get("password") or ""
        discord_username = (request.form.get("discord_username") or "").strip() or None
        if not email or "@" not in email:
            error = "Enter a valid email."
        elif len(pw) < 8:
            error = "Password needs at least 8 characters."
        elif User.query.filter_by(email=email).first():
            error = "That email already has an account — log in instead."
        else:
            u = User(email=email, discord_username=discord_username)
            u.set_password(pw)
            u.email_verified = True  # verification removed in v15: active on signup
            db.session.add(u)
            db.session.commit()
            login_user(u)
            return redirect(url_for("billing.billing"))
    return render_template("signup.html", error=error)


@auth_bp.route("/verify/<token>")
def verify(token):
    u = User.query.filter_by(verify_token=token).first()
    if u and User._token_valid(u.verify_token, u.verify_token_expires, token):
        u.email_verified = True
        u.verify_token = None
        u.verify_token_expires = None
        db.session.commit()
        return render_template("verified.html")
    return render_template("verify_pending.html",
                           error="That link is invalid or expired.",
                           email=current_user.email if current_user.is_authenticated else "")


@auth_bp.route("/verify-pending")
@login_required
def verify_pending():
    if current_user.email_verified:
        return redirect("/app")
    return render_template("verify_pending.html", email=current_user.email)


@auth_bp.route("/resend-verification", methods=["POST"])
@login_required
def resend_verification():
    if not current_user.email_verified:
        try:
            send_verification(current_user)
            db.session.commit()
        except Exception as e:
            print(f"[MAIL] resend-verification failed: {e}", flush=True)
            db.session.rollback()
            return render_template("verify_pending.html", email=current_user.email,
                                   error="Couldn't send the email — Resend may not be configured correctly.")
    return redirect(url_for("auth.verify_pending"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect("/app")
    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        pw = request.form.get("password") or ""
        u = User.query.filter_by(email=email).first()
        if not u or not u.check_password(pw):
            error = "Wrong email or password."
        else:
            login_user(u)
            nxt = request.args.get("next") or "/app"
            return redirect(nxt if nxt.startswith("/") else "/app")
    return render_template("login.html", error=error)


@auth_bp.route("/logout")
def logout():
    logout_user()
    return redirect("/")


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    sent = False
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        u = User.query.filter_by(email=email).first()
        if u:
            try:
                send_password_reset(u)
                db.session.commit()
            except Exception as e:
                print(f"[MAIL] password-reset failed for {email}: {e}", flush=True)
                db.session.rollback()
        sent = True  # same message either way: no account enumeration
    return render_template("forgot.html", sent=sent)


@auth_bp.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    u = User.query.filter_by(reset_token=token).first()
    valid = bool(u and User._token_valid(u.reset_token, u.reset_token_expires, token))
    error = None
    if request.method == "POST" and valid:
        pw = request.form.get("password") or ""
        if len(pw) < 8:
            error = "Password needs at least 8 characters."
        else:
            u.set_password(pw)
            u.reset_token = None
            u.reset_token_expires = None
            db.session.commit()
            return render_template("reset_done.html")
    return render_template("reset.html", valid=valid, error=error)


@auth_bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    msg, error = None, None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "password":
            cur = request.form.get("current_password") or ""
            new = request.form.get("new_password") or ""
            if not current_user.check_password(cur):
                error = "Current password is wrong."
            elif len(new) < 8:
                error = "New password needs at least 8 characters."
            else:
                current_user.set_password(new)
                db.session.commit()
                msg = "Password changed. ✅"
        elif action == "discord":
            current_user.discord_username = (request.form.get("discord_username") or "").strip() or None
            db.session.commit()
            msg = "Discord username saved. ✅"
    return render_template("account.html", msg=msg, error=error)
