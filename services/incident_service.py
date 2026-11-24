"""Incident lifecycle management and analytics."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from agents.common.timeutil import utcnow
from backend import metrics
from backend.db import Database
from backend.models import Alert, ApprovalRequest, Incident, RCAReport, RemediationExecution, Ticket
from backend.realtime import EventBus
from services.audit_service import AuditService

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("open", "investigating", "awaiting_approval", "remediating", "monitoring", "escalated")
TERMINAL_STATUSES = ("resolved", "false_positive", "closed")
SEVERITIES = ("P1", "P2", "P3", "P4")


class IncidentError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def incident_event(i: Incident) -> dict[str, Any]:
    return {"id": i.id, "title": i.title, "service": i.service, "severity": i.severity, "status": i.status,
            "category": i.category, "workflow_status": i.workflow_status, "updated_at": i.updated_at.isoformat()}


class IncidentService:
    def __init__(self, db: Database, events: EventBus, audit: AuditService) -> None:
        self._db, self._events, self._audit = db, events, audit

    # ------------------------------------------------------------------ create / find
    def create(self, title: str, service: str, severity: str = "P3", summary: str = "", category: str = "unknown",
               affected: list[str] | None = None, detection: dict[str, Any] | None = None, status: str = "open",
               actor: str = "system") -> Incident:
        affected = sorted({*(affected or []), service.lower()})
        with self._db.session() as session:
            incident = Incident(title=title[:255] or f"Incident on {service}", service=service.lower(), severity=severity,
                                summary=summary, category=category, affected_services=affected, detection=detection or {},
                                status=status, dedup_key=f"{service.lower()}:{category}")
            session.add(incident)
            session.flush()
            session.refresh(incident)
            if affected:  # link currently firing alerts of affected services to the incident
                for alert in session.scalars(select(Alert).where(
                        Alert.service.in_(affected), Alert.incident_id.is_(None), Alert.status == "firing")):
                    alert.incident_id = incident.id
            event = incident_event(incident)
        metrics.INCIDENTS_CREATED.labels(severity=severity).inc()
        self._audit.log("incident.created", actor, resource_type="incident", resource_id=incident.id,
                        details={"service": service, "severity": severity, "title": incident.title})
        self._events.publish("incident.created", event)
        return incident

    def find_active(self, services: list[str]) -> Incident | None:
        names = [s.lower() for s in services]
        with self._db.session() as session:
            rows = session.scalars(select(Incident).where(Incident.status.in_(ACTIVE_STATUSES)).order_by(Incident.id.desc()))
            for incident in rows:
                if incident.service in names or set(incident.affected_services) & set(names):
                    return incident
        return None

    def resolution_cutoff(self, services: list[str], window_minutes: int) -> datetime | None:
        """Latest resolution time of recent incidents touching `services` (older signals must not re-open them)."""
        names = {s.lower() for s in services}
        since = utcnow() - timedelta(minutes=window_minutes)
        with self._db.session() as session:
            cutoffs = [
                i.resolved_at for i in session.scalars(select(Incident).where(Incident.resolved_at >= since))
                if i.resolved_at and (i.service in names or set(i.affected_services) & names)
            ]
        return max(cutoffs) if cutoffs else None

    def touch(self, incident_id: int, affected: list[str]) -> None:
        with self._db.session() as session:
            incident = session.get(Incident, incident_id)
            if incident:
                incident.affected_services = sorted(set(incident.affected_services) | {a.lower() for a in affected})
                incident.updated_at = utcnow()

    # ------------------------------------------------------------------ read
    def get(self, incident_id: int) -> Incident | None:
        with self._db.session() as session:
            return session.get(Incident, incident_id)

    def list(self, status: str | None = None, severity: str | None = None, service: str | None = None,
             active_only: bool = False, limit: int = 50, offset: int = 0) -> tuple[list[Incident], int]:
        with self._db.session() as session:
            query, count = select(Incident), select(func.count(Incident.id))
            conds = []
            if status:
                conds.append(Incident.status == status)
            if severity:
                conds.append(Incident.severity == severity)
            if service:
                conds.append(Incident.service == service.lower())
            if active_only:
                conds.append(Incident.status.in_(ACTIVE_STATUSES))
            for cond in conds:
                query, count = query.where(cond), count.where(cond)
            rows = session.scalars(query.order_by(Incident.created_at.desc()).limit(limit).offset(offset)).all()
            return list(rows), int(session.scalar(count) or 0)

    def detail(self, incident_id: int) -> dict[str, Any] | None:
        with self._db.session() as session:
            incident = session.get(Incident, incident_id)
            if incident is None:
                return None
            state = incident.workflow_state or {}
            rca = session.scalars(select(RCAReport).where(RCAReport.incident_id == incident_id).order_by(RCAReport.id.desc())).first()
            approvals = session.scalars(select(ApprovalRequest).where(ApprovalRequest.incident_id == incident_id).order_by(ApprovalRequest.id)).all()
            executions = session.scalars(select(RemediationExecution).where(RemediationExecution.incident_id == incident_id).order_by(RemediationExecution.id)).all()
            tickets = session.scalars(select(Ticket).where(Ticket.incident_id == incident_id).order_by(Ticket.id)).all()
            return {
                "incident": incident, "rca": rca, "approvals": list(approvals), "executions": list(executions),
                "tickets": list(tickets), "events": state.get("events", []), "verification": state.get("verification", {}),
            }

    # ------------------------------------------------------------------ mutate
    def set_status(self, incident_id: int, status: str, actor: str, role: str = "") -> Incident:
        with self._db.session() as session:
            incident = session.get(Incident, incident_id)
            if incident is None:
                raise IncidentError("Incident not found", 404)
            previous = incident.status
            incident.status = status
            incident.updated_at = utcnow()
            session.flush()
            event = incident_event(incident)
        self._audit.log("incident.status_changed", actor, role, "incident", incident_id, {"from": previous, "to": status})
        self._events.publish("incident.updated", event)
        return incident

    def mark_resolved(self, incident_id: int, notes: str, actor: str, role: str = "") -> Incident:
        with self._db.session() as session:
            incident = session.get(Incident, incident_id)
            if incident is None:
                raise IncidentError("Incident not found", 404)
            if incident.status in TERMINAL_STATUSES:
                raise IncidentError(f"Incident is already {incident.status}", 409)
            incident.status = "resolved"
            incident.resolved_at = utcnow()
            incident.resolution_notes = notes
            incident.updated_at = utcnow()
            session.flush()
            duration = (incident.resolved_at - incident.created_at).total_seconds()
            event = incident_event(incident)
        metrics.INCIDENT_RESOLUTION.observe(max(duration, 0))
        self._audit.log("incident.resolved", actor, role, "incident", incident_id, {"notes": notes})
        self._events.publish("incident.updated", event)
        return incident

    # ------------------------------------------------------------------ analytics
    def refresh_gauges(self) -> int:
        with self._db.session() as session:
            open_count = int(session.scalar(select(func.count(Incident.id)).where(Incident.status.in_(ACTIVE_STATUSES))) or 0)
        metrics.INCIDENTS_OPEN.set(open_count)
        return open_count

    def stats(self, days: int = 30) -> dict[str, Any]:
        since = utcnow() - timedelta(days=days)
        with self._db.session() as session:
            incidents = list(session.scalars(select(Incident).where(Incident.created_at >= since)))
            executions = list(session.scalars(select(RemediationExecution).where(RemediationExecution.started_at >= since)))
            reports = list(session.scalars(select(RCAReport).where(RCAReport.feedback.is_not(None))))
            alerts = int(session.scalar(select(func.count(Alert.id)).where(Alert.received_at >= since)) or 0)
        resolved = [i for i in incidents if i.resolved_at]
        mttr = [(i.resolved_at - i.created_at).total_seconds() for i in resolved]
        daily: dict[str, Counter[str]] = {}
        for i in incidents:
            daily.setdefault(i.created_at.date().isoformat(), Counter())[i.severity] += 1
        recovery = [e for e in executions if e.action != "open_ticket"]
        automated_ok = [e for e in recovery if e.status in ("succeeded", "simulated")]
        correct = sum(1 for r in reports if r.feedback == "correct")
        return {
            "window_days": days,
            "total_incidents": len(incidents),
            "active_incidents": sum(i.status in ACTIVE_STATUSES for i in incidents),
            "resolved_incidents": len(resolved),
            "mttr_seconds": round(sum(mttr) / len(mttr), 1) if mttr else None,
            "by_severity": dict(Counter(i.severity for i in incidents)),
            "by_category": dict(Counter(i.category for i in incidents)),
            "by_service": dict(Counter(i.service for i in incidents).most_common(10)),
            "by_status": dict(Counter(i.status for i in incidents)),
            "trend": [{"date": d, **{s: c.get(s, 0) for s in SEVERITIES}, "total": sum(c.values())} for d, c in sorted(daily.items())],
            "alerts_received": alerts,
            "remediation_actions": len(recovery),
            "remediation_success_rate": round(len(automated_ok) / len(recovery), 3) if recovery else None,
            "automated_remediation_coverage": (
                round(len({e.incident_id for e in automated_ok if e.incident_id}) / len(incidents), 3) if incidents else None),
            "rca_accuracy": round(correct / len(reports), 3) if reports else None,
            "rca_feedback_count": len(reports),
        }
