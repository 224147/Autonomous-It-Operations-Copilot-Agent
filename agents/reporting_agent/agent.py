"""Reporting Agent: assembles the incident report / RCA document from all workflow results."""
from __future__ import annotations

import json
import logging
from typing import Any

from agents.common.ports import AgentContext
from agents.common.timeutil import iso, utcnow
from agents.learning_agent.agent import derive_outcome
from workflows.state import WorkflowState, event

logger = logging.getLogger(__name__)

FINAL_STATUS = {
    "healthy": "resolved", "unhealthy": "escalated", "failed": "escalated", "skipped": "escalated",
    "simulated": "monitoring", "unknown": "monitoring",
}


def final_status(state: WorkflowState) -> str:
    if state.get("status") == "error":
        return "error"
    if not (state.get("detection") or {}).get("is_incident"):
        return "no_incident"
    if state.get("status") == "rejected" or state.get("outcome") == "rejected":
        return "rejected"
    return FINAL_STATUS.get((state.get("verification") or {}).get("status", "skipped"), "escalated")


class ReportingAgent:
    name = "reporting_agent"

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    def run(self, state: WorkflowState) -> dict[str, Any]:
        status = final_status(state)
        if status in ("no_incident", "error"):
            return {"status": status, "outcome": status,
                    "report": {"status": status, "generated_at": iso(utcnow()), "errors": state.get("errors", [])}}

        rca = state.get("rca") or {}
        detection = state.get("detection") or {}
        severity = state.get("severity") or {}
        corr = state.get("correlation") or {}
        plan = state.get("recommendations") or {}
        verification = state.get("verification") or {}
        executions = state.get("executions", [])
        timeline = sorted(
            [{"timestamp": t["timestamp"], "source": "signal", "service": t.get("service"), "description": t["description"]}
             for t in corr.get("timeline", [])]
            + [{"timestamp": e["timestamp"], "source": "workflow", "stage": e["stage"], "description": e["message"]}
               for e in state.get("events", [])],
            key=lambda i: i["timestamp"],
        )
        report: dict[str, Any] = {
            "title": detection.get("title", "Incident"),
            "incident_id": state.get("incident_id"),
            "severity": severity.get("level", "P3"),
            "status": status,
            "outcome": derive_outcome(state),
            "root_cause": rca.get("root_cause", ""),
            "category": rca.get("category", "unknown"),
            "root_service": rca.get("root_service", ""),
            "confidence": rca.get("confidence", 0.0),
            "explanation": rca.get("explanation", ""),
            "contributing_factors": rca.get("contributing_factors", []),
            "evidence": rca.get("evidence", []),
            "affected_services": detection.get("affected_services", []),
            "timeline": timeline,
            "recommendations": plan.get("actions", []),
            "executions": executions,
            "verification": verification,
            "similar_incidents": (state.get("knowledge") or {}).get("similar_incidents", []),
            "generated_at": iso(utcnow()),
            "errors": state.get("errors", []),
        }
        report["lessons_learned"] = self._lessons(report)
        report["summary"] = self._summary(report)
        report["markdown"] = self._markdown(report)
        return {
            "report": report, "status": status, "outcome": report["outcome"],
            "events": [event("reporting", f"Incident report generated (status: {status})")],
        }

    def _summary(self, r: dict[str, Any]) -> str:
        fallback = (
            f"{r['severity']} incident on {r['root_service'] or 'unknown service'}: {r['root_cause']} "
            f"(confidence {r['confidence']:.0%}). Status: {r['status']}."
        )
        if not self.ctx.llm:
            return fallback
        facts = {k: r[k] for k in ("severity", "root_cause", "confidence", "status", "affected_services")}
        facts["actions"] = [f"{e['action']}={e['status']}" for e in r["executions"]]
        facts["verification"] = r["verification"].get("details")
        text = self.ctx.llm.chat(
            "Write a 2-3 sentence executive incident summary using ONLY the provided facts.",
            json.dumps(facts, default=str), max_tokens=250,
        )
        return text.strip() if text else fallback

    @staticmethod
    def _lessons(r: dict[str, Any]) -> list[str]:
        lessons = []
        if any("deployment" in f.lower() for f in r["contributing_factors"]):
            lessons.append("Add canary analysis / automated rollback gates for this service's deployments.")
        if r["category"] in ("memory_exhaustion", "cpu_saturation", "traffic_spike"):
            lessons.append("Review resource limits and autoscaling (HPA) configuration.")
        if r["category"] in ("db_connectivity", "db_contention"):
            lessons.append("Review connection pool sizing, timeouts and slow queries.")
        if r["verification"].get("status") in ("unhealthy", "failed"):
            lessons.append("Automated remediation did not restore health; update the runbook for this failure mode.")
        if r["category"] == "unknown":
            lessons.append("Add detection signatures/runbook for this failure mode to the knowledge base.")
        return lessons

    @staticmethod
    def _markdown(r: dict[str, Any]) -> str:
        lines = [
            f"# Incident Report: {r['title']}", "",
            f"- **Severity:** {r['severity']}", f"- **Status:** {r['status']}",
            f"- **Root service:** {r['root_service']}", f"- **Category:** {r['category']}",
            f"- **Confidence:** {r['confidence']:.0%}",
            f"- **Affected services:** {', '.join(r['affected_services']) or 'n/a'}", "",
            "## Summary", r["summary"], "", "## Root Cause", r["root_cause"], "", r["explanation"], "",
        ]
        if r["contributing_factors"]:
            lines += ["## Contributing Factors", *[f"- {f}" for f in r["contributing_factors"]], ""]
        if r["evidence"]:
            lines += ["## Evidence", *[f"- [{e['type']}] {e['detail']}" for e in r["evidence"][:15]], ""]
        lines += ["## Timeline", *[f"- `{t['timestamp']}` {t['description']}" for t in r["timeline"]], ""]
        if r["executions"]:
            lines += ["## Remediation Actions",
                      *[f"- {e['action']} on {e['target']}: **{e['status']}** ({e['mode']})" for e in r["executions"]], ""]
        if r["verification"]:
            lines += ["## Verification", f"{r['verification'].get('status')}: {r['verification'].get('details')}", ""]
        if r["lessons_learned"]:
            lines += ["## Lessons Learned", *[f"- {item}" for item in r["lessons_learned"]], ""]
        return "\n".join(lines)
