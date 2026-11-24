"""RCA reports: retrieval, responder feedback and accuracy tracking."""
from __future__ import annotations

from sqlalchemy import func, select

from backend import metrics
from backend.db import Database
from backend.models import RCAReport
from services.audit_service import AuditService


class RCAError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class RCAService:
    def __init__(self, db: Database, audit: AuditService) -> None:
        self._db, self._audit = db, audit

    def list(self, limit: int = 50, offset: int = 0, category: str | None = None) -> tuple[list[RCAReport], int]:
        with self._db.session() as session:
            query, count = select(RCAReport), select(func.count(RCAReport.id))
            if category:
                query, count = query.where(RCAReport.category == category), count.where(RCAReport.category == category)
            rows = session.scalars(query.order_by(RCAReport.id.desc()).limit(limit).offset(offset)).all()
            return list(rows), int(session.scalar(count) or 0)

    def get(self, report_id: int) -> RCAReport | None:
        with self._db.session() as session:
            return session.get(RCAReport, report_id)

    def for_incident(self, incident_id: int) -> RCAReport | None:
        with self._db.session() as session:
            return session.scalars(select(RCAReport).where(RCAReport.incident_id == incident_id).order_by(RCAReport.id.desc())).first()

    def feedback(self, report_id: int, result: str, notes: str, actor: str, role: str = "") -> RCAReport:
        with self._db.session() as session:
            report = session.get(RCAReport, report_id)
            if report is None:
                raise RCAError("RCA report not found", 404)
            report.feedback, report.feedback_notes, report.feedback_by = result, notes, actor
            session.flush()
            session.refresh(report)
        metrics.RCA_FEEDBACK.labels(result=result).inc()
        self.refresh_accuracy_gauge()
        self._audit.log("rca.feedback", actor, role, "rca", report_id, {"result": result})
        return report

    def accuracy(self) -> dict[str, float | int | None]:
        with self._db.session() as session:
            total = int(session.scalar(select(func.count(RCAReport.id)).where(RCAReport.feedback.is_not(None))) or 0)
            correct = int(session.scalar(select(func.count(RCAReport.id)).where(RCAReport.feedback == "correct")) or 0)
        return {"feedback_count": total, "correct": correct, "accuracy": round(correct / total, 4) if total else None}

    def refresh_accuracy_gauge(self) -> None:
        acc = self.accuracy()["accuracy"]
        if acc is not None:
            metrics.RCA_ACCURACY.set(acc)
