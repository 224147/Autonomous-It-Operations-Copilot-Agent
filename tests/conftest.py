"""Shared fixtures: an isolated container (SQLite, in-memory KV, local Chroma, fake infrastructure executors)."""
from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agents.evaluation.scenarios import DEMO_SERVICES
from backend.config import Settings
from backend.container import build_container
from backend.db import Database
from backend.kv import MemoryKV
from backend.main import create_app
from infrastructure.integrations import AwsVmClient, CacheClient, TicketClient
from infrastructure.kubernetes import KubernetesClient
from rag.embeddings import HashingEmbedder
from rag.loader import seed_knowledge_base
from rag.store import KnowledgeBase
from services.remediation_service import Executors

ADMIN_EMAIL = "admin@test.example"
ADMIN_PASSWORD = "AdminPass12345"


class FakeK8s(KubernetesClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def rollout_restart(self, namespace, name, kind="deployment"):  # noqa: ANN001
        self.calls.append(("rollout_restart", (namespace, name, kind)))
        return {"restarted": name}

    def restart_pod(self, namespace, deployment, kind="deployment"):  # noqa: ANN001
        self.calls.append(("restart_pod", (namespace, deployment, kind)))
        return {"deleted_pod": f"{deployment}-abc"}

    def scale(self, namespace, name, delta, kind="deployment"):  # noqa: ANN001
        self.calls.append(("scale", (namespace, name, delta, kind)))
        return {"from": 2, "to": 2 + delta}

    def rollback(self, namespace, name):  # noqa: ANN001
        self.calls.append(("rollback", (namespace, name)))
        return {"rolled_back_to_revision": 3}

    def trigger_backup(self, namespace, cronjob):  # noqa: ANN001
        self.calls.append(("trigger_backup", (namespace, cronjob)))
        return {"job": f"{cronjob}-manual"}


@pytest.fixture
def settings(tmp_path) -> Settings:
    # TEST_DATABASE_URL (e.g. a throw-away PostgreSQL) lets the whole suite run against the production database engine
    return Settings(
        app_env="test", database_url=os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path}/test.db",
        chroma_persist_dir=str(tmp_path / "chroma"),
        jwt_secret="test-secret-test-secret-test-secret-123456", bootstrap_admin_email=ADMIN_EMAIL,
        bootstrap_admin_password=ADMIN_PASSWORD, worker_enabled=False, llm_provider="none", remediation_mode="dry_run",
        ingest_api_keys="ingest-key-1", seed_demo_data=True, verify_wait_seconds=0, verify_poll_seconds=0,
        kb_seed_dir="./knowledge_base", cors_origins="http://localhost:5173",
    )


@pytest.fixture
def fake_k8s() -> FakeK8s:
    return FakeK8s()


@pytest.fixture
def container(settings, tmp_path, fake_k8s):
    kb = KnowledgeBase(HashingEmbedder(), persist_dir=str(tmp_path / "kb"))
    seed_knowledge_base(kb, settings.kb_seed_dir)
    executors = Executors(k8s=fake_k8s, aws=AwsVmClient(client=_FakeEc2()), cache=CacheClient("", client=None),
                          tickets=TicketClient(""))
    c = build_container(settings, db=Database(settings.database_url), kv=MemoryKV(), kb=kb, executors=executors, use_llm=False)
    if os.environ.get("TEST_DATABASE_URL"):
        from backend.db import Base

        Base.metadata.drop_all(c.db.engine)
    c.initialize()
    yield c
    c.close()


class _FakeEc2:
    def __init__(self) -> None:
        self.rebooted: list[str] = []

    def reboot_instances(self, InstanceIds):  # noqa: N803
        self.rebooted.extend(InstanceIds)


@pytest.fixture
def client(container):
    app = create_app(container.settings, container)
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, email: str = ADMIN_EMAIL, password: str = ADMIN_PASSWORD) -> dict[str, str]:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def admin_headers(client) -> dict[str, str]:
    return login(client)


def make_user(client: TestClient, admin_headers: dict[str, str], role: str, email: str | None = None) -> dict[str, str]:
    email = email or f"{role}@test.example"
    resp = client.post("/api/v1/users", headers=admin_headers,
                       json={"email": email, "password": "UserPass12345", "role": role, "full_name": role.title()})
    assert resp.status_code == 201, resp.text
    return login(client, email, "UserPass12345")


def drain(container) -> int:
    """Process every queued workflow job synchronously (stands in for the background worker)."""
    from services.remediation_service import JOB_QUEUE

    processed = 0
    while (job := container.kv.dequeue(JOB_QUEUE)) is not None:
        container.agents.process_job(job)
        processed += 1
    return processed


def demo_services() -> list[dict[str, Any]]:
    return DEMO_SERVICES
