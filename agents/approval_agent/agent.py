"""Human Approval Agent: gates impactful remediation behind an explicit, audited approval."""
from __future__ import annotations

from typing import Any

from agents.common.ports import AgentContext
from agents.common.taxonomy import RISK_ORDER
from workflows.state import WorkflowState, event

TICKET = "open_ticket"


class ApprovalAgent:
    name = "approval_agent"

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    def run(self, state: WorkflowState) -> dict[str, Any]:
        if state.get("resume_stage") == "approval":
            return self._resolve(state)

        plan = (state.get("recommendations") or {}).get("actions", [])
        tickets = [a for a in plan if a["action"] == TICKET]  # non-destructive: never gated
        recovery = [a for a in plan if a["action"] != TICKET]
        if not recovery:
            return {
                "approval": {"status": "not_required", "reason": "no automated recovery actions"},
                "approved_actions": tickets, "status": "running",
                "events": [event("approval", "No recovery actions to approve")],
            }

        # Policy: with AUTO_APPROVE_MAX_RISK=low a plan whose top-ranked action is low risk is auto-approved for its
        # low-risk actions only; higher-risk fallbacks are not executable and stay visible as recommendations.
        low = [a for a in recovery if RISK_ORDER.get(a["risk"], 1) <= RISK_ORDER["low"]]
        auto = self.ctx.auto_approve_max_risk == "low" and bool(low) and low[0] is recovery[0]
        executable = low if auto else recovery
        risk = "low" if auto else (state.get("recommendations") or {}).get("risk", "medium")
        record = self.ctx.approvals.request(state.get("incident_id") or 0, executable, risk, auto)
        if record["status"] == "approved":
            return {
                "approval": record, "approved_actions": tickets + executable, "status": "running",
                "events": [event("approval", f"Auto-approved {len(executable)} low-risk action(s) by policy")],
            }
        return {
            "approval": record, "approved_actions": [], "status": "awaiting_approval",
            "events": [event("approval", f"Approval requested for {len(recovery)} action(s) (risk: {risk})")],
        }

    def _resolve(self, state: WorkflowState) -> dict[str, Any]:
        approval = state.get("approval") or {}
        record = self.ctx.approvals.get(int(approval["id"])) if approval.get("id") else None
        plan = (state.get("recommendations") or {}).get("actions", [])
        tickets = [a for a in plan if a["action"] == TICKET]
        if record is None or record["status"] == "pending":
            return {"approval": record or approval, "status": "awaiting_approval", "approved_actions": [],
                    "events": [event("approval", "Still awaiting a human decision")]}
        if record["status"] == "approved":
            chosen = record.get("approved_actions") or [a["id"] for a in plan]
            approved = [a for a in plan if a["action"] != TICKET and a["id"] in chosen]
            return {
                "approval": record, "approved_actions": tickets + approved, "status": "running",
                "events": [event("approval", f"Approved by {record.get('decided_by')}: {len(approved)} action(s)")],
            }
        return {
            "approval": record, "approved_actions": tickets, "status": "rejected", "outcome": "rejected",
            "events": [event("approval", f"Remediation {record['status']} by {record.get('decided_by') or 'system'}")],
        }
