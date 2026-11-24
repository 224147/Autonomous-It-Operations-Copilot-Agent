"""In-memory port implementations for tests, the evaluation harness and offline demos."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from agents.common.ports import AgentContext
from agents.common.timeutil import parse_ts
from agents.evaluation.scenarios import DEMO_SERVICES
from agents.learning_agent.stats import ActionStats
from backend.kv import MemoryKV
from monitoring.signal_store import SignalStore


class FakePlatform:
    def __init__(self, services: list[dict[str, Any]] | None = None, deployments: list[dict[str, Any]] | None = None,
                 alerts: list[dict[str, Any]] | None = None) -> None:
        self._services = services if services is not None else DEMO_SERVICES
        self.deployment_records = deployments or []
        self.alert_records = alerts or []

    def services(self) -> list[dict[str, Any]]:
        return self._services

    def deployments(self, services: list[str], since: datetime) -> list[dict[str, Any]]:
        names = {s.lower() for s in services}
        return [d for d in self.deployment_records if d["service"].lower() in names and parse_ts(d["deployed_at"]) >= since]

    def alerts(self, services: list[str], since: datetime) -> list[dict[str, Any]]:
        names = {s.lower() for s in services}
        return [a for a in self.alert_records if a.get("service", "").lower() in names]


class FakeApprovals:
    def __init__(self) -> None:
        self.records: dict[int, dict[str, Any]] = {}

    def request(self, incident_id: int, plan: list[dict[str, Any]], risk: str, auto_approve: bool) -> dict[str, Any]:
        rid = len(self.records) + 1
        record = {"id": rid, "incident_id": incident_id, "plan": plan, "risk": risk,
                  "status": "approved" if auto_approve else "pending", "approved_actions": [], "decided_by": "policy" if auto_approve else None}
        self.records[rid] = record
        return record

    def get(self, approval_id: int) -> dict[str, Any] | None:
        return self.records.get(approval_id)

    def decide(self, approval_id: int, status: str, approved_actions: list[str] | None = None, by: str = "tester") -> None:
        self.records[approval_id].update(status=status, approved_actions=approved_actions or [], decided_by=by)


class FakeRemediation:
    def __init__(self, status: str = "succeeded", mode: str = "live") -> None:
        self.calls: list[dict[str, Any]] = []
        self.status, self.mode = status, mode

    def execute(self, incident_id: int | None, approval_id: int | None, action: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(action)
        return {"status": self.status, "mode": self.mode, "output": {"fake": True}, "error": "" if self.status != "failed" else "boom"}


def make_context(kb=None, llm=None, platform: FakePlatform | None = None, auto_approve: str = "none",  # noqa: ANN001
                 remediation: FakeRemediation | None = None, approvals: FakeApprovals | None = None) -> AgentContext:
    kv = MemoryKV()
    return AgentContext(
        signals=SignalStore(kv), platform=platform or FakePlatform(), approvals=approvals or FakeApprovals(),
        remediation=remediation or FakeRemediation(), stats=ActionStats(kv), kb=kb, llm=llm,
        verify_wait_seconds=0, verify_poll_seconds=0, auto_approve_max_risk=auto_approve, sleep=lambda _s: None,
    )
