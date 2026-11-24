"""Monitoring integrations: signal/alert ingestion, alert management, inventory, deployments and service health."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status

from backend.container import Container
from backend.deps import Principal, admin, get_container, ingest_principal, operator, viewer
from backend.schemas import (
    AlertIn,
    AlertOut,
    DeploymentIn,
    DeploymentOut,
    IngestResult,
    LogBatch,
    MetricBatch,
    Page,
    ServiceIn,
    ServiceOut,
    SpanBatch,
)
from monitoring.alert_ingestion.parsers import parse_alertmanager, parse_azure_monitor, parse_datadog
from monitoring.alert_ingestion.service import AlertError
from monitoring.health import service_health
from monitoring.log_ingestion.loki import parse_loki_push
from monitoring.metric_ingestion.prometheus import parse_otlp_metrics
from monitoring.trace_ingestion.otlp import parse_otlp_traces

router = APIRouter(tags=["monitoring"])
MAX_BODY_BYTES = 10 * 1024 * 1024


async def _json_body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Payload too large")
    try:
        data = await request.json()
    except ValueError as exc:
        raise HTTPException(422, "Body must be valid JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(422, "Body must be a JSON object")
    return data


def _alerts_from(items: list[dict[str, Any]]) -> list[AlertIn]:
    alerts = []
    for item in items:
        try:
            alerts.append(AlertIn(**item))
        except ValueError:
            continue  # skip malformed vendor entries rather than rejecting the whole batch
    return alerts


# ------------------------------------------------------------------ ingestion
@router.post("/ingest/logs", response_model=IngestResult, tags=["ingestion"])
def ingest_logs(body: LogBatch, _: str = Depends(ingest_principal), container: Container = Depends(get_container)):
    return IngestResult(accepted=container.ingestion.ingest_signals("log", body.logs))


@router.post("/ingest/logs/loki", response_model=IngestResult, tags=["ingestion"], summary="Loki push-format logs")
async def ingest_loki(request: Request, _: str = Depends(ingest_principal), container: Container = Depends(get_container)):
    logs = parse_loki_push(await _json_body(request))
    return IngestResult(accepted=container.ingestion.ingest_signals("log", logs))


@router.post("/ingest/metrics", response_model=IngestResult, tags=["ingestion"])
def ingest_metrics(body: MetricBatch, _: str = Depends(ingest_principal), container: Container = Depends(get_container)):
    return IngestResult(accepted=container.ingestion.ingest_signals("metric", body.metrics))


@router.post("/ingest/metrics/otlp", response_model=IngestResult, tags=["ingestion"], summary="OTLP/HTTP JSON metrics")
async def ingest_otlp_metrics(request: Request, _: str = Depends(ingest_principal),
                              container: Container = Depends(get_container)):
    return IngestResult(accepted=container.ingestion.ingest_signals("metric", parse_otlp_metrics(await _json_body(request))))


@router.post("/ingest/traces", response_model=IngestResult, tags=["ingestion"])
def ingest_traces(body: SpanBatch, _: str = Depends(ingest_principal), container: Container = Depends(get_container)):
    return IngestResult(accepted=container.ingestion.ingest_signals("trace", body.spans))


@router.post("/ingest/traces/otlp", response_model=IngestResult, tags=["ingestion"], summary="OTLP/HTTP JSON traces")
async def ingest_otlp_traces(request: Request, _: str = Depends(ingest_principal),
                             container: Container = Depends(get_container)):
    return IngestResult(accepted=container.ingestion.ingest_signals("trace", parse_otlp_traces(await _json_body(request))))


def _ingest_alerts(container: Container, alerts: list[AlertIn]) -> IngestResult:
    accepted, triggered = container.ingestion.ingest_alerts(alerts)
    return IngestResult(accepted=accepted, incidents_triggered=triggered)


@router.post("/ingest/alerts", response_model=IngestResult, tags=["ingestion"])
def ingest_alerts(body: list[AlertIn] = Body(..., max_length=1000), _: str = Depends(ingest_principal),
                  container: Container = Depends(get_container)):
    return _ingest_alerts(container, body)


@router.post("/ingest/alerts/alertmanager", response_model=IngestResult, tags=["ingestion"],
             summary="Prometheus Alertmanager / Grafana webhook")
async def ingest_alertmanager(request: Request, _: str = Depends(ingest_principal),
                              container: Container = Depends(get_container)):
    return _ingest_alerts(container, _alerts_from(parse_alertmanager(await _json_body(request))))


@router.post("/ingest/alerts/azure-monitor", response_model=IngestResult, tags=["ingestion"],
             summary="Azure Monitor common alert schema webhook")
async def ingest_azure(request: Request, _: str = Depends(ingest_principal), container: Container = Depends(get_container)):
    return _ingest_alerts(container, _alerts_from(parse_azure_monitor(await _json_body(request))))


@router.post("/ingest/alerts/datadog", response_model=IngestResult, tags=["ingestion"], summary="Datadog webhook")
async def ingest_datadog(request: Request, _: str = Depends(ingest_principal), container: Container = Depends(get_container)):
    return _ingest_alerts(container, _alerts_from(parse_datadog(await _json_body(request))))


# ------------------------------------------------------------------ alert center
@router.get("/alerts", response_model=Page)
def list_alerts(status_: str | None = Query(None, alias="status", pattern="^(firing|resolved|acknowledged)$"),
                service: str | None = None, severity: str | None = None, limit: int = Query(100, ge=1, le=500),
                offset: int = Query(0, ge=0), _: Principal = Depends(viewer),
                container: Container = Depends(get_container)) -> Page:
    rows, total = container.alerts.list(status_, service, severity, limit, offset)
    return Page(items=[AlertOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertOut)
def acknowledge_alert(alert_id: int, principal: Principal = Depends(operator), container: Container = Depends(get_container)):
    try:
        return container.alerts.acknowledge(alert_id, principal.email, principal.role)
    except AlertError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/alerts/{alert_id}/resolve", response_model=AlertOut)
def resolve_alert(alert_id: int, principal: Principal = Depends(operator), container: Container = Depends(get_container)):
    try:
        return container.alerts.resolve(alert_id, principal.email, principal.role)
    except AlertError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


# ------------------------------------------------------------------ inventory / deployments / health
@router.get("/services", response_model=list[ServiceOut], tags=["inventory"])
def list_services(_: Principal = Depends(viewer), container: Container = Depends(get_container)):
    return container.inventory.list_services()


@router.get("/services/health", tags=["inventory"], summary="Current health of every inventoried service")
def services_health(_: Principal = Depends(viewer), container: Container = Depends(get_container)) -> list[dict]:
    return service_health(container.signals, container.inventory, container.incidents)


@router.put("/services/{name}", response_model=ServiceOut, tags=["inventory"])
def upsert_service(name: str, body: ServiceIn, principal: Principal = Depends(admin),
                   container: Container = Depends(get_container)):
    if body.name != name.lower():
        raise HTTPException(422, "Path name and body name differ")
    service = container.inventory.upsert_service(body)
    container.audit.log("service.upserted", principal.email, principal.role, "service", service.name, ip=principal.ip)
    return service


@router.delete("/services/{name}", status_code=204, tags=["inventory"])
def delete_service(name: str, principal: Principal = Depends(admin), container: Container = Depends(get_container)) -> None:
    if not container.inventory.delete_service(name):
        raise HTTPException(404, "Service not found")
    container.audit.log("service.deleted", principal.email, principal.role, "service", name, ip=principal.ip)


@router.get("/deployments", response_model=list[DeploymentOut], tags=["inventory"])
def list_deployments(service: str | None = None, limit: int = Query(100, ge=1, le=500), _: Principal = Depends(viewer),
                     container: Container = Depends(get_container)):
    return container.inventory.list_deployments(service, limit)


@router.post("/deployments", response_model=DeploymentOut, status_code=201, tags=["inventory"],
             summary="Record a deployment (called by CI/CD; used for RCA correlation)")
def record_deployment(body: DeploymentIn, actor: str = Depends(ingest_principal), container: Container = Depends(get_container)):
    deployment = container.inventory.record_deployment(body)
    container.audit.log("deployment.recorded", actor, resource_type="deployment", resource_id=deployment.id,
                        details={"service": deployment.service, "version": deployment.version})
    container.events.publish("deployment.recorded", {"id": deployment.id, "service": deployment.service,
                                                     "version": deployment.version, "status": deployment.status})
    return deployment
