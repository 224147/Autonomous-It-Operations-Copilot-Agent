"""SQLAlchemy ORM models (PostgreSQL in production, SQLite for development/tests)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from backend.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Always stores UTC and returns timezone-aware UTC datetimes (SQLite drops tzinfo otherwise)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _dt() -> Any:
    return UTCDateTime()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    auth_provider: Mapped[str] = mapped_column(String(32), default="local")
    created_at: Mapped[datetime] = mapped_column(_dt(), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(_dt(), nullable=True)


class Service(Base):
    """Service inventory entry."""

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    tier: Mapped[int] = mapped_column(Integer, default=3)  # 1 = most critical
    team: Mapped[str] = mapped_column(String(128), default="")
    environment: Mapped[str] = mapped_column(String(32), default="production")
    kind: Mapped[str] = mapped_column(String(32), default="deployment")  # deployment|statefulset|database|vm
    namespace: Mapped[str] = mapped_column(String(128), default="default")
    deployment: Mapped[str] = mapped_column(String(128), default="")
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    cache_prefix: Mapped[str] = mapped_column(String(128), default="")
    vm_instance_id: Mapped[str] = mapped_column(String(128), default="")
    backup_cronjob: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(_dt(), default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_service_received", "service", "received_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(64), default="generic")
    name: Mapped[str] = mapped_column(String(255))
    service: Mapped[str] = mapped_column(String(128), index=True, default="")
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    status: Mapped[str] = mapped_column(String(16), default="firing")  # firing|resolved|acknowledged
    summary: Mapped[str] = mapped_column(Text, default="")
    labels: Mapped[dict] = mapped_column(JSON, default=dict)
    annotations: Mapped[dict] = mapped_column(JSON, default=dict)
    starts_at: Mapped[datetime] = mapped_column(_dt(), default=utcnow)
    ends_at: Mapped[datetime | None] = mapped_column(_dt(), nullable=True)
    received_at: Mapped[datetime] = mapped_column(_dt(), default=utcnow)
    acknowledged_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True)


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (Index("ix_incidents_status_service", "status", "service"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    service: Mapped[str] = mapped_column(String(128), index=True)
    affected_services: Mapped[list] = mapped_column(JSON, default=list)
    category: Mapped[str] = mapped_column(String(64), default="unknown")
    severity: Mapped[str] = mapped_column(String(4), default="P3")
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    dedup_key: Mapped[str] = mapped_column(String(255), default="", index=True)
    detection: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation: Mapped[dict] = mapped_column(JSON, default=dict)
    recommendations: Mapped[list] = mapped_column(JSON, default=list)
    workflow_state: Mapped[dict] = mapped_column(JSON, default=dict)
    workflow_status: Mapped[str] = mapped_column(String(32), default="pending")
    resolution_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(_dt(), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(_dt(), default=utcnow, onupdate=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(_dt(), nullable=True)

    rca_reports: Mapped[list["RCAReport"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", order_by="RCAReport.id.desc()"
    )


class RCAReport(Base):
    __tablename__ = "rca_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    root_cause: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(64), default="unknown")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    explanation: Mapped[str] = mapped_column(Text, default="")
    contributing_factors: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    timeline: Mapped[list] = mapped_column(JSON, default=list)
    recommendations: Mapped[list] = mapped_column(JSON, default=list)
    content_markdown: Mapped[str] = mapped_column(Text, default="")
    feedback: Mapped[str | None] = mapped_column(String(16), nullable=True)  # correct|incorrect
    feedback_notes: Mapped[str] = mapped_column(Text, default="")
    feedback_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_dt(), default=utcnow)

    incident: Mapped[Incident] = relationship(back_populates="rca_reports")


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=True, index=True)
    plan: Mapped[list] = mapped_column(JSON, default=list)
    approved_actions: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|approved|rejected|expired
    risk: Mapped[str] = mapped_column(String(16), default="medium")
    requested_at: Mapped[datetime] = mapped_column(_dt(), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(_dt(), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(_dt(), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    auto_approved: Mapped[bool] = mapped_column(Boolean, default=False)


class RemediationExecution(Base):
    __tablename__ = "remediation_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True)
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("approval_requests.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(255), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16))  # succeeded|failed|simulated|rejected
    mode: Mapped[str] = mapped_column(String(16), default="dry_run")
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    executed_by: Mapped[str] = mapped_column(String(255), default="system")
    started_at: Mapped[datetime] = mapped_column(_dt(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(_dt(), nullable=True)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(8), default="P3")
    status: Mapped[str] = mapped_column(String(16), default="open")
    external_ref: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(_dt(), default=utcnow)


class Deployment(Base):
    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(128), default="")
    image: Mapped[str] = mapped_column(String(255), default="")
    environment: Mapped[str] = mapped_column(String(32), default="production")
    status: Mapped[str] = mapped_column(String(16), default="succeeded")  # succeeded|failed|rolled_back|in_progress
    commit_sha: Mapped[str] = mapped_column(String(64), default="")
    deployed_by: Mapped[str] = mapped_column(String(255), default="")
    source: Mapped[str] = mapped_column(String(32), default="api")
    notes: Mapped[str] = mapped_column(Text, default="")
    deployed_at: Mapped[datetime] = mapped_column(_dt(), default=utcnow, index=True)


class AuditLog(Base):
    """Append-only, hash-chained audit trail."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(_dt(), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(255), default="system")
    actor_role: Mapped[str] = mapped_column(String(32), default="")
    action: Mapped[str] = mapped_column(String(128), index=True)
    resource_type: Mapped[str] = mapped_column(String(64), default="")
    resource_id: Mapped[str] = mapped_column(String(64), default="")
    outcome: Mapped[str] = mapped_column(String(16), default="success")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64), default="")
