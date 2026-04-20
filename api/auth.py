"""JWT 기반 auth 미들웨어 + 라우트.

- /api/auth/login       : 이메일+비번 → JWT 토큰
- /api/auth/me          : 토큰 검증 → 현재 유저
- /api/admin/users      : admin 전용 유저 관리

토큰 시크릿은 config/.env 의 JWT_SECRET 에서 로드. 없으면 런타임 생성 경고.
"""
from __future__ import annotations

import os
import secrets
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import jwt
from flask import Blueprint, current_app, jsonify, request

from agents.db.users import UsersDB


JWT_ALGO = "HS256"
JWT_TTL_SECONDS = 60 * 60 * 24 * 7  # 7일


def _load_jwt_secret() -> str:
    env_path = Path(__file__).parent.parent / "config" / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("JWT_SECRET="):
                return line.split("=", 1)[1].strip()
    secret = os.environ.get("JWT_SECRET")
    if secret:
        return secret
    secret = secrets.token_urlsafe(48)
    print(f"[auth] WARN: JWT_SECRET not set — generated ephemeral secret (will invalidate on restart)")
    return secret


JWT_SECRET = _load_jwt_secret()


def _issue_token(email: str, role: str) -> str:
    now = int(time.time())
    payload = {
        "sub": email,
        "role": role,
        "iat": now,
        "exp": now + JWT_TTL_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def _decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        return None


def _extract_token() -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()
    return None


def require_auth(role: str | None = None):
    """라우트 데코레이터. role='admin' 지정시 admin 만 허용."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            token = _extract_token()
            if not token:
                return jsonify({"error": "missing token", "code": "AUTH_MISSING"}), 401
            payload = _decode_token(token)
            if payload is None:
                return jsonify({"error": "invalid token", "code": "AUTH_INVALID"}), 401
            if role == "admin" and payload.get("role") != "admin":
                return jsonify({"error": "admin required", "code": "AUTH_FORBIDDEN"}), 403
            request.user = payload  # type: ignore[attr-defined]
            return fn(*args, **kwargs)
        return wrapper
    return deco


def build_auth_blueprint(users_db: UsersDB) -> Blueprint:
    bp = Blueprint("auth", __name__)

    @bp.post("/api/auth/login")
    def login():
        body = request.get_json(silent=True) or {}
        email = (body.get("email") or "").strip()
        password = body.get("password") or ""
        if not email or not password:
            return jsonify({"error": "email and password required", "code": "AUTH_MISSING_FIELDS"}), 400
        user = users_db.verify_password(email, password)
        if user is None:
            return jsonify({"error": "invalid credentials", "code": "AUTH_INVALID"}), 401
        token = _issue_token(user["email"], user["role"])
        return jsonify({"token": token, "user": user})

    @bp.get("/api/auth/me")
    @require_auth()
    def me():
        payload = request.user  # type: ignore[attr-defined]
        user = users_db.get_user(payload["sub"])
        if user is None:
            return jsonify({"error": "user not found", "code": "AUTH_NOT_FOUND"}), 404
        return jsonify({"user": user})

    @bp.patch("/api/auth/me/password")
    @require_auth()
    def change_my_password():
        payload = request.user  # type: ignore[attr-defined]
        body = request.get_json(silent=True) or {}
        new_password = body.get("newPassword") or ""
        if len(new_password) < 4:
            return jsonify({"error": "password must be >= 4 chars", "code": "AUTH_PW_TOO_SHORT"}), 400
        users_db.update_password(payload["sub"], new_password)
        return jsonify({"ok": True})

    @bp.get("/api/admin/users")
    @require_auth(role="admin")
    def list_users():
        return jsonify({"users": users_db.list_users()})

    @bp.post("/api/admin/users")
    @require_auth(role="admin")
    def create_user():
        body = request.get_json(silent=True) or {}
        email = (body.get("email") or "").strip()
        password = body.get("password") or ""
        role = body.get("role") or "user"
        try:
            user = users_db.create_user(email, password, role)
        except ValueError as e:
            return jsonify({"error": str(e), "code": "AUTH_INVALID_INPUT"}), 400
        return jsonify({"user": user}), 201

    @bp.delete("/api/admin/users/<email>")
    @require_auth(role="admin")
    def delete_user(email: str):
        try:
            users_db.delete_user(email)
        except ValueError as e:
            return jsonify({"error": str(e), "code": "AUTH_INVALID_OP"}), 400
        return jsonify({"ok": True})

    return bp
