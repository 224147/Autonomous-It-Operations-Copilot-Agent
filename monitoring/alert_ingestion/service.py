"""Alert storage/aggregation, signal ingestion and the incident trigger service."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from agents.common.ports import PlatformData
from agents.common.timeutil import parse_ts, utcnow
from agents.incident_agent.detector import detect
from agents.monitoring_agent.agent import resolve_scope
from agents.monitoring_agent.normalizer import NormalizationAgent
from backend import metrics
from backend.config import Settings
from backend.db import Database
from backend.kv import KeyValueStore
from backend.models import Alert
from backend.realtime import EventBus
from backend.schemas import AlertIn
from monitoring.alert_ingestion.parsers import fingerprint
from monitoring.signal_store import SignalStore
from services.audit_service import AuditService
from services.incident_service import IncidentService

logger = logging.getLogger(__name__)


class AlertError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class AlertService:
    """Alert aggregation: stores alerts, de-duplicates by fingerprint and tracks firing/resolved state."""

    def __init__(self, db: Database, events: EventBus, audit: AuditService) -> None:
        self._db, self._events, self._audit = db, events, audit

    def ingest(self, alerts: list[AlertIn]) -> list[Alert]:
        stored: list[Alert] = []
        with self._db.session() as session:
            for item in alerts:
                service = item.service.lower()
                fp = item.fingerprint or fingerprint(item.name, service, item.labels)
                existing = session.scalar(select(Alert).where(Alert.fingerprint == fp, Alert.status != "resolved")) \
                    if item.status == "resolved" else session.scalar(
                        select(Alert).where(Alert.fingerprint == fp, Alert.status.in_(("firing", "acknowledged"))))
                if existing is not None:
                    existing.summary = item.summary or existing.summary
                    existing.severity = item.severity or existing.severity
                    if item.status == "resolved":
                        existing.status, existing.ends_at = "resolved", item.ends_at or utcnow()
                    stored.append(existing)
                    continue
                if item.status == "resolved":
                    continue  # resolution for an alert we never saw
                alert = Alert(fingerprint=fp, source=item.source, name=item.name, service=service, severity=item.severity,
                              status="firing", summary=item.summary, labels=item.labels, annotations=item.annotations,
                              starts_at=item.starts_at or utcnow(), received_at=utcnow())
                session.add(alert)
                stored.append(alert)
            session.flush()
            for alert in stored:
                session.refresh(alert)
            payloads = [{"id": a.id, "name": a.name, "service": a.service, "severity": a.severity, "status": a.status}
                        for a in stored]
        for item in alerts:
            metrics.ALERTS_RECEIVED.labels(source=item.source).inc()
        for payload in payloads:
            self._events.publish("alert.received", payload)
        return stored

    def list(self, status: str | None = None, service: str | None = None, severity: str | None = None,
             limit: int = 100, offset: int = 0) -> tuple[list[Alert], int]:
        with self._db.session() as session:
            query, count = select(Alert), select(func.count(Alert.id))
            for cond in (
                Alert.status == status if status else None,
                Alert.service == service.lower() if service else None,
                Alert.severity == severity if severity else None,
            ):
                if cond is not None:
                    query, count = query.where(cond), count.where(cond)
            rows = session.scalars(query.order_by(Alert.received_at.desc()).limit(limit).offset(offset)).all()
            return list(rows), int(session.scalar(count) or 0)

    def acknowledge(self, alert_id: int, actor: str, role: str = "") -> Alert:
        return self._transition(alert_id, "acknowledged", actor, role)

    def resolve(self, alert_id: int, actor: str, role: str = "") -> Alert:
        return self._transition(alert_id, "resolved", actor, role)

    def _transition(self, alert_id: int, status: str, actor: str, role: str) -> Alert:
        with self._db.session() as session:
            alert = session.get(Alert, alert_id)
            if alert is None:
                raise AlertError("Alert not found", 404)
            alert.status = status
            if status == "acknowledged":
                alert.acknowledged_by = actor
            else:
                alert.ends_at = utcnow()
            session.flush()
            session.refresh(alert)
        self._audit.log(f"alert.{status}", actor, role, "alert", alert_id)
        self._events.publish("alert.updated", {"id": alert_id, "status": status})
        return alert


class IncidentTrigger:
    """Incident Trigger Service: turns detected anomalies into incidents and queues the agent workflow."""

    def __init__(self, settings: Settings, signals: SignalStore, platform: PlatformData, incidents: IncidentService,
                 kv: KeyValueStore, enqueue: Callable[[int, str], None]) -> None:
        self._s, self._signals, self._platform, self._incidents, self._kv, self._enqueue = settings, signals, platform, incidents, kv, enqueue

    def evaluate(self, service: str, reason: str = "signal") -> int | None:
        """Return the id of a newly created incident, or None."""
        service = service.lower()
        if self._kv.incr(f"trigger:debounce:{service}", ttl=10) > 1:
            return None
        window = self._s.detection_window_minutes
        since = utcnow() - timedelta(minutes=window)
        scope = resolve_scope(service, self._platform.services())
        raw: dict[str, list] = {"logs": [], "metrics": [], "traces": [], "alerts": self._platform.alerts(scope, since)}
        for svc in scope:
            for kind, key in (("log", "logs"), ("metric", "metrics"), ("trace", "traces")):
                raw[key].extend(self._signals.query(kind, svc, since=since))
        if not any(raw.values()):
            return None
        cutoff = self._incidents.resolution_cutoff(scope, window)
        result = detect(NormalizationAgent().run({"raw_signals": raw})["signals"], since=cutoff)
        if not result["is_incident"]:
            return None
        affected = sorted(result["service_scores"], key=result["service_scores"].get, reverse=True)
        existing = self._incidents.find_active(affected)
        if existing is not None:
            self._incidents.touch(existing.id, affected)
            return None
        primary = service if service in result["service_scores"] else affected[0]
        top = result["anomalies"][0]
        incident = self._incidents.create(
            title=f"{primary}: {top['description']}"[:240], service=primary, severity="P3",
            summary=f"Auto-detected from {reason}: {len(result['anomalies'])} anomalies, score {result['score']:.1f}",
            affected=affected, status="open",
        )
        self._enqueue(incident.id, reason)
        return incident.id

    def sweep(self) -> list[int]:
        created = []
        for service in self._signals.services():
            try:
                incident_id = self.evaluate(service, "sweep")
                if incident_id:
                    created.append(incident_id)
            except Exception:
                logger.exception("Trigger evaluation failed for %s", service)
        return created


class IngestionService:
    def __init__(self, signals: SignalStore, alerts: AlertService, trigger: IncidentTrigger) -> None:
        self._signals, self._alerts, self._trigger = signals, alerts, trigger

    @staticmethod
    def _dump(items: list[Any]) -> list[dict[str, Any]]:
        out = []
        for item in items:
            data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
            if data.get("timestamp") is not None and not isinstance(data["timestamp"], (str, int, float)):
                data["timestamp"] = parse_ts(data["timestamp"]).isoformat()
            out.append(data)
        return out

    def ingest_signals(self, kind: str, items: list[Any]) -> int:
        accepted = self._signals.add(kind, self._dump(items))
        metrics.SIGNALS_INGESTED.labels(kind=kind).inc(accepted)
        return accepted

    def ingest_alerts(self, alerts: list[AlertIn]) -> tuple[int, list[int]]:
        stored = self._alerts.ingest(alerts)
        triggered: list[int] = []
        for service in {a.service for a in stored if a.service and a.status == "firing"}:
            incident_id = self._trigger.evaluate(service, "alert")
            if incident_id:
                triggered.append(incident_id)
        return len(stored), triggered
