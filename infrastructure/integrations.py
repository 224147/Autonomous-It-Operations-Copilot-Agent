"""Cloud VM, cache and ticketing integrations used by the remediation engine."""
from __future__ import annotations

import logging
from typing import Any

import httpx
import redis

logger = logging.getLogger(__name__)


class IntegrationError(RuntimeError):
    pass


class AwsVmClient:
    """Restart EC2 instances (requires boto3 and AWS credentials from the environment / workload identity)."""

    def __init__(self, region: str = "", client=None) -> None:  # noqa: ANN001
        self._region = region
        self._client = client

    def reboot(self, instance_id: str) -> dict[str, Any]:
        if not instance_id:
            raise IntegrationError("service has no vm_instance_id configured")
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise IntegrationError("boto3 is not installed; install it to enable restart_vm") from exc
            self._client = boto3.client("ec2", region_name=self._region or None)
        self._client.reboot_instances(InstanceIds=[instance_id])
        return {"rebooted_instance": instance_id, "provider": "aws"}


class CacheClient:
    """Clears keys under a service-specific prefix. Never flushes the whole cache."""

    def __init__(self, url: str, client: redis.Redis | None = None) -> None:
        self._url = url
        self._client = client

    def clear_prefix(self, prefix: str) -> dict[str, Any]:
        if not prefix or len(prefix) < 3:
            raise IntegrationError("refusing to clear cache without a specific key prefix")
        if self._client is None:
            if not self._url:
                raise IntegrationError("CACHE_REDIS_URL is not configured")
            self._client = redis.Redis.from_url(self._url, decode_responses=True, socket_timeout=10)
        deleted, batch = 0, []
        for key in self._client.scan_iter(match=f"{prefix}*", count=500):
            batch.append(key)
            if len(batch) >= 500:
                deleted += self._client.unlink(*batch)
                batch = []
        if batch:
            deleted += self._client.unlink(*batch)
        return {"prefix": prefix, "keys_deleted": int(deleted)}


class TicketClient:
    """Forwards tickets to an ITSM webhook (ServiceNow/Jira automation/PagerDuty-compatible JSON endpoint)."""

    def __init__(self, url: str, token: str = "", transport: httpx.BaseTransport | None = None) -> None:
        self._url, self._token, self._transport = url, token, transport

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    def create(self, payload: dict[str, Any]) -> str:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        with httpx.Client(timeout=15.0, transport=self._transport) as client:
            resp = client.post(self._url, json=payload, headers=headers)
            resp.raise_for_status()
            try:
                body = resp.json()
            except ValueError:
                return ""
        if isinstance(body, dict):
            for key in ("id", "key", "number", "url", "ticket_id"):
                if body.get(key):
                    return str(body[key])
        return ""
