"""Password hashing, JWT handling and RBAC."""
from __future__ import annotations

import hmac
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

ROLES = ("viewer", "operator", "approver", "admin")  # ascending privilege
ROLE_RANK = {role: idx for idx, role in enumerate(ROLES)}
MAX_PASSWORD_BYTES = 72  # bcrypt limit

_PASSWORD_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{10,}$")


class TokenError(Exception):
    pass


def validate_password_strength(password: str) -> None:
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        raise ValueError("Password must be at most 72 bytes")
    if not _PASSWORD_RE.match(password):
        raise ValueError("Password must be at least 10 characters and contain letters and digits")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:MAX_PASSWORD_BYTES], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        # keep timing roughly constant for unknown users
        bcrypt.checkpw(b"x", bcrypt.hashpw(b"y", bcrypt.gensalt(4)))
        return False
    try:
        return bcrypt.checkpw(password.encode()[:MAX_PASSWORD_BYTES], hashed.encode())
    except ValueError:
        return False


def create_token(
    subject: str, role: str, secret: str, algorithm: str, ttl: timedelta, token_type: str = "access"  # noqa: S107
) -> tuple[str, str, datetime]:
    now = datetime.now(UTC)
    expires = now + ttl
    jti = uuid.uuid4().hex
    payload = {"sub": subject, "role": role, "type": token_type, "jti": jti, "iat": now, "exp": expires}
    return jwt.encode(payload, secret, algorithm=algorithm), jti, expires


def decode_token(token: str, secret: str, algorithm: str, expected_type: str = "access") -> dict[str, Any]:
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm], options={"require": ["exp", "sub", "jti"]})
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("type") != expected_type:
        raise TokenError("wrong token type")
    return payload


def has_role(user_role: str, required: str) -> bool:
    return ROLE_RANK.get(user_role, -1) >= ROLE_RANK[required]


def constant_time_in(value: str, candidates: list[str]) -> bool:
    ok = False
    for candidate in candidates:
        ok |= hmac.compare_digest(value.encode(), candidate.encode())
    return ok
