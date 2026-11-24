"""Agent service: runs the LangGraph incident workflow, persists its results and processes the job queue."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from agents.common.ports import AgentContext
from agents.common.timeutil import parse_ts, utcnow
from agents.incident_agent.detector import detect
from agents.learning_agent.agent import incident_document
from agents.monitoring_agent.normalizer import NormalizationAgent
from backend import metrics
from backend.config import Settings
from backend.db import Database
from backend.kv import KeyValueStore
from backend.models import Incident, RCAReport
from backend.realtime import EventBus
from services.audit_service import AuditService
from services.incident_service import TERMINAL_STATUSES, IncidentService, incident_event
from services.remediation_service import JOB_QUEUE, RemediationService
from workflows.incident_workflow import build_workflow, run_workflow
from workflows.state import WorkflowState

logger = logging.getLogger(__name__)

AGENT_REGISTRY = [
    ("monitoring_agent", "Collects logs, metrics, traces and alerts", "Logs, metrics, traces", "Incident signals"),
    ("normalization_agent", "Normalizes data into a canonical schema", "Raw signals", "Normalized signals"),
    ("incident_detection_agent", "Detects anomalies and classifies incidents", "Incident signals", "Incident classification"),
    ("severity_classification_agent", "Scores severity P1-P4", "Classification, service tiers", "Severity"),
    ("correlation_agent", "Correlates signals and maps dependencies", "Metrics + logs + traces", "Dependency analysis"),
    ("rca_agent", "Finds probable root causes with confidence", "Correlated data", "Root cause"),
    ("knowledge_agent", "Retrieves historical incidents and runbooks (RAG)", "Root cause", "Historical incidents"),
    ("recommendation_agent", "Ranks remediation actions", "Root cause + KB", "Resolution suggestions"),
    ("approval_agent", "Gates remediation behind human approval", "Remediation plan", "Approved action"),
    ("remediation_agent", "Executes approved actions", "Approved action", "Infrastructure recovery"),
    ("verification_agent", "Verifies the service recovered", "Recovery status", "Healthy state"),
    ("learning_agent", "Feeds outcomes back into the knowledge base", "Resolution details", "Knowledge update"),
    ("reporting_agent", "Produces the incident/RCA report", "All results", "Incident report"),
]

STATUS_MAP = {
    "awaiting_approval": "awaiting_approval",
    "resolved": "resolved",
    "monitoring": "monitoring",
    "escalated": "escalated",
    "rejected": "escalated",
    "no_incident": "false_positive",
    "error": "open",
}


class AgentService:
    def __init__(self, settings: Settings, db: Database, kv: KeyValueStore, events: EventBus, incidents: IncidentService,
                 remediation: RemediationService, audit: AuditService, ctx: AgentContext) -> None:
        self._s, self._db, self._kv, self._events = settings, db, kv, events
        self._incidents, self._remediation, self._audit, self.ctx = incidents, remediation, audit, ctx
        self._graph = build_workflow(ctx)

    # ------------------------------------------------------------------ queueing
    def enqueue_analysis(self, incident_id: int, reason: str = "") -> None:
        self._kv.enqueue(JOB_QUEUE, {"type": "analyze", "incident_id": incident_id, "reason": reason,
                                     "enqueued_at": utcnow().isoformat()})
        metrics.QUEUE_DEPTH.set(self._kv.queue_length(JOB_QUEUE))

    def process_job(self, job: dict[str, Any]) -> None:
        incident_id = int(job.get("incident_id") or 0)
        if not incident_id:
            return
        lock = f"lock:incident:{incident_id}"
        if self._kv.incr(lock, ttl=900) > 1:
            logger.info("Incident %s is already being processed; skipping %s job", incident_id, job.get("type"))
            return
        try:
            if job.get("type") == "resume":
                self.resume(incident_id)
            else:
                self.run_analysis(incident_id, job.get("enqueued_at"))
        except Exception:
            logger.exception("Workflow job failed for incident %s", incident_id)
            self._mark_error(incident_id)
        finally:
            self._kv.delete(lock)
            metrics.QUEUE_DEPTH.set(self._kv.queue_length(JOB_QUEUE))

    # ------------------------------------------------------------------ workflow execution
    def run_analysis(self, incident_id: int, enqueued_at: str | None = None) -> WorkflowState:
        with self._db.session() as session:
            incident = session.get(Incident, incident_id)
            if incident is None:
                raise ValueError(f"incident {incident_id} not found")
            service = incident.service
            incident.workflow_status = "running"
            if incident.status == "open":
                incident.status = "investigating"
            event = incident_event(incident)
        self._events.publish("incident.updated", event)
        initial: WorkflowState = {
            "incident_id": incident_id,
            "trigger": {"service": service, "window_minutes": self._s.detection_window_minutes, "reason": "incident"},
            "status": "running", "executions": [], "events": [], "errors": [], "attempts": 0,
        }
        state = run_workflow(self._graph, initial)
        self._apply_result(incident_id, state, new_run=True)
        if enqueued_at:
            metrics.ALERT_PROCESSING.observe(max((utcnow() - parse_ts(enqueued_at)).total_seconds(), 0))
        metrics.WORKFLOW_RUNS.labels(status=state.get("status", "unknown")).inc()
        return state

    def resume(self, incident_id: int) -> WorkflowState:
        with self._db.session() as session:
            incident = session.get(Incident, incident_id)
            if incident is None or not incident.workflow_state:
                raise ValueError(f"no workflow state to resume for incident {incident_id}")
            state: WorkflowState = dict(incident.workflow_state)  # type: ignore[assignment]
            incident.workflow_status = "running"
        state["resume_stage"] = "approval"
        state["status"] = "running"
        final = run_workflow(self._graph, state)
        self._apply_result(incident_id, final, new_run=False)
        metrics.WORKFLOW_RUNS.labels(status=final.get("status", "unknown")).inc()
        return final

    def _apply_result(self, incident_id: int, state: WorkflowState, new_run: bool) -> None:
        persisted = {k: v for k, v in state.items() if k not in ("signals", "raw_signals")}
        status = state.get("status", "error")
        detection = state.get("detection") or {}
        rca = state.get("rca") or {}
        report = state.get("report") or {}
        with self._db.session() as session:
            incident = session.get(Incident, incident_id)
            if incident is None:
                return
            previous_status = incident.status
            incident.workflow_state = persisted
            incident.workflow_status = status
            incident.status = STATUS_MAP.get(status, "open") if incident.status not in TERMINAL_STATUSES else incident.status
            if status == "no_incident":
                incident.summary = incident.summary or "No anomalies were found in the analysis window."
            if detection.get("is_incident"):
                incident.title = detection.get("title", incident.title)[:255]
                incident.affected_services = sorted(set(detection.get("affected_services", [])) | {incident.service})
                incident.detection = {k: detection[k] for k in ("score", "anomalies", "summary", "category_hint") if k in detection}
                incident.summary = report.get("summary") or detection.get("summary", incident.summary)
            if state.get("severity"):
                incident.severity = state["severity"]["level"]
            if rca:
                incident.category = rca.get("category", incident.category)
            if state.get("correlation"):
                corr = state["correlation"]
                incident.correlation = {k: corr[k] for k in ("root_candidates", "service_impact", "propagation_paths",
                                                             "failing_calls", "recent_deployments", "timeline") if k in corr}
            if state.get("recommendations"):
                incident.recommendations = state["recommendations"].get("actions", [])
            if incident.status == "resolved" and incident.resolved_at is None:
                incident.resolved_at = utcnow()
                metrics.INCIDENT_RESOLUTION.observe(max((incident.resolved_at - incident.created_at).total_seconds(), 0))
                incident.resolution_notes = incident.resolution_notes or (state.get("verification") or {}).get("details", "")
            incident.updated_at = utcnow()
            if rca:
                self._upsert_rca(session, incident, state, report, new_run)
            session.flush()
            event = incident_event(incident)
            escalated = incident.status == "escalated" and previous_status != "escalated"
            severity, title = incident.severity, incident.title
        self._audit.log("workflow.completed", "agent:orchestrator", resource_type="incident", resource_id=incident_id,
                        details={"status": status, "category": rca.get("category"), "confidence": rca.get("confidence")})
        self._events.publish("incident.updated", event)
        if escalated and not any(e["action"] == "open_ticket" for e in state.get("executions", [])):
            self._remediation.create_ticket(incident_id, f"[{severity}] Escalation: {title}",
                                            "Automated remediation did not resolve the incident; human follow-up required.", severity)

    @staticmethod
    def _upsert_rca(session, incident: Incident, state: WorkflowState, report: dict[str, Any], new_run: bool) -> None:  # noqa: ANN001
        rca = state["rca"]
        corr = state.get("correlation") or {}
        existing = None if new_run else session.scalars(
            select(RCAReport).where(RCAReport.incident_id == incident.id).order_by(RCAReport.id.desc())).first()
        row = existing or RCAReport(incident_id=incident.id)
        row.root_cause = rca.get("root_cause", "")
        row.category = rca.get("category", "unknown")
        row.confidence = float(rca.get("confidence", 0.0))
        row.explanation = rca.get("explanation", "")
        row.contributing_factors = rca.get("contributing_factors", [])
        row.evidence = rca.get("evidence", [])
        row.timeline = report.get("timeline") or corr.get("timeline", [])
        row.recommendations = (state.get("recommendations") or {}).get("actions", [])
        row.content_markdown = report.get("markdown", "")
        if existing is None:
            session.add(row)

    def _mark_error(self, incident_id: int) -> None:
        with self._db.session() as session:
            incident = session.get(Incident, incident_id)
            if incident:
                incident.workflow_status = "error"
                event = incident_event(incident)
        self._events.publish("incident.updated", event)

    # ------------------------------------------------------------------ learning from manual resolution
    def learn_from_resolution(self, incident_id: int, notes: str) -> bool:
        if not self.ctx.kb:
            return False
        with self._db.session() as session:
            incident = session.get(Incident, incident_id)
            if incident is None or not (incident.workflow_state or {}).get("rca"):
                return False
            state: WorkflowState = dict(incident.workflow_state)  # type: ignore[assignment]
        text, metadata = incident_document(state, "resolved")
        metadata["incident_id"] = incident_id
        metadata["resolution_notes"] = notes[:500]
        try:
            self.ctx.kb.upsert(f"inc-{incident_id}", text + f"\nResolution notes: {notes}", "incident", metadata)
            return True
        except Exception:
            logger.exception("Failed to index resolved incident %s", incident_id)
            return False

    # ------------------------------------------------------------------ maintenance
    def auto_resolve_sweep(self) -> list[int]:
        """Resolve incidents whose services have been verifiably healthy again (monitoring status)."""
        resolved: list[int] = []
        with self._db.session() as session:
            candidates = [(i.id, list(i.affected_services) or [i.service], i.updated_at, i.workflow_state or {})
                          for i in session.scalars(select(Incident).where(Incident.status == "monitoring"))]
        for incident_id, services, updated, state in candidates:
            if utcnow() - updated < timedelta(minutes=2):
                continue
            since = utcnow() - timedelta(minutes=3)
            finished = [parse_ts(e["finished_at"]) for e in state.get("executions", []) if e.get("finished_at")]
            if finished:  # signals emitted before the remediation finished say nothing about recovery
                since = max(since, max(finished))
            raw: dict[str, list] = {"logs": [], "metrics": [], "traces": [], "alerts": []}
            for svc in services:
                for kind, key in (("log", "logs"), ("metric", "metrics"), ("trace", "traces")):
                    raw[key].extend(self.ctx.signals.query(kind, svc, since=since))
            norm = NormalizationAgent().run({"raw_signals": raw})["signals"]
            if not norm["metrics"]:
                continue  # no fresh data: cannot claim recovery
            result = detect(norm, since=since, current_only=True)
            if result["score"] < 1:
                try:
                    self._incidents.mark_resolved(incident_id, "Auto-resolved: metrics returned to healthy ranges", "system")
                except Exception:
                    logger.exception("Auto-resolution of incident %s failed", incident_id)
                    continue
                self.learn_from_resolution(incident_id, "Auto-resolved after verified recovery")
                resolved.append(incident_id)
        return resolved

    # ------------------------------------------------------------------ worker
    async def worker(self, name: str) -> None:
        logger.info("Workflow worker %s started", name)
        while True:
            try:
                job = await asyncio.to_thread(self._kv.dequeue, JOB_QUEUE, 2)
                if job:
                    await asyncio.to_thread(self.process_job, job)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker %s loop error", name)
                await asyncio.sleep(2)

    def registry(self) -> list[dict[str, Any]]:
        return [{"name": n, "description": d, "input": i, "output": o} for n, d, i, o in AGENT_REGISTRY]

