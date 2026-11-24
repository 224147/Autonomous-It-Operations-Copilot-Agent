"""Remediation (Execution) Agent: executes approved actions through the remediation engine."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from agents.common.ports import AgentContext
from workflows.state import WorkflowState, event

logger = logging.getLogger(__name__)


class RemediationAgent:
    name = "remediation_agent"

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    def run(self, state: WorkflowState) -> dict[str, Any]:
        done = {e["action_id"] for e in state.get("executions", [])}
        pending = [a for a in state.get("approved_actions", []) if a["id"] not in done]
        tickets = [a for a in pending if a["action"] == "open_ticket"]
        recovery = [a for a in pending if a["action"] != "open_ticket"]
        to_run = tickets + recovery[:1]  # one recovery action per pass; verification decides whether to continue

        approval_id = (state.get("approval") or {}).get("id")
        executions, events = [], []
        for action in to_run:
            started = datetime.now(UTC).isoformat()
            try:
                result = self.ctx.remediation.execute(state.get("incident_id"), approval_id, action)
            except Exception as exc:  # engine errors become a failed execution, never a crashed workflow
                logger.exception("Remediation action %s crashed", action["action"])
                result = {"status": "failed", "mode": "unknown", "output": {}, "error": str(exc)}
            record = {
                "action_id": action["id"], "action": action["action"], "target": action.get("target", ""),
                "params": action.get("params", {}), "status": result.get("status", "failed"),
                "mode": result.get("mode", ""), "output": result.get("output", {}), "error": result.get("error", ""),
                "started_at": started, "finished_at": datetime.now(UTC).isoformat(),
            }
            executions.append(record)
            events.append(event("remediation", f"{action['action']} on {action.get('target', '')}: {record['status']}"
                                + (f" ({record['error']})" if record["error"] else "")))
        update: dict[str, Any] = {"executions": executions, "events": events, "status": "running"}
        if recovery[:1]:
            update["attempts"] = int(state.get("attempts", 0)) + 1
        return update
