"""Learning Agent: feeds resolved/attempted incidents back into the vector knowledge base and action statistics."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from agents.common.ports import AgentContext
from agents.common.timeutil import iso, utcnow
from workflows.state import WorkflowState, event

logger = logging.getLogger(__name__)


def derive_outcome(state: WorkflowState) -> str:
    verification = (state.get("verification") or {}).get("status")
    if state.get("outcome") == "rejected" or state.get("status") == "rejected":
        return "rejected"
    return {
        "healthy": "resolved", "unhealthy": "failed", "failed": "failed",
        "simulated": "unverified", "unknown": "unverified",
    }.get(verification or "", "no_action")


def incident_document(state: WorkflowState, outcome: str) -> tuple[str, dict[str, Any]]:
    rca = state.get("rca") or {}
    detection = state.get("detection") or {}
    severity = (state.get("severity") or {}).get("level", "")
    recovery = [e for e in state.get("executions", []) if e["action"] != "open_ticket"]
    taken = [e["action"] for e in recovery]
    resolution = recovery[-1]["action"] if outcome == "resolved" and recovery else ""
    text = "\n".join([
        f"Incident: {detection.get('title', '')}",
        f"Category: {rca.get('category', 'unknown')}",
        f"Root cause: {rca.get('root_cause', '')}",
        f"Explanation: {rca.get('explanation', '')}",
        f"Affected services: {', '.join(detection.get('affected_services', []))}",
        "Evidence: " + "; ".join(e["detail"] for e in rca.get("evidence", [])[:6]),
        f"Actions taken: {', '.join(taken) or 'none'}",
        f"Outcome: {outcome}",
    ])
    metadata = {
        "title": detection.get("title", "Incident"),
        "category": rca.get("category", "unknown"),
        "service": rca.get("root_service", ""),
        "severity": severity,
        "outcome": outcome,
        "resolution_action": resolution,
        "actions_taken": taken,
        "resolved": outcome == "resolved",
        "learned_at": iso(utcnow()),
    }
    return text, metadata


class LearningAgent:
    name = "learning_agent"

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    def run(self, state: WorkflowState) -> dict[str, Any]:
        outcome = derive_outcome(state)
        category = (state.get("rca") or {}).get("category", "unknown")
        learning: dict[str, Any] = {"outcome": outcome, "indexed": False, "doc_id": None, "stats_updated": []}
        errors: list[dict[str, Any]] = []

        recovery = [e for e in state.get("executions", []) if e["action"] != "open_ticket" and e["status"] in ("succeeded", "failed")]
        verified = (state.get("verification") or {}).get("status") in ("healthy", "unhealthy", "failed")
        if recovery and verified:
            healthy = (state.get("verification") or {}).get("status") == "healthy"
            for idx, ex in enumerate(recovery):
                success = healthy and idx == len(recovery) - 1
                self.ctx.stats.record(category, ex["action"], success)
                learning["stats_updated"].append({"action": ex["action"], "success": success})

        if self.ctx.kb and state.get("detection", {}).get("is_incident"):
            doc_id = f"inc-{state.get('incident_id') or uuid.uuid4().hex[:12]}"
            try:
                text, metadata = incident_document(state, outcome)
                metadata["incident_id"] = state.get("incident_id") or 0
                self.ctx.kb.upsert(doc_id, text, "incident", metadata)
                learning.update(indexed=True, doc_id=doc_id)
            except Exception as exc:  # learning must never fail the incident workflow
                logger.exception("Failed to index incident into knowledge base")
                errors.append({"stage": "learning", "error": str(exc)})
        return {
            "learning": learning, "errors": errors,
            "events": [event("learning", f"Outcome '{outcome}' recorded; knowledge base {'updated' if learning['indexed'] else 'unchanged'}")],
        }
