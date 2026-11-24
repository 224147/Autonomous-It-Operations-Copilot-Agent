"""Incident, RCA, agent orchestration and reporting endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from backend.container import Container
from backend.deps import Principal, get_container, operator, viewer
from backend.schemas import (
    ApprovalOut,
    ExecutionOut,
    IncidentCreate,
    IncidentDetail,
    IncidentOut,
    IncidentResolve,
    IncidentStatusUpdate,
    Page,
    RCAFeedback,
    RCAReportOut,
    TicketOut,
    WorkflowRunRequest,
)
from services.incident_service import IncidentError
from services.rca_service import RCAError

router = APIRouter(tags=["incidents"])


def _detail(container: Container, incident_id: int) -> IncidentDetail:
    data = container.incidents.detail(incident_id)
    if data is None:
        raise HTTPException(404, "Incident not found")
    base = IncidentOut.model_validate(data["incident"]).model_dump()
    inc = data["incident"]
    return IncidentDetail(
        **base, detection=inc.detection, correlation=inc.correlation, recommendations=inc.recommendations,
        resolution_notes=inc.resolution_notes,
        rca=RCAReportOut.model_validate(data["rca"]) if data["rca"] else None,
        approvals=[ApprovalOut.model_validate(a) for a in data["approvals"]],
        executions=[ExecutionOut.model_validate(e) for e in data["executions"]],
        tickets=[TicketOut.model_validate(t) for t in data["tickets"]],
        events=data["events"], verification=data["verification"],
    )


# ------------------------------------------------------------------ incidents
@router.get("/incidents", response_model=Page)
def list_incidents(status_: str | None = Query(None, alias="status"), severity: str | None = None,
                   service: str | None = None, active: bool = False, limit: int = Query(50, ge=1, le=200),
                   offset: int = Query(0, ge=0), _: Principal = Depends(viewer),
                   container: Container = Depends(get_container)) -> Page:
    rows, total = container.incidents.list(status_, severity, service, active, limit, offset)
    return Page(items=[IncidentOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/incidents/stats", summary="Incident analytics (counts, trend, MTTR)")
def incident_stats(days: int = Query(30, ge=1, le=365), _: Principal = Depends(viewer),
                   container: Container = Depends(get_container)) -> dict[str, Any]:
    return container.incidents.stats(days)


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: int, _: Principal = Depends(viewer), container: Container = Depends(get_container)):
    return _detail(container, incident_id)


@router.post("/incidents", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
def create_incident(body: IncidentCreate, principal: Principal = Depends(operator),
                    container: Container = Depends(get_container)):
    if container.inventory.get_service(body.service) is None:
        raise HTTPException(422, f"Service '{body.service}' is not in the inventory")
    incident = container.incidents.create(
        title=body.title or f"Manually reported incident on {body.service}", service=body.service, severity=body.severity,
        summary=body.description, status="investigating", actor=principal.email)
    container.agents.enqueue_analysis(incident.id, "manual")
    return incident


@router.post("/incidents/{incident_id}/analyze", status_code=status.HTTP_202_ACCEPTED,
             summary="Re-run the multi-agent workflow for an incident")
def analyze_incident(incident_id: int, principal: Principal = Depends(operator),
                     container: Container = Depends(get_container)) -> dict:
    if container.incidents.get(incident_id) is None:
        raise HTTPException(404, "Incident not found")
    container.agents.enqueue_analysis(incident_id, "manual-reanalysis")
    container.audit.log("workflow.requested", principal.email, principal.role, "incident", incident_id, ip=principal.ip)
    return {"queued": True, "incident_id": incident_id}


@router.post("/incidents/{incident_id}/resolve", response_model=IncidentOut)
def resolve_incident(incident_id: int, body: IncidentResolve, principal: Principal = Depends(operator),
                     container: Container = Depends(get_container)):
    try:
        incident = container.incidents.mark_resolved(incident_id, body.notes, principal.email, principal.role)
    except IncidentError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    container.agents.learn_from_resolution(incident_id, body.notes)  # Learning Agent: feed the knowledge base
    return incident


@router.patch("/incidents/{incident_id}/status", response_model=IncidentOut)
def update_status(incident_id: int, body: IncidentStatusUpdate, principal: Principal = Depends(operator),
                  container: Container = Depends(get_container)):
    try:
        return container.incidents.set_status(incident_id, body.status, principal.email, principal.role)
    except IncidentError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/incidents/{incident_id}/rca", response_model=RCAReportOut)
def incident_rca(incident_id: int, _: Principal = Depends(viewer), container: Container = Depends(get_container)):
    report = container.rca.for_incident(incident_id)
    if report is None:
        raise HTTPException(404, "No RCA report yet for this incident")
    return report


# ------------------------------------------------------------------ RCA service
@router.get("/rca", response_model=Page)
def list_rca(category: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
             _: Principal = Depends(viewer), container: Container = Depends(get_container)) -> Page:
    rows, total = container.rca.list(limit, offset, category)
    return Page(items=[RCAReportOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/rca/accuracy")
def rca_accuracy(_: Principal = Depends(viewer), container: Container = Depends(get_container)) -> dict:
    return container.rca.accuracy()


@router.get("/rca/{report_id}", response_model=RCAReportOut)
def get_rca(report_id: int, _: Principal = Depends(viewer), container: Container = Depends(get_container)):
    report = container.rca.get(report_id)
    if report is None:
        raise HTTPException(404, "RCA report not found")
    return report


@router.post("/rca/{report_id}/feedback", response_model=RCAReportOut)
def rca_feedback(report_id: int, body: RCAFeedback, principal: Principal = Depends(operator),
                 container: Container = Depends(get_container)):
    try:
        return container.rca.feedback(report_id, body.result, body.notes, principal.email, principal.role)
    except RCAError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/rca/{report_id}/markdown", response_class=Response)
def rca_markdown(report_id: int, _: Principal = Depends(viewer), container: Container = Depends(get_container)):
    report = container.rca.get(report_id)
    if report is None:
        raise HTTPException(404, "RCA report not found")
    return Response(report.content_markdown or "# RCA report not finalised yet", media_type="text/markdown")


# ------------------------------------------------------------------ agent service
@router.get("/agents", summary="Registered agents and workflow queue state")
def list_agents(_: Principal = Depends(viewer), container: Container = Depends(get_container)) -> dict:
    return {
        "agents": container.agents.registry(),
        "workflow": [
            "monitoring", "normalization", "incident_detection", "severity", "correlation", "rca", "knowledge",
            "recommendation", "human_approval", "remediation", "verification", "learning", "reporting",
        ],
        "llm": container.llm.name if container.llm else None,
        "embedder": container.kb.embedder.id if container.kb else None,
        "remediation_mode": container.settings.remediation_mode,
        "queue_depth": container.kv.queue_length("workflow:jobs"),
    }


@router.post("/agents/workflows/run", status_code=status.HTTP_202_ACCEPTED,
             summary="Start an investigation for a service (creates an incident and queues the workflow)")
def run_workflow_for_service(body: WorkflowRunRequest, principal: Principal = Depends(operator),
                             container: Container = Depends(get_container)) -> dict:
    if container.inventory.get_service(body.service) is None:
        raise HTTPException(422, f"Service '{body.service}' is not in the inventory")
    incident = container.incidents.create(
        title=f"Investigation of {body.service}", service=body.service, summary=f"Manual investigation: {body.reason}",
        status="investigating", actor=principal.email)
    container.agents.enqueue_analysis(incident.id, body.reason)
    return {"queued": True, "incident_id": incident.id}


@router.get("/agents/workflows/{incident_id}", summary="Persisted workflow state for an incident")
def workflow_state(incident_id: int, _: Principal = Depends(viewer), container: Container = Depends(get_container)) -> dict:
    incident = container.incidents.get(incident_id)
    if incident is None:
        raise HTTPException(404, "Incident not found")
    state = incident.workflow_state or {}
    return {"incident_id": incident_id, "workflow_status": incident.workflow_status, "events": state.get("events", []),
            "errors": state.get("errors", []), "severity": state.get("severity"), "approval": state.get("approval"),
            "verification": state.get("verification"), "learning": state.get("learning")}


# ------------------------------------------------------------------ reporting service
@router.get("/reporting/incidents/{incident_id}")
def incident_report(incident_id: int, _: Principal = Depends(viewer), container: Container = Depends(get_container)) -> dict:
    report = container.reporting.incident_report(incident_id)
    if report is None:
        raise HTTPException(404, "Incident not found")
    return report


@router.get("/reporting/incidents/{incident_id}/markdown", response_class=Response)
def incident_report_markdown(incident_id: int, _: Principal = Depends(viewer), container: Container = Depends(get_container)):
    report = container.reporting.incident_report(incident_id)
    if report is None:
        raise HTTPException(404, "Incident not found")
    return Response(report.get("markdown") or "# Report not available yet", media_type="text/markdown")


@router.get("/reporting/analytics")
def analytics(days: int = Query(30, ge=1, le=365), _: Principal = Depends(viewer),
              container: Container = Depends(get_container)) -> dict:
    return container.reporting.analytics(days)


@router.get("/reporting/kpis", summary="Success-criteria KPIs measured from live data")
def kpis(days: int = Query(30, ge=1, le=365), _: Principal = Depends(viewer),
         container: Container = Depends(get_container)) -> dict:
    return container.reporting.kpis(days)

