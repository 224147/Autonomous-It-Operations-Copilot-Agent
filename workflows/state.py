"""LangGraph state shared by all agents."""
from __future__ import annotations

import operator
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict


class WorkflowState(TypedDict, total=False):
    # input
    incident_id: int | None
    trigger: dict[str, Any]  # {service, window_minutes, reason, alert_ids}
    resume_stage: str | None  # "approval" when resuming after a human decision

    # monitoring / normalisation
    scope_services: list[str]
    raw_signals: dict[str, list[dict[str, Any]]]
    signals: dict[str, list[dict[str, Any]]]
    signal_summary: dict[str, int]

    # detection / severity
    detection: dict[str, Any]
    severity: dict[str, Any]

    # analysis
    correlation: dict[str, Any]
    rca: dict[str, Any]
    knowledge: dict[str, Any]
    recommendations: dict[str, Any]

    # approval / remediation / verification
    approval: dict[str, Any]
    approved_actions: list[dict[str, Any]]
    attempts: int
    verification: dict[str, Any]

    # learning / reporting
    learning: dict[str, Any]
    report: dict[str, Any]

    # outcome
    status: str  # running | no_incident | awaiting_approval | rejected | remediated | resolved | escalated | completed
    outcome: str

    # append-only (reducers merge parallel/iterative updates)
    executions: Annotated[list[dict[str, Any]], operator.add]
    events: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[dict[str, Any]], operator.add]


def event(stage: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"timestamp": datetime.now(UTC).isoformat(), "stage": stage, "message": message, **extra}
