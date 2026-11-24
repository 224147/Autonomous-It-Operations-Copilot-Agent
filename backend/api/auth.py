"""Authentication, user management and audit endpoints."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.container import Container
from backend.deps import Principal, admin, client_ip, current_principal, get_container
from backend.schemas import (
    AuditOut,
    EntraLoginRequest,
    LoginRequest,
    PasswordChange,
    RefreshRequest,
    TokenPair,
    UserCreate,
    UserOut,
    UserUpdate,
)
from services.auth_service import AuthError
from services.user_service import UserError

router = APIRouter(tags=["auth"])


@router.get("/auth/config", summary="Public sign-in configuration")
def auth_config(container: Container = Depends(get_container)) -> dict:
    s = container.settings
    return {"local_login": True, "entra_enabled": container.auth.entra_enabled,
            "entra_tenant_id": s.entra_tenant_id if container.auth.entra_enabled else None,
            "entra_client_id": s.entra_client_id if container.auth.entra_enabled else None}


@router.post("/auth/login", response_model=TokenPair)
def login(body: LoginRequest, request: Request, container: Container = Depends(get_container)) -> TokenPair:
    ip = client_ip(request)
    try:
        user = container.auth.authenticate(body.email, body.password)
    except AuthError as exc:
        container.audit.log("auth.login", body.email, resource_type="user", outcome="failure", ip=ip,
                            details={"reason": str(exc)})
        raise HTTPException(exc.status_code, str(exc)) from exc
    container.audit.log("auth.login", user.email, user.role, "user", user.id, ip=ip)
    return container.auth.issue_tokens(user)


@router.post("/auth/sso/entra", response_model=TokenPair, summary="Exchange an Azure Entra ID id_token for platform tokens")
def entra_login(body: EntraLoginRequest, request: Request, container: Container = Depends(get_container)) -> TokenPair:
    ip = client_ip(request)
    try:
        user = container.auth.entra_login(body.id_token)
    except AuthError as exc:
        container.audit.log("auth.sso_login", "unknown", resource_type="user", outcome="failure", ip=ip,
                            details={"reason": str(exc)})
        raise HTTPException(exc.status_code, str(exc)) from exc
    container.audit.log("auth.sso_login", user.email, user.role, "user", user.id, ip=ip)
    return container.auth.issue_tokens(user)


@router.post("/auth/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, container: Container = Depends(get_container)) -> TokenPair:
    try:
        return container.auth.refresh(body.refresh_token)
    except AuthError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: RefreshRequest | None = None, principal: Principal = Depends(current_principal),
           container: Container = Depends(get_container)) -> None:
    container.auth.logout(principal.token, body.refresh_token if body else None)
    container.audit.log("auth.logout", principal.email, principal.role, "user", principal.user.id, ip=principal.ip)


@router.get("/auth/me", response_model=UserOut)
def me(principal: Principal = Depends(current_principal)) -> UserOut:
    return UserOut.model_validate(principal.user)


@router.post("/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: PasswordChange, principal: Principal = Depends(current_principal),
                    container: Container = Depends(get_container)) -> None:
    try:
        container.users.change_password(principal.user.id, body.current_password, body.new_password)
    except (UserError, ValueError) as exc:
        raise HTTPException(getattr(exc, "status_code", 400), str(exc)) from exc
    container.audit.log("user.password_changed", principal.email, principal.role, "user", principal.user.id, ip=principal.ip)


# ------------------------------------------------------------------ user management (admin)
@router.get("/users", response_model=list[UserOut])
def list_users(_: Principal = Depends(admin), container: Container = Depends(get_container)):
    return container.users.list()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, principal: Principal = Depends(admin), container: Container = Depends(get_container)):
    try:
        user = container.users.create(body)
    except (UserError, ValueError) as exc:
        raise HTTPException(getattr(exc, "status_code", 422), str(exc)) from exc
    container.audit.log("user.created", principal.email, principal.role, "user", user.id,
                        {"email": user.email, "role": user.role}, ip=principal.ip)
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, principal: Principal = Depends(admin),
                container: Container = Depends(get_container)):
    try:
        user = container.users.update(user_id, body, principal.user.id)
    except (UserError, ValueError) as exc:
        raise HTTPException(getattr(exc, "status_code", 422), str(exc)) from exc
    container.audit.log("user.updated", principal.email, principal.role, "user", user_id,
                        body.model_dump(exclude_none=True, exclude={"password"}), ip=principal.ip)
    return user


# ------------------------------------------------------------------ audit
@router.get("/audit", response_model=dict, summary="Audit trail (admin only)")
def audit_logs(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), actor: str | None = None,
               action: str | None = None, since: datetime | None = None, _: Principal = Depends(admin),
               container: Container = Depends(get_container)):
    rows, total = container.audit.list(limit, offset, actor, action, since)
    return {"items": [AuditOut.model_validate(r).model_dump(mode="json") for r in rows], "total": total,
            "limit": limit, "offset": offset}


@router.get("/audit/verify", summary="Verify audit-log hash chain integrity")
def audit_verify(_: Principal = Depends(admin), container: Container = Depends(get_container)) -> dict:
    return container.audit.verify_chain()

