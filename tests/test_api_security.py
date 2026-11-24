"""Authentication, RBAC, SSO, audit trail and configuration tests."""
from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.config import Settings
from backend.vault import load_vault_overrides
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login, make_user


def test_health_and_metrics_endpoints(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    ready = client.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["checks"]["database"] is True
    cfg = client.get("/api/v1/auth/config")
    assert cfg.headers["cache-control"] == "no-store" and cfg.headers["x-content-type-options"] == "nosniff"
    assert cfg.headers["x-frame-options"] == "DENY" and cfg.headers["x-request-id"]
    metrics = client.get("/metrics")
    assert metrics.status_code == 200 and 'route="/api/v1/auth/config"' in metrics.text


def test_login_success_and_failure(client):
    assert client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"}).status_code == 401
    headers = login(client)
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert me["email"] == ADMIN_EMAIL and me["role"] == "admin"
    assert "hashed_password" not in me


def test_protected_routes_require_authentication(client):
    assert client.get("/api/v1/incidents").status_code == 401
    assert client.get("/api/v1/incidents", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_login_lockout_after_repeated_failures(client):
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "bad"})
    locked = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert locked.status_code == 429


def test_refresh_token_rotation_and_logout_revocation(client):
    tokens = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}).json()
    new = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert new.status_code == 200
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401  # reuse
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": new.json()["access_token"]}).status_code == 401  # wrong type
    headers = {"Authorization": f"Bearer {new.json()['access_token']}"}
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_rbac_hierarchy(client, admin_headers):
    viewer = make_user(client, admin_headers, "viewer")
    operator = make_user(client, admin_headers, "operator")
    approver = make_user(client, admin_headers, "approver")
    assert client.get("/api/v1/incidents", headers=viewer).status_code == 200
    assert client.post("/api/v1/incidents", headers=viewer, json={"service": "payment-service"}).status_code == 403
    assert client.post("/api/v1/incidents", headers=operator, json={"service": "payment-service"}).status_code == 201
    assert client.post("/api/v1/remediation/approvals/1/decision", headers=operator,
                       json={"decision": "approve"}).status_code == 403
    assert client.post("/api/v1/remediation/approvals/999/decision", headers=approver,
                       json={"decision": "approve"}).status_code == 404  # authorised, but nothing to decide
    assert client.get("/api/v1/users", headers=approver).status_code == 403
    assert client.get("/api/v1/audit", headers=operator).status_code == 403
    assert client.get("/api/v1/users", headers=admin_headers).status_code == 200


def test_user_management_guards(client, admin_headers):
    me = client.get("/api/v1/auth/me", headers=admin_headers).json()
    assert client.patch(f"/api/v1/users/{me['id']}", headers=admin_headers, json={"is_active": False}).status_code == 409
    weak = client.post("/api/v1/users", headers=admin_headers, json={"email": "w@test.example", "password": "short"})
    assert weak.status_code == 422
    nodigits = client.post("/api/v1/users", headers=admin_headers, json={"email": "w@test.example", "password": "onlyletterspassword"})
    assert nodigits.status_code == 422
    first = make_user(client, admin_headers, "viewer", "dup@test.example")
    assert first
    dup = client.post("/api/v1/users", headers=admin_headers, json={"email": "dup@test.example", "password": "UserPass12345"})
    assert dup.status_code == 409


def test_audit_trail_is_hash_chained_and_tamper_evident(client, container, admin_headers):
    make_user(client, admin_headers, "viewer")
    assert client.get("/api/v1/audit/verify", headers=admin_headers).json()["valid"] is True
    logs = client.get("/api/v1/audit", headers=admin_headers).json()
    assert logs["total"] >= 2 and any(item["action"] == "user.created" for item in logs["items"])
    assert "password" not in json.dumps(logs).lower().replace("password_changed", "")

    from sqlalchemy import update

    from backend.models import AuditLog

    with container.db.session() as session:
        session.execute(update(AuditLog).where(AuditLog.id == 1).values(actor="mallory"))
    verdict = client.get("/api/v1/audit/verify", headers=admin_headers).json()
    assert verdict["valid"] is False and verdict["broken_at"] == 1


