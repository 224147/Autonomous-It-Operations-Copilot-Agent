"""FastAPI application factory (API gateway for the Autonomous IT Operations Copilot)."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend import metrics
from backend.api import auth, incidents, knowledge, monitoring, remediation, system
from backend.config import Settings, get_settings
from backend.container import Container, build_container
from backend.logging_config import configure_logging, request_id_var
from monitoring.alert_ingestion.service import AlertError
from services.auth_service import AuthError
from services.incident_service import IncidentError
from services.knowledge_service import KnowledgeError
from services.rca_service import RCAError
from services.remediation_service import RemediationError
from services.user_service import UserError

logger = logging.getLogger(__name__)
API_PREFIX = "/api/v1"
SERVICE_ERRORS = (AuthError, UserError, RemediationError, IncidentError, RCAError, KnowledgeError, AlertError)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


def _setup_tracing(app: FastAPI, settings: Settings) -> None:
    if not settings.otel_exporter_otlp_endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": settings.otel_service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
            endpoint=settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces")))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app, excluded_urls="health/.*,metrics")
        logger.info("OpenTelemetry tracing enabled -> %s", settings.otel_exporter_otlp_endpoint)
    except Exception:
        logger.exception("Failed to initialise OpenTelemetry; continuing without tracing")


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or (container.settings if container else get_settings())
    configure_logging(settings.log_level, settings.log_json)
    settings.validate_for_runtime()
    owns_container = container is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal container
        if container is None:
            container = build_container(settings)
        app.state.container = container
        container.initialize()
        loop = asyncio.get_running_loop()
        container.events.attach_loop(loop)

        def validate(token: str):
            try:
                return container.auth.user_from_access_token(token)[0]
            except AuthError:
                return None

        container.events.token_validator = validate
        metrics.APP_UP.set(1)
        tasks: list[asyncio.Task] = []
        if settings.worker_enabled:
            container.scheduler.start()
            tasks = [asyncio.create_task(container.agents.worker(f"worker-{i}")) for i in range(max(1, settings.worker_concurrency))]
        logger.info("%s %s started (env=%s, remediation=%s, llm=%s)", settings.app_name, settings.app_version,
                    settings.app_env, settings.remediation_mode, container.llm.name if container.llm else "none")
        try:
            yield
        finally:
            metrics.APP_UP.set(0)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await container.scheduler.stop()
            if owns_container:
                container.close()

    app = FastAPI(
        title=settings.app_name, version=settings.app_version, lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs", redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "X-API-Key"], max_age=600)

    @app.middleware("http")
    async def observability(request: Request, call_next):  # noqa: ANN001
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled error on %s %s", request.method, request.url.path)
            response = JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
        finally:
            request_id_var.reset(token)
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        if route is not None and request.url.path.startswith(API_PREFIX) and not path.startswith(API_PREFIX):
            path = API_PREFIX + path  # newer FastAPI exposes the router-relative path
        elapsed = time.perf_counter() - started
        metrics.HTTP_REQUESTS.labels(request.method, path, response.status_code).inc()
        metrics.HTTP_LATENCY.labels(request.method, path).observe(elapsed)
        response.headers["X-Request-ID"] = rid
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        if path.startswith(API_PREFIX):
            response.headers.setdefault("Cache-Control", "no-store")
        if not path.startswith("/health") and path != "/metrics":
            logger.info("%s %s -> %s (%.0fms)", request.method, request.url.path, response.status_code, elapsed * 1000)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exc(_: Request, exc: StarletteHTTPException):  # noqa: ANN001
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError):  # noqa: ANN001
        return JSONResponse({"detail": str(exc)}, status_code=422)

    for error_type in SERVICE_ERRORS:
        @app.exception_handler(error_type)
        async def service_error(_: Request, exc):  # noqa: ANN001
            return JSONResponse({"detail": str(exc)}, status_code=getattr(exc, "status_code", 400))

    for module in (auth, incidents, knowledge, monitoring, remediation):
        app.include_router(module.router, prefix=API_PREFIX)
    app.include_router(system.router)

    # Socket.IO is mounted lazily because the EventBus lives in the container created at startup.
    class _SocketProxy:
        def __init__(self) -> None:
            self._app = None

        async def __call__(self, scope, receive, send):  # noqa: ANN001
            if self._app is None:
                self._app = socketio.ASGIApp(app.state.container.events.sio, socketio_path="ws/socket.io")
            await self._app(scope, receive, send)

    app.mount("/ws", _SocketProxy())
    _setup_tracing(app, settings)
    return app


app = create_app()
