"""Data Normalization Agent: converts raw signals from any source into the canonical schema."""
from __future__ import annotations

import re
from typing import Any

from agents.common.taxonomy import LOG_PATTERNS, METRIC_ALIASES, SEVERITY_WEIGHT
from agents.common.timeutil import iso, parse_ts
from workflows.state import WorkflowState, event

_LEVELS = {
    "trace": "info", "debug": "info", "info": "info", "notice": "info", "warn": "warning", "warning": "warning",
    "err": "error", "error": "error", "fatal": "critical", "critical": "critical", "crit": "critical",
    "panic": "critical", "emerg": "critical", "alert": "critical", "high": "error", "page": "critical",
}
_LEVEL_RE = re.compile(r"\b(fatal|critical|panic|error|err|warn(?:ing)?)\b", re.I)


def normalize_severity(value: Any, text: str = "") -> str:
    key = str(value or "").strip().lower()
    if key in _LEVELS:
        return _LEVELS[key]
    match = _LEVEL_RE.search(text or "")
    if match:
        return _LEVELS.get(match.group(1).lower(), "info")
    for pattern in LOG_PATTERNS:  # a known failure signature implies at least its severity
        if pattern.regex.search(text or ""):
            return pattern.severity
    return "info"


def normalize_metric_name(name: str) -> str:
    key = re.sub(r"[^a-z0-9_]+", "_", str(name).strip().lower()).strip("_")
    return METRIC_ALIASES.get(key, key)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class NormalizationAgent:
    name = "normalization_agent"

    def run(self, state: WorkflowState) -> dict[str, Any]:
        raw = state.get("raw_signals") or {}
        logs = self._logs(raw.get("logs", []))
        metrics = self._metrics(raw.get("metrics", []))
        traces = self._traces(raw.get("traces", []))
        alerts = self._alerts(raw.get("alerts", []))
        summary = {"logs": len(logs), "metrics": len(metrics), "traces": len(traces), "alerts": len(alerts)}
        return {
            "signals": {"logs": logs, "metrics": metrics, "traces": traces, "alerts": alerts},
            "signal_summary": summary,
            "raw_signals": {},  # free memory; normalized copy is authoritative
            "events": [event("normalization", f"Normalized signals: {summary}")],
        }

    @staticmethod
    def _logs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out, seen = [], set()
        for item in items:
            message = str(item.get("message") or item.get("line") or "").strip()
            service = str(item.get("service") or "").lower()
            if not message or not service:
                continue
            ts = iso(parse_ts(item.get("timestamp")))
            key = (service, ts, message)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "kind": "log", "service": service, "timestamp": ts, "message": message[:2000],
                "severity": normalize_severity(item.get("severity") or item.get("level"), message),
                "attributes": item.get("attributes") or {},
            })
        out.sort(key=lambda s: s["timestamp"])
        return out

    @staticmethod
    def _metrics(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out, seen = [], set()
        for item in items:
            value = _num(item.get("value"))
            service = str(item.get("service") or "").lower()
            name = normalize_metric_name(item.get("name") or "")
            if value is None or not service or not name or value != value:  # NaN guard
                continue
            ts = iso(parse_ts(item.get("timestamp")))
            key = (service, name, ts)
            if key in seen:
                continue
            seen.add(key)
            out.append({"kind": "metric", "service": service, "name": name, "value": value, "timestamp": ts,
                        "labels": item.get("labels") or {}})
        out.sort(key=lambda s: s["timestamp"])
        return out

    @staticmethod
    def _traces(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for item in items:
            service = str(item.get("service") or "").lower()
            if not service:
                continue
            attrs = item.get("attributes") or {}
            peer = item.get("peer_service") or attrs.get("peer.service") or attrs.get("net.peer.name") or ""
            status = str(item.get("status") or "ok").lower()
            status = "error" if status in ("error", "err", "failed", "2", "status_code_error") else "ok"
            duration = _num(item.get("duration_ms")) or 0.0
            out.append({
                "kind": "trace", "service": service, "timestamp": iso(parse_ts(item.get("timestamp"))),
                "trace_id": str(item.get("trace_id") or ""), "span_id": str(item.get("span_id") or ""),
                "parent_span_id": str(item.get("parent_span_id") or ""), "name": str(item.get("name") or ""),
                "duration_ms": duration, "status": status, "peer_service": str(peer).lower(),
                "attributes": attrs,
            })
        out.sort(key=lambda s: s["timestamp"])
        return out

    @staticmethod
    def _alerts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for item in items:
            severity = normalize_severity(item.get("severity"), item.get("name", ""))
            if SEVERITY_WEIGHT.get(severity, 0) == 0:
                severity = "warning"
            out.append({
                "kind": "alert", "service": str(item.get("service") or "").lower(), "name": str(item.get("name") or ""),
                "severity": severity, "status": str(item.get("status") or "firing"),
                "timestamp": iso(parse_ts(item.get("starts_at") or item.get("timestamp"))),
                "summary": str(item.get("summary") or ""), "source": str(item.get("source") or ""),
                "id": item.get("id"),
            })
        out.sort(key=lambda s: s["timestamp"])
        return out
