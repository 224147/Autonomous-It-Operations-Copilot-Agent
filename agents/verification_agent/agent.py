"""Verification Agent: confirms the service returned to a healthy state after remediation."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from agents.common.ports import AgentContext
from agents.common.timeutil import iso, parse_ts, utcnow
from agents.incident_agent.detector import detect
from agents.monitoring_agent.normalizer import NormalizationAgent
from workflows.state import WorkflowState, event

logger = logging.getLogger(__name__)


class VerificationAgent:
    name = "verification_agent"

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    def run(self, state: WorkflowState) -> dict[str, Any]:
        recovery = [e for e in state.get("executions", []) if e["action"] != "open_ticket"]
        if not recovery:
            return self._result("skipped", None, "No recovery action was executed", state)
        last = recovery[-1]
        if last["status"] == "failed":
            return self._result("failed", False, f"{last['action']} failed: {last.get('error', '')}", state)
        if last["status"] == "simulated":
            return self._result("simulated", None, "Dry-run mode: action was simulated, health cannot be verified", state)

        rca = state.get("rca") or {}
        detection = state.get("detection") or {}
        services = sorted({s for s in (rca.get("root_service"), detection.get("primary_service")) if s})
        since = parse_ts(last["finished_at"])
        deadline = utcnow() + timedelta(seconds=self.ctx.verify_wait_seconds)
        verdict: tuple[bool, bool, list[dict[str, Any]]] | None = None
        while True:
            verdict = self._check(services, since)
            has_samples, healthy, anomalies = verdict
            if has_samples and healthy:
                break
            if utcnow() >= deadline:
                break
            self.ctx.sleep(self.ctx.verify_poll_seconds)
        has_samples, healthy, anomalies = verdict
        if not has_samples:
            return self._result("unknown", None, "No fresh metrics were received after remediation", state)
        if healthy:
            return self._result("healthy", True, "Metrics returned to normal ranges after remediation", state)
        return self._result("unhealthy", False, "Anomalies persist: " + "; ".join(a["description"] for a in anomalies[:3]), state)

    def _check(self, services: list[str], since) -> tuple[bool, bool, list[dict[str, Any]]]:  # noqa: ANN001
        raw: dict[str, list[dict[str, Any]]] = {"logs": [], "metrics": [], "traces": [], "alerts": []}
        now = utcnow()
        for svc in services:
            for kind, key in (("log", "logs"), ("metric", "metrics"), ("trace", "traces")):
                raw[key].extend(self.ctx.signals.query(kind, svc, since=since))
            for connector in self.ctx.connectors:
                try:
                    pulled = connector.fetch({"name": svc}, since, now)
                    for key in ("logs", "metrics", "traces"):
                        raw[key].extend(pulled.get(key, []))
                except Exception as exc:
                    logger.warning("verification connector %s failed: %s", connector.name, exc)
        norm = NormalizationAgent().run({"raw_signals": raw})["signals"]
        result = detect(norm, since=since, current_only=True)
        relevant = [a for a in result["anomalies"] if a["service"] in services]
        score = sum(a["weight"] for a in relevant)
        return bool(norm["metrics"]), score < 3.0 and not any(a["severity"] == "critical" for a in relevant), relevant

    @staticmethod
    def _result(status: str, healthy: bool | None, details: str, state: WorkflowState) -> dict[str, Any]:
        verification = {"status": status, "healthy": healthy, "details": details, "checked_at": iso(utcnow())}
        update: dict[str, Any] = {"verification": verification,
                                  "events": [event("verification", f"{status}: {details}")]}
        if status == "healthy":
            update["outcome"] = "resolved"
        return update
