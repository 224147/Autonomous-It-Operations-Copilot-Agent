"""Monitoring Agent: gathers logs, metrics, traces and alerts for the triggered service and its neighbours."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from agents.common.ports import AgentContext
from agents.common.timeutil import utcnow
from workflows.state import WorkflowState, event

logger = logging.getLogger(__name__)


def resolve_scope(service: str, inventory: list[dict[str, Any]]) -> list[str]:
    """The service itself, its direct dependencies and its direct dependants."""
    by_name = {s["name"].lower(): s for s in inventory}
    scope = {service.lower()}
    entry = by_name.get(service.lower())
    if entry:
        scope.update(d.lower() for d in entry.get("depends_on") or [])
    for other in inventory:
        if service.lower() in [d.lower() for d in other.get("depends_on") or []]:
            scope.add(other["name"].lower())
    return sorted(scope)


class MonitoringAgent:
    name = "monitoring_agent"

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    def run(self, state: WorkflowState) -> dict[str, Any]:
        trigger = state.get("trigger") or {}
        service = str(trigger.get("service") or "").lower()
        if not service:
            raise ValueError("trigger.service is required")
        window = int(trigger.get("window_minutes") or self.ctx.detection_window_minutes)
        until = utcnow()
        since = until - timedelta(minutes=window)

        inventory = self.ctx.platform.services()
        scope = resolve_scope(service, inventory)
        meta = {s["name"].lower(): s for s in inventory}

        raw: dict[str, list[dict[str, Any]]] = {"logs": [], "metrics": [], "traces": [], "alerts": []}
        for svc in scope:
            for kind, key in (("log", "logs"), ("metric", "metrics"), ("trace", "traces")):
                raw[key].extend(self.ctx.signals.query(kind, svc, since=since))
            for connector in self.ctx.connectors:
                try:
                    pulled = connector.fetch(meta.get(svc, {"name": svc}), since, until)
                    for key in ("logs", "metrics", "traces"):
                        raw[key].extend(pulled.get(key, []))
                except Exception as exc:  # a failing integration must not stop the investigation
                    logger.warning("connector %s failed for %s: %s", connector.name, svc, exc)
        raw["alerts"] = self.ctx.platform.alerts(scope, since)

        counts = {k: len(v) for k, v in raw.items()}
        return {
            "scope_services": scope,
            "raw_signals": raw,
            "events": [event("monitoring", f"Collected {counts['logs']} logs, {counts['metrics']} metric samples, "
                             f"{counts['traces']} spans, {counts['alerts']} alerts for {', '.join(scope)}")],
            "status": "running",
        }
