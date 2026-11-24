"""HashiCorp Vault integration: overlays secrets from a KV v2 path onto Settings."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from backend.config import Settings

logger = logging.getLogger(__name__)

K8S_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")


def _headers(settings: Settings) -> dict[str, str]:
    return {"X-Vault-Namespace": settings.vault_namespace} if settings.vault_namespace else {}


def _login(client: httpx.Client, settings: Settings) -> str:
    token = settings.vault_token.get_secret_value()
    if token:
        return token
    if settings.vault_k8s_role and K8S_TOKEN_PATH.exists():
        resp = client.post(
            f"{settings.vault_addr.rstrip('/')}/v1/auth/kubernetes/login",
            json={"role": settings.vault_k8s_role, "jwt": K8S_TOKEN_PATH.read_text().strip()},
            headers=_headers(settings),
        )
        resp.raise_for_status()
        return resp.json()["auth"]["client_token"]
    raise RuntimeError("Vault configured but neither VAULT_TOKEN nor VAULT_K8S_ROLE is usable")


def load_vault_overrides(settings: Settings, transport: httpx.BaseTransport | None = None) -> dict[str, str]:
    """Return {setting_name: value} read from Vault KV v2. Values override environment variables."""
    from backend.config import Settings as SettingsModel

    known = set(SettingsModel.model_fields)
    try:
        with httpx.Client(timeout=10.0, transport=transport) as client:
            token = _login(client, settings)
            url = f"{settings.vault_addr.rstrip('/')}/v1/{settings.vault_kv_mount}/data/{settings.vault_secret_path}"
            resp = client.get(url, headers={**_headers(settings), "X-Vault-Token": token})
            resp.raise_for_status()
            data = resp.json()["data"]["data"]
    except Exception:
        if settings.is_production:
            raise
        logger.exception("Vault secret load failed; continuing with environment configuration")
        return {}
    overrides = {k.lower(): str(v) for k, v in data.items() if k.lower() in known}
    logger.info("Loaded %d settings from Vault", len(overrides))
    return overrides
