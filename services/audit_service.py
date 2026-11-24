"""Append-only, hash-chained audit trail."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime
from typing import Any

from sqlalchemy import select

from backend.db import Database
from backend.logging_config import redact
from backend.models import AuditLog, utcnow

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {"password", "token", "secret", "api_key", "authorization", "id_token", "refresh_token"}


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***" if k.lower() in SENSITIVE_KEYS else _scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        return redact(value)
    return value


def _digest(prev: str, row: AuditLog) -> str:
    payload = json.dumps(
        [prev, row.timestamp.isoformat(), row.actor, row.actor_role, row.action, row.resource_type,
         row.resource_id, row.outcome, row.details, row.ip_address],
        sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class AuditService:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._lock = threading.Lock()

    def log(self, action: str, actor: str = "system", actor_role: str = "", resource_type: str = "",
            resource_id: str | int = "", details: dict[str, Any] | None = None, outcome: str = "success",
            ip: str = "") -> None:
        try:
            with self._lock, self._db.session() as session:
                last = session.scalar(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
                row = AuditLog(
                    timestamp=utcnow().replace(microsecond=0), actor=actor, actor_role=actor_role, action=action,
                    resource_type=resource_type, resource_id=str(resource_id), outcome=outcome,
                    details=_scrub(details or {}), ip_address=ip, prev_hash=last.hash if last else "",
                )
                row.hash = _digest(row.prev_hash, row)
                session.add(row)
        except Exception:  # auditing must not break the primary operation, but the failure is loud
            logger.exception("AUDIT WRITE FAILED action=%s actor=%s", action, actor)

    def list(self, limit: int = 100, offset: int = 0, actor: str | None = None, action: str | None = None,
             since: datetime | None = None) -> tuple[list[AuditLog], int]:
        from sqlalchemy import func

        with self._db.session() as session:
            query = select(AuditLog)
            count = select(func.count(AuditLog.id))
            for cond in (
                (AuditLog.actor == actor) if actor else None,
                AuditLog.action.like(f"{action}%") if action else None,
                (AuditLog.timestamp >= since) if since else None,
            ):
                if cond is not None:
                    query, count = query.where(cond), count.where(cond)
            rows = session.scalars(query.order_by(AuditLog.id.desc()).limit(limit).offset(offset)).all()
            return list(rows), int(session.scalar(count) or 0)

    def verify_chain(self) -> dict[str, Any]:
        with self._db.session() as session:
            prev = ""
            checked = 0
            for row in session.scalars(select(AuditLog).order_by(AuditLog.id)):
                if row.prev_hash != prev or row.hash != _digest(prev, row):
                    return {"valid": False, "checked": checked, "broken_at": row.id}
                prev = row.hash
                checked += 1
        return {"valid": True, "checked": checked, "broken_at": None}
