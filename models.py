"""
Data models and in-memory store for BTC 15M Kalshi Platform.
"""

from typing import Dict, Any, Optional
import time

class UserStore:
    def __init__(self):
        self.users = {}
        self.sessions = {}

    def create_user(self, email: str, name: str, password_hash: str) -> Dict[str, Any]:
        user = {
            "email": email.lower().strip(),
            "name": name.strip(),
            "password_hash": password_hash,
            "created_at": time.time(),
            "plan": "pro_scalper",
            "is_admin": False
        }
        self.users[user["email"]] = user
        return user

    def get_user(self, email: str) -> Optional[Dict[str, Any]]:
        return self.users.get(email.lower().strip())

user_store = UserStore()
