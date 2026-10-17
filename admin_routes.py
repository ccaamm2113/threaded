"""Admin panel: user overview + manual subscription control.

Access: login required AND email listed in ADMIN_EMAILS (config).
"""
from flask import Blueprint, render_template, request, redirect, url_for, abort
from flask_login import current_user

import config
from models import db, User

admin_bp = Blueprint("admin", __name__)


def _admin_required():
    """Returns a redirect response when access should be rerouted, else None.
    Raises 403 for logged-in non-admins. DEV_MODE bypasses everything."""
    if config.DEV_MODE:
        return None
    if not current_user.is_authenticated:
        return redirect("/login?next=/admin")
    if current_user.email.lower() not in config.ADMIN_EMAILS:
        abort(403)
    return None


@admin_bp.route("/admin")
def admin():
    r = _admin_required()
    if r:
        return r
    users = User.query.order_by(User.created_at.desc()).all()
    stats = {
        "total": len(users),
        "verified": sum(1 for u in users if u.email_verified),
        "subscribers": sum(1 for u in users if u.is_subscriber),
    }
    return render_template("admin.html", users=users, stats=stats)


@admin_bp.route("/admin/toggle-sub/<int:uid>", methods=["POST"])
def toggle_sub(uid):
    r = _admin_required()
    if r:
        return r
    u = User.query.get_or_404(uid)
    u.is_subscriber = not u.is_subscriber
    db.session.commit()
    return redirect(url_for("admin.admin"))


@admin_bp.route("/admin/delete/<int:uid>", methods=["POST"])
def delete_user(uid):
    r = _admin_required()
    if r:
        return r
    u = User.query.get_or_404(uid)
    if u.id == current_user.id:
        return redirect(url_for("admin.admin"))  # don't delete yourself
    db.session.delete(u)
    db.session.commit()
    return redirect(url_for("admin.admin"))


@admin_bp.route("/admin/resend-verify/<int:uid>", methods=["POST"])
def resend_verify(uid):
    r = _admin_required()
    if r:
        return r
    u = User.query.get_or_404(uid)
    if not u.email_verified:
        from email_service import send_verification
        try:
            send_verification(u)
            db.session.commit()
        except Exception:
            db.session.rollback()
    return redirect(url_for("admin.admin"))
