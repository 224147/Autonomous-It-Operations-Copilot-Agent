"""Authentication: local JWT login, token refresh/revocation, login throttling and Azure Entra ID SSO."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from sqlalchemy import func, select

from backend.config import Settings
from backend.db import Database
from backend.kv import KeyValueStore
from backend.models import User, utcnow
from backend.schemas import TokenPair
from backend.security import (
    ROLE_RANK,
    TokenError,
    create_token,
    decode_token,
    hash_password,
    validate_password_strength,
    verify_password,
)

logger = logging.getLogger(__name__)


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthService:
    def __init__(self, settings: Settings, db: Database, kv: KeyValueStore,
                 entra_key_resolver: Callable[[str], Any] | None = None) -> None:
        self._s, self._db, self._kv = settings, db, kv
        self._entra_key_resolver = entra_key_resolver

    # ------------------------------------------------------------------ bootstrap
    def bootstrap_admin(self) -> None:
        email = self._s.bootstrap_admin_email.strip().lower()
        password = self._s.bootstrap_admin_password.get_secret_value()
        if not email or not password:
            return
        with self._db.session() as session:
            if session.scalar(select(func.count(User.id))):
                return
            validate_password_strength(password)
            session.add(User(email=email, full_name="Administrator", hashed_password=hash_password(password), role="admin"))
        logger.info("Bootstrap administrator %s created", email)

    # ------------------------------------------------------------------ local login
    def _fail_key(self, email: str) -> str:
        return f"login:fail:{email}"

    def authenticate(self, email: str, password: str) -> User:
        email = email.strip().lower()
        key = self._fail_key(email)
        failures = self._kv.get_json(key) or 0
        if int(failures) >= self._s.login_max_attempts:
            raise AuthError("Too many failed attempts; try again later", 429)
        with self._db.session() as session:
            user = session.scalar(select(User).where(User.email == email))
            if user is not None and user.auth_provider != "local":
                user = None  # SSO accounts cannot use password login
            valid = verify_password(password, user.hashed_password if user else None)
            if not (user and valid and user.is_active):
                self._kv.incr(key, ttl=self._s.login_lockout_minutes * 60)
                raise AuthError("Invalid credentials")
            user.last_login_at = utcnow()
            session.flush()
            session.expunge(user)
        self._kv.delete(key)
        return user

    # ------------------------------------------------------------------ tokens
    def issue_tokens(self, user: User) -> TokenPair:
        secret, alg = self._s.jwt_secret_value, self._s.jwt_algorithm
        access, _, _ = create_token(str(user.id), user.role, secret, alg, timedelta(minutes=self._s.access_token_ttl_minutes))
        refresh, _, _ = create_token(str(user.id), user.role, secret, alg, timedelta(days=self._s.refresh_token_ttl_days), "refresh")
        return TokenPair(access_token=access, refresh_token=refresh, expires_in=self._s.access_token_ttl_minutes * 60)

    def _revoked(self, jti: str) -> bool:
        return self._kv.get_json(f"revoked:{jti}") is not None

    def revoke(self, payload: dict[str, Any]) -> None:
        ttl = max(1, int(payload["exp"] - datetime.now(UTC).timestamp()))
        self._kv.set_json(f"revoked:{payload['jti']}", 1, ttl=ttl)

    def refresh(self, refresh_token: str) -> TokenPair:
        try:
            payload = decode_token(refresh_token, self._s.jwt_secret_value, self._s.jwt_algorithm, "refresh")
        except TokenError as exc:
            raise AuthError("Invalid refresh token") from exc
        if self._revoked(payload["jti"]):
            raise AuthError("Refresh token has been revoked")
        user = self.get_user(int(payload["sub"]))
        if user is None or not user.is_active:
            raise AuthError("User is inactive")
        self.revoke(payload)  # rotation: a refresh token can be used once
        return self.issue_tokens(user)

    def logout(self, access_payload: dict[str, Any], refresh_token: str | None = None) -> None:
        self.revoke(access_payload)
        if refresh_token:
            try:
                self.revoke(decode_token(refresh_token, self._s.jwt_secret_value, self._s.jwt_algorithm, "refresh"))
            except TokenError:
                pass

    def get_user(self, user_id: int) -> User | None:
        with self._db.session() as session:
            user = session.get(User, user_id)
            if user:
                session.expunge(user)
            return user

    def user_from_access_token(self, token: str) -> tuple[User, dict[str, Any]]:
        try:
            payload = decode_token(token, self._s.jwt_secret_value, self._s.jwt_algorithm, "access")
        except TokenError as exc:
            raise AuthError("Invalid or expired token") from exc
        if self._revoked(payload["jti"]):
            raise AuthError("Token has been revoked")
        user = self.get_user(int(payload["sub"]))
        if user is None or not user.is_active:
            raise AuthError("User is inactive")
        return user, payload

    # ------------------------------------------------------------------ Azure Entra ID
    @property
    def entra_enabled(self) -> bool:
        return bool(self._s.entra_tenant_id and self._s.entra_client_id)

    def _entra_signing_key(self, token: str) -> Any:
        if self._entra_key_resolver:
            return self._entra_key_resolver(token)
        client = jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{self._s.entra_tenant_id}/discovery/v2.0/keys", cache_keys=True, lifespan=3600
        )
        return client.get_signing_key_from_jwt(token).key

    def _map_role(self, claims: dict[str, Any]) -> str:
        try:
            mapping: dict[str, str] = json.loads(self._s.entra_role_map) if self._s.entra_role_map else {}
        except ValueError:
            logger.error("ENTRA_ROLE_MAP is not valid JSON; using default role")
            mapping = {}
        values = list(claims.get("groups") or []) + list(claims.get("roles") or [])
        roles = [mapping[v] for v in values if mapping.get(v) in ROLE_RANK]
        default = self._s.entra_default_role if self._s.entra_default_role in ROLE_RANK else "viewer"
        return max(roles, key=lambda r: ROLE_RANK[r]) if roles else default

    def entra_login(self, id_token: str) -> User:
        if not self.entra_enabled:
            raise AuthError("Azure Entra ID SSO is not configured", 501)
        tenant = self._s.entra_tenant_id
        try:
            claims = jwt.decode(
                id_token, self._entra_signing_key(id_token), algorithms=["RS256"], audience=self._s.entra_client_id,
                options={"require": ["exp", "iss", "aud"]},
            )
        except Exception as exc:
            raise AuthError("Invalid Entra ID token") from exc
        if claims.get("iss") not in (f"https://login.microsoftonline.com/{tenant}/v2.0", f"https://sts.windows.net/{tenant}/"):
            raise AuthError("Token issuer does not match the configured tenant")
        if claims.get("tid") and claims["tid"] != tenant:
            raise AuthError("Token tenant mismatch")
        email = str(claims.get("email") or claims.get("preferred_username") or claims.get("upn") or "").lower()
        if not email:
            raise AuthError("Token does not contain an email/username claim")
        role = self._map_role(claims)
        with self._db.session() as session:
            user = session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(email=email, full_name=str(claims.get("name") or ""), role=role, auth_provider="entra", is_active=True)
                session.add(user)
            elif user.auth_provider != "entra":
                raise AuthError("A local account with this email already exists", 409)
            if not user.is_active:
                raise AuthError("User is inactive", 403)
            user.role = role  # the identity provider is the source of truth for role assignments
            user.last_login_at = utcnow()
            session.flush()
            session.expunge(user)
        return user