# ------------------------------------------------------------------ Azure Entra SSO
@pytest.fixture
def entra(tmp_path, settings):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    s = settings.model_copy(update={
        "entra_tenant_id": "tenant-1", "entra_client_id": "client-1",
        "entra_role_map": json.dumps({"group-ops": "operator", "group-admins": "admin"}),
    })
    return key, s


def _id_token(key, **overrides):
    claims = {"iss": "https://login.microsoftonline.com/tenant-1/v2.0", "aud": "client-1", "tid": "tenant-1",
              "exp": int(time.time()) + 600, "preferred_username": "Sso.User@corp.example", "name": "Sso User",
              "groups": ["group-ops"], **overrides}
    return jwt.encode(claims, key, algorithm="RS256")


def test_entra_sso_login_maps_groups_to_roles(entra, tmp_path):
    from fastapi.testclient import TestClient

    from backend.container import build_container
    from backend.db import Database
    from backend.kv import MemoryKV
    from backend.main import create_app

    key, s = entra
    s = s.model_copy(update={"database_url": f"sqlite:///{tmp_path}/sso.db"})
    c = build_container(s, db=Database(s.database_url), kv=MemoryKV(), kb=None, use_llm=False,
                        entra_key_resolver=lambda _t: key.public_key())
    with TestClient(create_app(s, c)) as client:
        assert client.get("/api/v1/auth/config").json()["entra_enabled"] is True
        ok = client.post("/api/v1/auth/sso/entra", json={"id_token": _id_token(key)})
        assert ok.status_code == 200
        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"}).json()
        assert me["email"] == "sso.user@corp.example" and me["role"] == "operator" and me["auth_provider"] == "entra"

        for bad in (_id_token(key, aud="other"), _id_token(key, iss="https://evil.example/v2.0"),
                    _id_token(key, exp=int(time.time()) - 10), _id_token(key, tid="other-tenant"),
                    _id_token(rsa.generate_private_key(public_exponent=65537, key_size=2048))):
            assert client.post("/api/v1/auth/sso/entra", json={"id_token": bad}).status_code == 401
        # SSO accounts can never log in with a password
        assert client.post("/api/v1/auth/login", json={"email": "sso.user@corp.example", "password": "x"}).status_code == 401


def test_sso_not_configured_returns_501(client):
    assert client.post("/api/v1/auth/sso/entra", json={"id_token": "x" * 40}).status_code == 501


# ------------------------------------------------------------------ configuration / Vault
def test_production_config_is_validated():
    with pytest.raises(RuntimeError) as err:
        Settings(app_env="production", jwt_secret="short", database_url="sqlite:///x.db", cors_origins="*").validate_for_runtime()
    message = str(err.value)
    assert "JWT_SECRET" in message and "PostgreSQL" in message and "CORS" in message
    Settings(app_env="production", jwt_secret="x" * 40, database_url="postgresql+psycopg://u:p@h/db",
             cors_origins="https://ops.example").validate_for_runtime()


def test_vault_secrets_override_environment():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Vault-Token"] == "root-token"
        assert request.url.path == "/v1/secret/data/copilot"
        return httpx.Response(200, json={"data": {"data": {"JWT_SECRET": "from-vault-" + "v" * 30, "OPENAI_API_KEY": "sk-vault",
                                                            "NOT_A_SETTING": "ignored"}}})

    s = Settings(vault_addr="http://vault:8200", vault_token="root-token", vault_secret_path="copilot")
    overrides = load_vault_overrides(s, transport=httpx.MockTransport(handler))
    assert overrides == {"jwt_secret": "from-vault-" + "v" * 30, "openai_api_key": "sk-vault"}
    assert Settings(**overrides).jwt_secret.get_secret_value().startswith("from-vault-")
