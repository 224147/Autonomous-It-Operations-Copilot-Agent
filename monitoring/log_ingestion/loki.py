"""Log ingestion: Loki push-format parser and the Loki pull connector."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import httpx

from agents.common.timeutil import iso, parse_ts
from monitoring.alert_ingestion.parsers import resolve_service

logger = logging.getLogger(__name__)

SAFE = re.compile(r"^[a-zA-Z0-9_.\-]*$")
DEFAULT_QUERY = (
    '{namespace="$namespace", pod=~"$deployment-.*"} '
    '|~ "(?i)(error|fatal|panic|exception|oomkilled|crashloop|timeout|refused|denied|expired|evicted|deadlock|no space)"'
)


def parse_loki_push(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Loki /loki/api/v1/push format: {"streams": [{"stream": {...labels}, "values": [[ns_ts, line], ...]}]}."""
    out = []
    for stream in payload.get("streams", []):
        labels = stream.get("stream") or {}
        service = resolve_service(labels)
        for value in stream.get("values", []):
            if len(value) >= 2 and service:
                out.append({"service": service, "message": str(value[1]), "timestamp": iso(parse_ts(value[0])),
                            "severity": labels.get("level") or labels.get("severity"), "attributes": labels})
    return out


class LokiConnector:
    name = "loki"

    def __init__(self, url: str, query_template: str = DEFAULT_QUERY, transport: httpx.BaseTransport | None = None,
                 limit: int = 500) -> None:
        self._url = url.rstrip("/")
        self._template = query_template
        self._transport = transport
        self._limit = limit

    def fetch(self, service: dict[str, Any], since: datetime, until: datetime) -> dict[str, list[dict[str, Any]]]:
        name = str(service.get("name", ""))
        values = {"namespace": service.get("namespace") or "default", "deployment": service.get("deployment") or name,
                  "service": name}
        if not all(SAFE.match(str(v)) for v in values.values()):
            raise ValueError("unsafe characters in service metadata")
        query = self._template
        for key, val in values.items():
            query = query.replace(f"${key}", str(val))
        params = {"query": query, "start": int(since.timestamp() * 1e9), "end": int(until.timestamp() * 1e9),
                  "limit": self._limit, "direction": "forward"}
        with httpx.Client(timeout=15.0, transport=self._transport) as client:
            resp = client.get(f"{self._url}/loki/api/v1/query_range", params=params)
            resp.raise_for_status()
            result = resp.json().get("data", {}).get("result", [])
        logs = []
        for stream in result:
            for ts, line in stream.get("values", []):
                logs.append({"service": name.lower(), "message": line, "timestamp": iso(parse_ts(ts)),
                             "attributes": stream.get("stream", {})})
        return {"logs": logs, "metrics": [], "traces": []}
