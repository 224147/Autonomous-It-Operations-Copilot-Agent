"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Role = Literal["viewer", "operator", "approver", "admin"]
Severity = Literal["P1", "P2", "P3", "P4"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth / users ---------------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class EntraLoginRequest(BaseModel):
    id_token: str = Field(min_length=20)


class UserOut(ORM):
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool
    auth_provider: str
    created_at: datetime
    last_login_at: datetime | None = None


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(default="", max_length=255)
    password: str = Field(min_length=10, max_length=72)
    role: Role = "viewer"


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=72)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=72)


# --- inventory --------------------------------------------------------------------------------------
class ServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$")
    tier: int = Field(default=3, ge=1, le=4)
    team: str = ""
    environment: str = "production"
    kind: Literal["deployment", "statefulset", "database", "vm"] = "deployment"
    namespace: str = Field(default="default", max_length=128, pattern=r"^[a-z0-9\-]*$")
    deployment: str = Field(default="", max_length=128, pattern=r"^[a-z0-9.\-]*$")
    depends_on: list[str] = Field(default_factory=list)
    cache_prefix: str = ""
    vm_instance_id: str = ""
    backup_cronjob: str = Field(default="", pattern=r"^[a-z0-9.\-]*$")
    description: str = ""

    @field_validator("name")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()

    @field_validator("depends_on")
    @classmethod
    def _deps(cls, v: list[str]) -> list[str]:
        return [d.lower() for d in v]


class ServiceOut(ServiceIn, ORM):
    id: int
    created_at: datetime


class DeploymentIn(BaseModel):
    service: str = Field(min_length=1, max_length=128)
    version: str = ""
    image: str = ""
    environment: str = "production"
    status: Literal["succeeded", "failed", "rolled_back", "in_progress"] = "succeeded"
    commit_sha: str = ""
    deployed_by: str = ""
    source: str = "api"
    notes: str = ""
    deployed_at: datetime | None = None


class DeploymentOut(DeploymentIn, ORM):
    id: int
    deployed_at: datetime


# --- alerts / ingestion -----------------------------------------------------------------------------
class AlertIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    service: str = Field(default="", max_length=128)
    severity: str = "warning"
    status: Literal["firing", "resolved"] = "firing"
    summary: str = ""
    source: str = "generic"
    labels: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    fingerprint: str | None = None


class AlertOut(ORM):
    id: int
    fingerprint: str
    source: str
    name: str
    service: str
    severity: str
    status: str
    summary: str
    labels: dict[str, Any]
    starts_at: datetime
    ends_at: datetime | None
    received_at: datetime
    acknowledged_by: str | None
    incident_id: int | None


class LogEntry(BaseModel):
    service: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=8000)
    severity: str | None = None
    timestamp: datetime | str | float | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class MetricSample(BaseModel):
    service: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    value: float
    timestamp: datetime | str | float | None = None
    labels: dict[str, Any] = Field(default_factory=dict)


class SpanIn(BaseModel):
    service: str = Field(min_length=1, max_length=128)
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""
    name: str = ""
    duration_ms: float = 0.0
    status: str = "ok"
    peer_service: str = ""
    timestamp: datetime | str | float | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class LogBatch(BaseModel):
    logs: list[LogEntry] = Field(max_length=5000)


class MetricBatch(BaseModel):
    metrics: list[MetricSample] = Field(max_length=10000)


class SpanBatch(BaseModel):
    spans: list[SpanIn] = Field(max_length=5000)


class IngestResult(BaseModel):
    accepted: int
    rejected: int = 0
    incidents_triggered: list[int] = Field(default_factory=list)


# --- incidents / RCA ---------------------------------------------------------------------------------
class IncidentOut(ORM):
    id: int
    title: str
    service: str
    affected_services: list[str]
    category: str
    severity: str
    status: str
    summary: str
    workflow_status: str
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None


class RCAReportOut(ORM):
    id: int
    incident_id: int
    root_cause: str
    category: str
    confidence: float
    explanation: str
    contributing_factors: list[Any]
    evidence: list[Any]
    timeline: list[Any]
    recommendations: list[Any]
    feedback: str | None
    feedback_notes: str
    created_at: datetime


class ApprovalOut(ORM):
    id: int
    incident_id: int | None
    plan: list[Any]
    approved_actions: list[Any]
    status: str
    risk: str
    requested_at: datetime
    expires_at: datetime | None
    decided_by: str | None
    decided_at: datetime | None
    comment: str
    auto_approved: bool


class ExecutionOut(ORM):
    id: int
    incident_id: int | None
    approval_id: int | None
    action: str
    target: str
    params: dict[str, Any]
    status: str
    mode: str
    output: dict[str, Any]
    error: str
    executed_by: str
    started_at: datetime
    finished_at: datetime | None


class TicketOut(ORM):
    id: int
    incident_id: int | None
    title: str
    description: str
    priority: str
    status: str
    external_ref: str
    created_at: datetime


class IncidentDetail(IncidentOut):
    detection: dict[str, Any]
    correlation: dict[str, Any]
    recommendations: list[Any]
    resolution_notes: str
    rca: RCAReportOut | None = None
    approvals: list[ApprovalOut] = Field(default_factory=list)
    executions: list[ExecutionOut] = Field(default_factory=list)
    tickets: list[TicketOut] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    verification: dict[str, Any] = Field(default_factory=dict)


class IncidentCreate(BaseModel):
    service: str = Field(min_length=1, max_length=128)
    title: str = Field(default="", max_length=255)
    severity: Severity = "P3"
    description: str = Field(default="", max_length=4000)


class IncidentResolve(BaseModel):
    notes: str = Field(default="", max_length=4000)


class IncidentStatusUpdate(BaseModel):
    status: Literal["open", "investigating", "monitoring", "escalated", "false_positive", "closed"]


class RCAFeedback(BaseModel):
    result: Literal["correct", "incorrect"]
    notes: str = Field(default="", max_length=2000)


class WorkflowRunRequest(BaseModel):
    service: str = Field(min_length=1, max_length=128)
    window_minutes: int = Field(default=15, ge=1, le=240)
    reason: str = Field(default="manual", max_length=255)


# --- remediation ------------------------------------------------------------------------------------
class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=2000)
    approved_actions: list[str] = Field(default_factory=list, description="Action ids to approve; empty approves the whole plan")


class ManualRemediation(BaseModel):
    incident_id: int | None = None
    action: str
    target: str = Field(min_length=1, max_length=128)
    params: dict[str, Any] = Field(default_factory=dict)
    justification: str = Field(min_length=5, max_length=2000)


# --- knowledge --------------------------------------------------------------------------------------
class KnowledgeSearch(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=5, ge=1, le=25)
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid"
    doc_types: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)


class KnowledgeDocumentIn(BaseModel):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$")
    doc_type: Literal["incident", "runbook", "sop", "postmortem", "infrastructure", "deployment", "dependency", "ticket"]
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=200000)
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- chat -------------------------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9\-_]+$")


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    llm_used: bool = False


class AuditOut(ORM):
    id: int
    timestamp: datetime
    actor: str
    actor_role: str
    action: str
    resource_type: str
    resource_id: str
    outcome: str
    details: dict[str, Any]
    ip_address: str
    hash: str


class Page(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int
