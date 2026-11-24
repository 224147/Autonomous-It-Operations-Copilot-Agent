"""FastAPI dependencies: container access, authentication and RBAC."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.container import Container
from backend.models import User
from backend.security import ROLES, constant_time_in, has_role
from services.auth_service import AuthError

bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    user: User
    token: dict[str, Any]
    ip: str

    @property
    def email(self) -> str:
        return self.user.email

    @property
    def role(self) -> str:
        return self.user.role


def get_container(request: Request) -> Container:
    return request.app.state.container


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:  # the ingress/NGINX appends the real client address last
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else ""


def current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    container: Container = Depends(get_container),
) -> Principal:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    try:
        user, payload = container.auth.user_from_access_token(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
    return Principal(user=user, token=payload, ip=client_ip(request))


def require_role(minimum: str) -> Callable[..., Principal]:
    assert minimum in ROLES

    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not has_role(principal.role, minimum):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role '{minimum}' or higher")
        return principal

    return dependency


viewer = require_role("viewer")
operator = require_role("operator")
approver = require_role("approver")
admin = require_role("admin")


def ingest_principal(
    request: Request,
    x_api_key: str | None = Header(default=None),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    container: Container = Depends(get_container),
) -> str:
    """Collectors authenticate with an ingest API key; humans/other services with an operator+ JWT."""
    keys = container.settings.ingest_key_list
    if x_api_key and keys and constant_time_in(x_api_key, keys):
        return "ingest-key"
    # Alertmanager and similar webhook senders can only set an Authorization header
    if credentials is not None and keys and constant_time_in(credentials.credentials, keys):
        return "ingest-key"
    if credentials is not None:
        try:
            user, _ = container.auth.user_from_access_token(credentials.credentials)
        except AuthError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
        if has_role(user.role, "operator"):
            return user.email
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Requires role 'operator' or higher")
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Provide X-API-Key or a bearer token")
