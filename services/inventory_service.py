"""Service inventory and deployment registry. Also implements the PlatformData port used by the agents."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select

from agents.common.timeutil import parse_ts
from backend.db import Database
from backend.models import Alert, Deployment, Service, utcnow
from backend.schemas import DeploymentIn, ServiceIn

logger = logging.getLogger(__name__)

SERVICE_FIELDS = ("name", "tier", "team", "environment", "kind", "namespace", "deployment", "depends_on",
                  "cache_prefix", "vm_instance_id", "backup_cronjob", "description")


def service_dict(s: Service) -> dict[str, Any]:
    return {f: getattr(s, f) for f in SERVICE_FIELDS}


class InventoryService:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ---------------------------------------------------------------- inventory CRUD
    def list_services(self) -> list[Service]:
        with self._db.session() as session:
            return list(session.scalars(select(Service).order_by(Service.name)))

    def get_service(self, name: str) -> Service | None:
        with self._db.session() as session:
            return session.scalar(select(Service).where(Service.name == name.lower()))

    def upsert_service(self, data: ServiceIn) -> Service:
        with self._db.session() as session:
            svc = session.scalar(select(Service).where(Service.name == data.name))
            if svc is None:
                svc = Service(name=data.name)
                session.add(svc)
            for field in SERVICE_FIELDS:
                if field != "name":
                    setattr(svc, field, getattr(data, field))
            session.flush()
            session.refresh(svc)
            return svc

    def delete_service(self, name: str) -> bool:
        with self._db.session() as session:
            svc = session.scalar(select(Service).where(Service.name == name.lower()))
            if svc is None:
                return False
            session.delete(svc)
            return True

    def seed(self, services: list[dict[str, Any]]) -> int:
        count = 0
        for item in services:
            if self.get_service(item["name"]) is None:
                self.upsert_service(ServiceIn(**item))
                count += 1
        return count

    # ---------------------------------------------------------------- deployments
    def record_deployment(self, data: DeploymentIn) -> Deployment:
        with self._db.session() as session:
            row = Deployment(**data.model_dump(exclude={"deployed_at"}), deployed_at=data.deployed_at or utcnow())
            row.service = row.service.lower()
            session.add(row)
            session.flush()
            session.refresh(row)
            return row

    def list_deployments(self, service: str | None = None, limit: int = 100) -> list[Deployment]:
        with self._db.session() as session:
            query = select(Deployment).order_by(Deployment.deployed_at.desc()).limit(limit)
            if service:
                query = query.where(Deployment.service == service.lower())
            return list(session.scalars(query))

    # ---------------------------------------------------------------- PlatformData port
    def services(self) -> list[dict[str, Any]]:
        return [service_dict(s) for s in self.list_services()]

    def deployments(self, services: list[str], since: datetime) -> list[dict[str, Any]]:
        names = [s.lower() for s in services]
        with self._db.session() as session:
            rows = session.scalars(
                select(Deployment).where(Deployment.service.in_(names), Deployment.deployed_at >= parse_ts(since))
            )
            return [
                {"service": d.service, "version": d.version, "status": d.status, "deployed_at": d.deployed_at.isoformat(),
                 "commit_sha": d.commit_sha, "deployed_by": d.deployed_by}
                for d in rows
            ]

    def alerts(self, services: list[str], since: datetime) -> list[dict[str, Any]]:
        names = [s.lower() for s in services]
        with self._db.session() as session:
            rows = session.scalars(
                select(Alert).where(Alert.service.in_(names), Alert.received_at >= parse_ts(since), Alert.status != "resolved")
            )
            return [
                {"id": a.id, "service": a.service, "name": a.name, "severity": a.severity, "status": a.status,
                 "summary": a.summary, "source": a.source, "starts_at": a.starts_at.isoformat()}
                for a in rows
            ]
