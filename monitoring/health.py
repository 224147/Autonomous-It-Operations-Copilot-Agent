"""Service health: current state per service derived from the freshest metrics and open incidents."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from agents.common.timeutil import utcnow
from agents.incident_agent.detector import detect
from agents.monitoring_agent.normalizer import NormalizationAgent
from monitoring.signal_store import SignalStore
from services.incident_service import IncidentService
from services.inventory_service import InventoryService

KEY_METRICS = ("cpu_usage_percent", "memory_usage_percent", "error_rate_percent", "latency_p99_ms", "restart_count")


def service_health(signals: SignalStore, inventory: InventoryService, incidents: IncidentService,
                   window_minutes: int = 5) -> list[dict[str, Any]]:
    since = utcnow() - timedelta(minutes=window_minutes)
    active, _ = incidents.list(active_only=True, limit=200)
    out = []
    for svc in inventory.list_services():
        raw = {"metrics": signals.query("metric", svc.name, since=since), "logs": signals.query("log", svc.name, since=since),
               "traces": signals.query("trace", svc.name, since=since), "alerts": []}
        norm = NormalizationAgent().run({"raw_signals": raw})["signals"]
        result = detect(norm, since=since, current_only=True)
        latest: dict[str, float] = {}
        for m in norm["metrics"]:
            latest[m["name"]] = m["value"]
        service_incidents = [i for i in active if i.service == svc.name or svc.name in i.affected_services]
        if not norm["metrics"] and not norm["logs"]:
            state = "unknown"
        elif result["score"] >= 3 or any(a["severity"] == "critical" for a in result["anomalies"]):
            state = "critical"
        elif result["score"] >= 1 or service_incidents:
            state = "degraded"
        else:
            state = "healthy"
        out.append({
            "name": svc.name, "tier": svc.tier, "team": svc.team, "kind": svc.kind, "state": state,
            "metrics": {k: round(latest[k], 2) for k in KEY_METRICS if k in latest},
            "anomalies": [{"name": a["name"], "severity": a["severity"], "description": a["description"]} for a in result["anomalies"][:5]],
            "active_incidents": [{"id": i.id, "severity": i.severity, "title": i.title} for i in service_incidents],
            "depends_on": svc.depends_on,
        })
    return out
