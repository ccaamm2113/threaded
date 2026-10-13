"""User accounts for Threaded."""
from datetime import datetime, timedelta
import secrets

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def _token():
    return secrets.token_urlsafe(32)


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    discord_username = db.Column(db.String(64), nullable=True)  # for the Subscriber role
    stripe_customer_id = db.Column(db.String(64), nullable=True)
    stripe_subscription_id = db.Column(db.String(64), nullable=True)
    is_subscriber = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    # email verification
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    verify_token = db.Column(db.String(64), nullable=True, index=True)
    verify_token_expires = db.Column(db.DateTime, nullable=True)
    # password reset
    reset_token = db.Column(db.String(64), nullable=True, index=True)
    reset_token_expires = db.Column(db.DateTime, nullable=True)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    def new_verify_token(self, hours=24):
        self.verify_token = _token()
        self.verify_token_expires = datetime.utcnow() + timedelta(hours=hours)
        return self.verify_token

    def new_reset_token(self, hours=1):
        self.reset_token = _token()
        self.reset_token_expires = datetime.utcnow() + timedelta(hours=hours)
        return self.reset_token

    @staticmethod
    def _token_valid(stored, expires, given):
        return (stored and expires and stored == given
                and datetime.utcnow() < expires)
