"""Health probes and Prometheus metrics."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from backend.container import Container
from backend.deps import get_container
from backend.metrics import REGISTRY
from backend.security import constant_time_in

router = APIRouter(tags=["system"])


@router.get("/health/live", summary="Liveness probe")
def live() -> dict:
    return {"status": "ok"}


@router.get("/health/ready", summary="Readiness probe (database, cache/queue, knowledge base)")
def ready(response: Response, container: Container = Depends(get_container)) -> dict:
    checks = {
        "database": container.db.ping(),
        "kv": container.kv.ping(),
        "knowledge_base": container.kb is not None,
    }
    healthy = checks["database"] and checks["kv"]  # RAG is degradable, the others are not
    if not healthy:
        response.status_code = 503
    return {"status": "ready" if healthy else "unavailable", "checks": checks, "version": container.settings.app_version,
            "llm": container.llm is not None, "remediation_mode": container.settings.remediation_mode}


@router.get("/metrics", include_in_schema=False)
def prometheus_metrics(request: Request, container: Container = Depends(get_container)) -> Response:
    token = container.settings.metrics_token.get_secret_value()
    if token:
        header = request.headers.get("authorization", "")
        if not (header.startswith("Bearer ") and constant_time_in(header[7:], [token])):
            raise HTTPException(401, "Metrics token required")
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
