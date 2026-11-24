"""Remediation: approvals, executions, tickets and the action catalogue."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from agents.common.taxonomy import ACTIONS
from backend.container import Container
from backend.deps import Principal, approver, get_container, operator, viewer
from backend.schemas import ApprovalDecision, ApprovalOut, ExecutionOut, ManualRemediation, TicketOut
from services.remediation_service import RemediationError

router = APIRouter(prefix="/remediation", tags=["remediation"])


@router.get("/actions", summary="Catalogue of supported remediation actions")
def action_catalogue(_: Principal = Depends(viewer), container: Container = Depends(get_container)) -> dict:
    return {"mode": container.settings.remediation_mode,
            "actions": [{"name": n, **meta} for n, meta in ACTIONS.items()]}


@router.get("/approvals", response_model=list[ApprovalOut])
def list_approvals(status: str | None = Query(None, pattern="^(pending|approved|rejected|expired)$"),
                   limit: int = Query(100, ge=1, le=500), _: Principal = Depends(viewer),
                   container: Container = Depends(get_container)):
    return container.remediation.list_approvals(status, limit)


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalOut)
def decide(approval_id: int, body: ApprovalDecision, principal: Principal = Depends(approver),
           container: Container = Depends(get_container)):
    try:
        data = container.remediation.decide(approval_id, body.decision == "approve", principal.email, principal.role,
                                            body.comment, body.approved_actions, principal.ip)
    except RemediationError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return ApprovalOut(**data)


@router.get("/executions", response_model=list[ExecutionOut])
def list_executions(incident_id: int | None = None, limit: int = Query(100, ge=1, le=500),
                    _: Principal = Depends(viewer), container: Container = Depends(get_container)):
    return container.remediation.list_executions(incident_id, limit)


@router.post("/execute", response_model=dict, summary="Manually execute a catalogue action (approver role, audited)")
def execute_manual(body: ManualRemediation, principal: Principal = Depends(approver),
                   container: Container = Depends(get_container)) -> dict:
    try:
        return container.remediation.execute_manual(principal.email, principal.role, body.incident_id, body.action,
                                                    body.target, body.params, body.justification, principal.ip)
    except RemediationError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/tickets", response_model=list[TicketOut])
def list_tickets(incident_id: int | None = None, limit: int = Query(100, ge=1, le=500),
                 _: Principal = Depends(viewer), container: Container = Depends(get_container)):
    return container.remediation.list_tickets(incident_id, limit)


@router.post("/tickets", response_model=TicketOut, status_code=201)
def create_ticket(incident_id: int, title: str = Query(..., min_length=3, max_length=255), description: str = "",
                  priority: str = Query("P3", pattern="^P[1-4]$"), principal: Principal = Depends(operator),
                  container: Container = Depends(get_container)):
    if container.incidents.get(incident_id) is None:
        raise HTTPException(404, "Incident not found")
    return container.remediation.create_ticket(incident_id, title, description, priority, principal.email)
