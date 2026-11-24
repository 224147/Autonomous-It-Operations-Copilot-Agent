"""Reporting: incident reports, analytics and platform KPIs."""
from __future__ import annotations

from typing import Any

from backend.db import Database
from backend.models import Incident
from services.incident_service import IncidentService
from services.rca_service import RCAService

TARGETS = {
    "incident_detection_accuracy": 0.90,
    "rca_accuracy": 0.85,
    "automated_remediation_coverage": 0.70,
    "mttr_reduction": 0.50,
    "platform_availability": 0.999,
}


class ReportingService:
    def __init__(self, db: Database, incidents: IncidentService, rca: RCAService) -> None:
        self._db, self._incidents, self._rca = db, incidents, rca

    def incident_report(self, incident_id: int) -> dict[str, Any] | None:
        with self._db.session() as session:
            incident = session.get(Incident, incident_id)
            if incident is None:
                return None
            state = incident.workflow_state or {}
            report = dict(state.get("report") or {})
            if not report:  # incident analysed but not finished (e.g. awaiting approval)
                report = {"title": incident.title, "incident_id": incident.id, "severity": incident.severity,
                          "status": incident.status, "summary": incident.summary, "markdown": ""}
            report.update(incident_id=incident.id, status=incident.status, severity=incident.severity,
                          created_at=incident.created_at.isoformat(),
                          resolved_at=incident.resolved_at.isoformat() if incident.resolved_at else None,
                          resolution_notes=incident.resolution_notes)
            if incident.resolved_at:
                report["mttr_seconds"] = (incident.resolved_at - incident.created_at).total_seconds()
            if not report.get("markdown"):
                rca = self._rca.for_incident(incident_id)
                if rca:
                    report["markdown"] = rca.content_markdown
            return report

    def analytics(self, days: int = 30) -> dict[str, Any]:
        return self._incidents.stats(days)

    def kpis(self, days: int = 30) -> dict[str, Any]:
        stats = self._incidents.stats(days)
        rca_acc = self._rca.accuracy()
        return {
            "window_days": days,
            "mttr_seconds": stats["mttr_seconds"],
            "rca_accuracy": rca_acc["accuracy"],
            "rca_feedback_count": rca_acc["feedback_count"],
            "automated_remediation_coverage": stats["automated_remediation_coverage"],
            "remediation_success_rate": stats["remediation_success_rate"],
            "total_incidents": stats["total_incidents"],
            "targets": TARGETS,
            "notes": {
                "incident_detection_accuracy": "Measured offline: python -m agents.evaluation.harness (needs labelled data)",
                "mttr_reduction": "Requires a pre-adoption MTTR baseline, which is not available to the platform",
                "platform_availability": "Measured by Prometheus (see copilot_up / probe metrics), not by the app itself",
            },
        }
