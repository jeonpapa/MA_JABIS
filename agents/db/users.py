"""v2 유저 인증 DB.

admin 이 email+password 로 계정 발급 → 팀원에게 credential 전달.
localStorage 기반 목업 로직을 서버 측 bcrypt 해시로 교체.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import bcrypt


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    email         TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);
"""


def _load_admin_env() -> tuple[str, str]:
    """config/.env → env var 순으로 기본 admin credential 로드."""
    env: dict[str, str] = {}
    env_path = Path(__file__).resolve().parents[2] / "config" / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    email = os.environ.get("ADMIN_EMAIL") or env.get("ADMIN_EMAIL") or "admin@marketintel.kr"
    password = os.environ.get("ADMIN_PASSWORD") or env.get("ADMIN_PASSWORD") or "admin1234"
    return email, password


DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD = _load_admin_env()


class UsersDB:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._ensure_default_admin()

    def _ensure_default_admin(self) -> None:
        cur = self._conn.execute(
            "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
        )
        if cur.fetchone() is None:
            self.create_user(
                DEFAULT_ADMIN_EMAIL,
                DEFAULT_ADMIN_PASSWORD,
                role="admin",
            )

    def create_user(self, email: str, password: str, role: str = "user") -> dict:
        email = email.strip().lower()
        if not email or "@" not in email:
            raise ValueError("invalid email")
        if len(password) < 4:
            raise ValueError("password must be >= 4 chars")
        if role not in ("admin", "user"):
            raise ValueError("invalid role")
        pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        now = datetime.utcnow().isoformat(timespec="seconds")
        try:
            self._conn.execute(
                "INSERT INTO users(email, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (email, pw_hash, role, now),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError("email already exists")
        return self.get_user(email)  # type: ignore[return-value]

    def verify_password(self, email: str, password: str) -> Optional[dict]:
        email = email.strip().lower()
        row = self._conn.execute(
            "SELECT email, password_hash, role, created_at FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        if row is None:
            return None
        if not bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("utf-8")):
            return None
        self._conn.execute(
            "UPDATE users SET last_login_at = ? WHERE email = ?",
            (datetime.utcnow().isoformat(timespec="seconds"), email),
        )
        self._conn.commit()
        return {"email": row["email"], "role": row["role"], "createdAt": row["created_at"]}

    def update_password(self, email: str, new_password: str) -> None:
        if len(new_password) < 4:
            raise ValueError("password must be >= 4 chars")
        pw_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        self._conn.execute(
            "UPDATE users SET password_hash = ? WHERE email = ?",
            (pw_hash, email.strip().lower()),
        )
        self._conn.commit()

    def delete_user(self, email: str) -> None:
        email = email.strip().lower()
        row = self._conn.execute("SELECT role FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            return
        if row["role"] == "admin":
            raise ValueError("cannot delete admin")
        self._conn.execute("DELETE FROM users WHERE email = ?", (email,))
        self._conn.commit()

    def list_users(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT email, role, created_at, last_login_at FROM users ORDER BY created_at"
        ).fetchall()
        return [
            {
                "email": r["email"],
                "role": r["role"],
                "createdAt": r["created_at"],
                "lastLoginAt": r["last_login_at"],
            }
            for r in rows
        ]

    def get_user(self, email: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT email, role, created_at, last_login_at FROM users WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
        if row is None:
            return None
        return {
            "email": row["email"],
            "role": row["role"],
            "createdAt": row["created_at"],
            "lastLoginAt": row["last_login_at"],
        }
