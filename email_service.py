"""Email sending for verification + password reset.

MAIL_PROVIDER=resend -> sends via Resend's HTTP API (needs RESEND_API_KEY).
MAIL_PROVIDER=console (default) -> prints the link to the server log instead.
Use console for local dev; resend in production.
"""
import requests

import config


def send_email(to, subject, html):
    if config.MAIL_PROVIDER == "resend" and config.RESEND_API_KEY:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
            json={"from": config.MAIL_FROM, "to": [to],
                  "subject": subject, "html": html},
            timeout=15)
        try:
            r.raise_for_status()
        except requests.HTTPError:
            # Surface Resend's own error message (e.g. bad key, unverified
            # domain) instead of a bare status code, so the [MAIL] log line
            # is actionable.
            body = ""
            try:
                body = r.text[:300]
            except Exception:
                pass
            raise RuntimeError(f"Resend HTTP {r.status_code}: {body}")
        return True
    # console fallback: log it so devs can click it
    print(f"\n[MAIL console] To: {to}\nSubject: {subject}\n{html}\n", flush=True)
    return True


def _link(path):
    return config.APP_URL.rstrip("/") + path


def send_verification(user):
    token = user.new_verify_token()
    link = _link(f"/verify/{token}")
    html = (f"<p>Welcome to <b>Threaded</b>! 🧵</p>"
            f"<p>Confirm your email to activate your account:</p>"
            f"<p><a href='{link}'>{link}</a></p>"
            f"<p>This link expires in 24 hours.</p>")
    return send_email(user.email, "Verify your Threaded account", html)


def send_password_reset(user):
    token = user.new_reset_token()
    link = _link(f"/reset/{token}")
    html = (f"<p>Someone asked to reset the password for <b>{user.email}</b> "
            f"on Threaded. If that was you:</p>"
            f"<p><a href='{link}'>{link}</a></p>"
            f"<p>This link expires in 1 hour. If it wasn't you, ignore this email.</p>")
    return send_email(user.email, "Reset your Threaded password", html)
