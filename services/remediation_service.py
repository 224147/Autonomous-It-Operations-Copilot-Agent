"""Remediation engine, approval workflow and ticketing.

Safety model (zero trust): every non-ticket action must be backed by an *approved* ApprovalRequest that contains the
exact action, targets must exist in the service inventory and in an allowed namespace, actions are rate limited by a
cooldown, and the default mode is dry-run (REMEDIATION_MODE=live is required to touch real infrastructure).
Only a fixed catalogue of actions exists - arbitrary shell/command execution is deliberately not supported.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from agents.common.taxonomy import ACTIONS
from agents.common.timeutil import utcnow
from backend import metrics
from backend.config import Settings
from backend.db import Database
from backend.kv import KeyValueStore
from backend.models import ApprovalRequest, Incident, RemediationExecution, Service, Ticket
from backend.realtime import EventBus
from infrastructure.integrations import AwsVmClient, CacheClient, IntegrationError, TicketClient
from infrastructure.kubernetes import KubernetesClient, KubernetesError
from rag.store import KnowledgeBase
from services.audit_service import AuditService
from services.inventory_service import InventoryService

logger = logging.getLogger(__name__)

JOB_QUEUE = "workflow:jobs"
K8S_ACTIONS = {"restart_service", "restart_pod", "rollback_deployment", "scale_deployment", "restart_database", "trigger_backup"}


@dataclass
class Executors:
    k8s: KubernetesClient
    aws: AwsVmClient
    cache: CacheClient
    tickets: TicketClient


def approval_dict(a: ApprovalRequest) -> dict[str, Any]:
    return {
        "id": a.id, "incident_id": a.incident_id, "plan": a.plan, "approved_actions": a.approved_actions,
        "status": a.status, "risk": a.risk, "requested_at": a.requested_at.isoformat(),
        "expires_at": a.expires_at.isoformat() if a.expires_at else None, "decided_by": a.decided_by,
        "decided_at": a.decided_at.isoformat() if a.decided_at else None, "comment": a.comment,
        "auto_approved": a.auto_approved,
    }


class RemediationError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class RemediationService:
    def __init__(self, settings: Settings, db: Database, kv: KeyValueStore, kb: KnowledgeBase | None,
                 inventory: InventoryService, audit: AuditService, events: EventBus, executors: Executors) -> None:
        self._s, self._db, self._kv, self._kb = settings, db, kv, kb
        self._inv, self._audit, self._events, self._x = inventory, audit, events, executors

    # ================================================================== approvals (ApprovalPort)
    def request(self, incident_id: int, plan: list[dict[str, Any]], risk: str, auto_approve: bool) -> dict[str, Any]:
        now = utcnow()
        with self._db.session() as session:
            record = ApprovalRequest(
                incident_id=incident_id or None, plan=plan, risk=risk, auto_approved=auto_approve,
                status="approved" if auto_approve else "pending", requested_at=now,
                expires_at=now + timedelta(minutes=self._s.approval_ttl_minutes),
                decided_by="policy:auto-approve-low-risk" if auto_approve else None,
                decided_at=now if auto_approve else None,
            )
            session.add(record)
            session.flush()
            data = approval_dict(record)
        self._audit.log("approval.requested" if not auto_approve else "approval.auto_approved", "agent:approval",
                        resource_type="approval", resource_id=data["id"],
                        details={"incident_id": incident_id, "risk": risk, "actions": [a["action"] for a in plan]})
        if not auto_approve:
            self._events.publish("approval.requested", data)
        return data

    def get(self, approval_id: int) -> dict[str, Any] | None:
        with self._db.session() as session:
            record = session.get(ApprovalRequest, approval_id)
            if record is None:
                return None
            self._expire_if_needed(session, record)
            return approval_dict(record)

    def list_approvals(self, status: str | None = None, limit: int = 100) -> list[ApprovalRequest]:
        with self._db.session() as session:
            query = select(ApprovalRequest).order_by(ApprovalRequest.id.desc()).limit(limit)
            if status:
                query = query.where(ApprovalRequest.status == status)
            rows = list(session.scalars(query))
            for r in rows:
                self._expire_if_needed(session, r)
            return rows

    def _expire_if_needed(self, session, record: ApprovalRequest) -> None:  # noqa: ANN001
        if record.status == "pending" and record.expires_at and record.expires_at < utcnow():
            record.status = "expired"
            record.decided_at = utcnow()
            record.decided_by = "system:expired"
            self._kv.enqueue(JOB_QUEUE, {"type": "resume", "incident_id": record.incident_id})

    def expire_pending(self) -> int:
        count = 0
        with self._db.session() as session:
            for record in session.scalars(select(ApprovalRequest).where(ApprovalRequest.status == "pending")):
                before = record.status
                self._expire_if_needed(session, record)
                count += before != record.status
        return count

    def decide(self, approval_id: int, approve: bool, actor: str, actor_role: str, comment: str = "",
               approved_actions: list[str] | None = None, ip: str = "") -> dict[str, Any]:
        with self._db.session() as session:
            record = session.get(ApprovalRequest, approval_id)
            if record is None:
                raise RemediationError("Approval request not found", 404)
            self._expire_if_needed(session, record)
            if record.status != "pending":
                raise RemediationError(f"Approval request is already {record.status}", 409)
            plan_ids = {a["id"] for a in record.plan}
            chosen = approved_actions or []
            if not set(chosen) <= plan_ids:
                raise RemediationError("approved_actions contains ids that are not part of the plan", 422)
            record.status = "approved" if approve else "rejected"
            record.approved_actions = list(chosen) if approve else []
            record.decided_by, record.decided_at, record.comment = actor, utcnow(), comment
            incident_id = record.incident_id
            data = approval_dict(record)
        self._audit.log(f"approval.{data['status']}", actor, actor_role, "approval", approval_id,
                        {"incident_id": incident_id, "comment": comment, "approved_actions": chosen}, ip=ip)
        self._events.publish("approval.decided", data)
        if incident_id:
            self._kv.enqueue(JOB_QUEUE, {"type": "resume", "incident_id": incident_id})
        return data

    # ================================================================== execution (RemediationPort)
    def execute(self, incident_id: int | None, approval_id: int | None, action: dict[str, Any]) -> dict[str, Any]:
        name = str(action.get("action", ""))
        target = str(action.get("target", "")).lower()
        params: dict[str, Any] = {}
        executed_by = "system"
        started = utcnow()
        status, output, error = "failed", {}, ""
        mode = self._s.remediation_mode
        try:
            if name not in ACTIONS:
                raise RemediationError(f"Unknown action '{name}'", 422)
            params = self._clean_params(name, action.get("params") or {})
            if name == "open_ticket":
                ticket = self._ticket_for_action(incident_id, target, params)
                output, status, mode = {"ticket_id": ticket.id, "external_ref": ticket.external_ref}, "succeeded", "live"
            else:
                self._authorize(approval_id, action, name, target)
                service = self._inv.get_service(target)
                if service is None:
                    raise RemediationError(f"Target '{target}' is not in the service inventory", 422)
                self._guard(service, name)
                output = self._run(name, service, params, incident_id, dry_run=mode == "dry_run", depth=0)
                status = "simulated" if mode == "dry_run" else "succeeded"
        except RemediationError as exc:
            status, error = "rejected", str(exc)
        except (KubernetesError, IntegrationError) as exc:
            status, error = "failed", str(exc)
        except Exception as exc:
            logger.exception("Remediation %s on %s failed", name, target)
            status, error = "failed", f"{type(exc).__name__}: {exc}"

        finished = utcnow()
        with self._db.session() as session:
            session.add(RemediationExecution(
                incident_id=incident_id, approval_id=approval_id, action=name, target=target, params=params,
                status=status, mode=mode, output=output, error=error, executed_by=executed_by,
                started_at=started, finished_at=finished,
            ))
        metrics.REMEDIATIONS.labels(action=name, status=status).inc()
        self._audit.log(f"remediation.{name}", executed_by, "agent", "service", target,
                        {"incident_id": incident_id, "approval_id": approval_id, "params": params, "mode": mode,
                         "error": error}, outcome="success" if status in ("succeeded", "simulated") else "failure")
        self._events.publish("remediation.executed", {"incident_id": incident_id, "action": name, "target": target,
                                                      "status": status, "mode": mode})
        return {"status": status, "mode": mode, "output": output, "error": error}

    def execute_manual(self, actor: str, actor_role: str, incident_id: int | None, action: str, target: str,
                       params: dict[str, Any], justification: str, ip: str = "") -> dict[str, Any]:
        """Operator-initiated action: recorded as an approval decided by the (approver-level) actor."""
        if action not in ACTIONS:
            raise RemediationError(f"Unknown action '{action}'", 422)
        step = {"id": "m1", "action": action, "target": target.lower(), "params": params, "risk": ACTIONS[action]["risk"]}
        now = utcnow()
        with self._db.session() as session:
            record = ApprovalRequest(incident_id=incident_id, plan=[step], risk=step["risk"], status="approved",
                                     requested_at=now, decided_by=actor, decided_at=now, comment=justification)
            session.add(record)
            session.flush()
            approval_id = record.id
        self._audit.log("remediation.manual_requested", actor, actor_role, "approval", approval_id,
                        {"action": action, "target": target, "justification": justification}, ip=ip)
        result = self.execute(incident_id, approval_id, step)
        result["approval_id"] = approval_id
        return result

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _clean_params(name: str, params: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        if name == "scale_deployment":
            try:
                clean["replicas_delta"] = max(-5, min(5, int(params.get("replicas_delta", 1)))) or 1
            except (TypeError, ValueError):
                clean["replicas_delta"] = 1
        elif name == "execute_runbook":
            rb = str(params.get("runbook_id", ""))
            if not rb or len(rb) > 128:
                raise RemediationError("execute_runbook requires params.runbook_id", 422)
            clean["runbook_id"] = rb
        elif name == "open_ticket":
            clean["priority"] = str(params.get("priority", "P3"))[:4]
        return clean

    def _authorize(self, approval_id: int | None, action: dict[str, Any], name: str, target: str) -> None:
        if not approval_id:
            raise RemediationError("Action is not backed by an approval", 403)
        with self._db.session() as session:
            record = session.get(ApprovalRequest, approval_id)
            if record is None or record.status != "approved":
                raise RemediationError("Approval is missing or not in 'approved' state", 403)
            plan_item = next((a for a in record.plan if a.get("id") == action.get("id")), None)
            if plan_item is None or plan_item["action"] != name or str(plan_item.get("target", "")).lower() != target:
                raise RemediationError("Action does not match the approved plan", 403)
            if record.approved_actions and action.get("id") not in record.approved_actions:
                raise RemediationError("Action was not selected by the approver", 403)

    def _guard(self, service: Service, name: str) -> None:
        allowed = self._s.allowed_namespaces
        if name in K8S_ACTIONS and allowed and service.namespace not in allowed:
            raise RemediationError(f"Namespace '{service.namespace}' is not in REMEDIATION_ALLOWED_NAMESPACES", 403)
        if self._s.remediation_mode == "live" and self._s.remediation_cooldown_seconds > 0:  # dry-runs are free
            key = f"remediation:cooldown:{name}:{service.name}"
            if self._kv.incr(key, ttl=self._s.remediation_cooldown_seconds) > 1:
                raise RemediationError(f"{name} on {service.name} is in cooldown ({self._s.remediation_cooldown_seconds}s)", 429)

    def _run(self, name: str, svc: Service, params: dict[str, Any], incident_id: int | None, dry_run: bool,
             depth: int) -> dict[str, Any]:
        workload_kind = "statefulset" if svc.kind == "statefulset" else "deployment"
        if dry_run:
            return {"dry_run": True, "would": ACTIONS[name]["description"], "target": svc.name,
                    "namespace": svc.namespace, "params": params,
                    **({"steps": self._runbook_steps(params["runbook_id"])} if name == "execute_runbook" else {})}
        if name in K8S_ACTIONS and not svc.deployment and name != "trigger_backup":
            raise RemediationError(f"Service {svc.name} has no Kubernetes workload configured", 422)
        if name == "restart_service":
            return self._x.k8s.rollout_restart(svc.namespace, svc.deployment, workload_kind)
        if name == "restart_pod":
            return self._x.k8s.restart_pod(svc.namespace, svc.deployment, workload_kind)
        if name == "rollback_deployment":
            if workload_kind != "deployment":
                raise RemediationError("Rollback is only supported for Deployments", 422)
            return self._x.k8s.rollback(svc.namespace, svc.deployment)
        if name == "scale_deployment":
            return self._x.k8s.scale(svc.namespace, svc.deployment, params.get("replicas_delta", 1), workload_kind)
        if name == "restart_database":
            if svc.kind != "statefulset":
                raise RemediationError("restart_database requires a statefulset-backed service", 422)
            return self._x.k8s.rollout_restart(svc.namespace, svc.deployment, "statefulset")
        if name == "trigger_backup":
            if not svc.backup_cronjob:
                raise RemediationError(f"Service {svc.name} has no backup_cronjob configured", 422)
            return self._x.k8s.trigger_backup(svc.namespace, svc.backup_cronjob)
        if name == "restart_vm":
            return self._x.aws.reboot(svc.vm_instance_id)
        if name == "clear_cache":
            return self._x.cache.clear_prefix(svc.cache_prefix)
        if name == "execute_runbook":
            return self._run_runbook(svc, params["runbook_id"], incident_id)
        raise RemediationError(f"Action {name} is not implemented", 422)

    def _runbook_steps(self, runbook_id: str) -> list[dict[str, Any]]:
        if self._kb is None:
            raise RemediationError("Knowledge base is not available", 503)
        doc = self._kb.get(runbook_id)
        if doc is None or doc["metadata"].get("doc_type") != "runbook":
            raise RemediationError(f"Runbook '{runbook_id}' not found", 404)
        try:
            steps = json.loads(doc["metadata"].get("steps") or "[]")
        except ValueError as exc:
            raise RemediationError("Runbook steps are malformed", 422) from exc
        for step in steps:
            if step.get("action") not in ACTIONS or step.get("action") == "execute_runbook":
                raise RemediationError(f"Runbook contains unsupported action {step.get('action')!r}", 422)
        return steps

    def _run_runbook(self, svc: Service, runbook_id: str, incident_id: int | None) -> dict[str, Any]:
        results = []
        for step in self._runbook_steps(runbook_id):
            name = step["action"]
            params = self._clean_params(name, step.get("params") or {})
            if name == "open_ticket":
                ticket = self._ticket_for_action(incident_id, svc.name, params)
                results.append({"action": name, "status": "succeeded", "ticket_id": ticket.id})
                continue
            self._guard_step(svc, name)
            out = self._run(name, svc, params, incident_id, dry_run=False, depth=1)
            results.append({"action": name, "status": "succeeded", "output": out})
        return {"runbook_id": runbook_id, "steps": results}

    def _guard_step(self, svc: Service, name: str) -> None:
        allowed = self._s.allowed_namespaces
        if name in K8S_ACTIONS and allowed and svc.namespace not in allowed:
            raise RemediationError(f"Namespace '{svc.namespace}' is not allowed", 403)

    # ================================================================== tickets
    def create_ticket(self, incident_id: int | None, title: str, description: str, priority: str,
                      actor: str = "system") -> Ticket:
        with self._db.session() as session:
            ticket = Ticket(incident_id=incident_id, title=title[:255], description=description, priority=priority[:8])
            session.add(ticket)
            session.flush()
            ticket_id = ticket.id
        ref = ""
        if self._x.tickets.enabled:
            try:
                ref = self._x.tickets.create({"title": title, "description": description, "priority": priority,
                                              "incident_id": incident_id, "source": "autonomous-it-ops-copilot"})
            except Exception:
                logger.exception("Ticket webhook failed; ticket kept locally")
        with self._db.session() as session:
            ticket = session.get(Ticket, ticket_id)
            ticket.external_ref = ref
            session.flush()
            session.expunge(ticket)
        self._audit.log("ticket.created", actor, resource_type="ticket", resource_id=ticket_id,
                        details={"incident_id": incident_id, "external_ref": ref})
        self._events.publish("ticket.created", {"id": ticket_id, "incident_id": incident_id, "title": title})
        return ticket

    def _ticket_for_action(self, incident_id: int | None, target: str, params: dict[str, Any]) -> Ticket:
        title, description, priority = f"Incident on {target}", "Opened automatically by the Autonomous IT Operations Copilot.", params.get("priority", "P3")
        if incident_id:
            with self._db.session() as session:
                incident = session.get(Incident, incident_id)
                if incident is not None:
                    state = incident.workflow_state or {}
                    rca = state.get("rca") or {}
                    title = f"[{incident.severity}] INC-{incident.id}: {incident.title}"
                    description = (f"{incident.summary}\n\nRoot cause: {rca.get('root_cause', 'under investigation')}\n"
                                   f"Confidence: {rca.get('confidence', 0):.0%}\nAffected: {', '.join(incident.affected_services)}")
                    priority = incident.severity
        return self.create_ticket(incident_id, title, description, priority)

    def list_executions(self, incident_id: int | None = None, limit: int = 100) -> list[RemediationExecution]:
        with self._db.session() as session:
            query = select(RemediationExecution).order_by(RemediationExecution.id.desc()).limit(limit)
            if incident_id:
                query = query.where(RemediationExecution.incident_id == incident_id)
            return list(session.scalars(query))

    def list_tickets(self, incident_id: int | None = None, limit: int = 100) -> list[Ticket]:
        with self._db.session() as session:
            query = select(Ticket).order_by(Ticket.id.desc()).limit(limit)
            if incident_id:
                query = query.where(Ticket.incident_id == incident_id)
            return list(session.scalars(query))
