"""Parsers that turn vendor alert payloads into the platform's alert schema."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from agents.common.timeutil import parse_ts

SERVICE_LABELS = ("service", "service_name", "app", "app_kubernetes_io_name", "deployment", "kubernetes_name", "job")
AZURE_SEVERITY = {"sev0": "critical", "sev1": "critical", "sev2": "error", "sev3": "warning", "sev4": "warning"}


def resolve_service(labels: dict[str, Any]) -> str:
    for key in SERVICE_LABELS:
        value = labels.get(key)
        if value:
            return str(value).lower()
    pod = labels.get("pod")
    if pod:  # deployment pods are named <deployment>-<rs-hash>-<pod-hash>
        parts = str(pod).split("-")
        return "-".join(parts[:-2] if len(parts) > 2 else parts).lower()
    return ""


def fingerprint(name: str, service: str, labels: dict[str, Any]) -> str:
    raw = json.dumps([name, service, sorted((str(k), str(v)) for k, v in labels.items())], sort_keys=True)
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()


def parse_alertmanager(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Prometheus Alertmanager (and Grafana unified alerting) webhook payloads."""
    out = []
    for alert in payload.get("alerts", []):
        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        service = resolve_service(labels)
        name = labels.get("alertname") or "alert"
        out.append({
            "name": name, "service": service, "severity": labels.get("severity", "warning"),
            "status": "resolved" if alert.get("status") == "resolved" else "firing",
            "summary": annotations.get("summary") or annotations.get("description") or "",
            "source": "alertmanager", "labels": labels, "annotations": annotations,
            "starts_at": parse_ts(alert.get("startsAt")) if alert.get("startsAt") else None,
            "ends_at": parse_ts(alert["endsAt"]) if alert.get("status") == "resolved" and alert.get("endsAt") else None,
            "fingerprint": alert.get("fingerprint") or fingerprint(name, service, labels),
        })
    return out


def parse_azure_monitor(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Azure Monitor common alert schema."""
    essentials = (payload.get("data") or {}).get("essentials") or {}
    if not essentials:
        return []
    targets = essentials.get("alertTargetIDs") or []
    service = str(targets[0]).rstrip("/").split("/")[-1].lower() if targets else ""
    name = essentials.get("alertRule") or "azure-alert"
    resolved = str(essentials.get("monitorCondition", "")).lower() == "resolved"
    labels = {"alertId": essentials.get("alertId", ""), "monitoringService": essentials.get("monitoringService", "")}
    return [{
        "name": name, "service": service, "severity": AZURE_SEVERITY.get(str(essentials.get("severity", "")).lower(), "warning"),
        "status": "resolved" if resolved else "firing", "summary": essentials.get("description") or name,
        "source": "azure-monitor", "labels": labels, "annotations": {},
        "starts_at": parse_ts(essentials.get("firedDateTime")) if essentials.get("firedDateTime") else None,
        "ends_at": parse_ts(essentials.get("resolvedDateTime")) if resolved and essentials.get("resolvedDateTime") else None,
        "fingerprint": str(essentials.get("alertId") or fingerprint(name, service, labels)),
    }]


def parse_datadog(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tags = payload.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    tag_map = {t.split(":", 1)[0]: t.split(":", 1)[1] for t in tags if ":" in t}
    service = resolve_service(tag_map)
    transition = str(payload.get("alert_transition") or payload.get("transition") or "").lower()
    alert_type = str(payload.get("alert_type") or "warning").lower()
    resolved = transition in ("recovered", "resolved") or alert_type == "success"
    name = payload.get("title") or payload.get("event_title") or "datadog-alert"
    return [{
        "name": str(name)[:255], "service": service, "severity": "critical" if alert_type == "error" else "warning",
        "status": "resolved" if resolved else "firing", "summary": str(payload.get("body") or payload.get("text") or "")[:1000],
        "source": "datadog", "labels": tag_map, "annotations": {},
        "starts_at": parse_ts(payload["date"]) if payload.get("date") else None, "ends_at": None,
        "fingerprint": str(payload.get("id") or payload.get("alert_id") or fingerprint(str(name), service, tag_map)),
    }]
