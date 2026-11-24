"""Ports (interfaces) the agents depend on. Implemented by the backend services; faked in tests."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from agents.common.llm import OpenAILLM
from monitoring.signal_store import SignalStore
from rag.store import KnowledgeBase


class PlatformData(Protocol):
    def services(self) -> list[dict[str, Any]]: ...
    def deployments(self, services: list[str], since: datetime) -> list[dict[str, Any]]: ...
    def alerts(self, services: list[str], since: datetime) -> list[dict[str, Any]]: ...


class ApprovalPort(Protocol):
    def request(self, incident_id: int, plan: list[dict[str, Any]], risk: str, auto_approve: bool) -> dict[str, Any]: ...
    def get(self, approval_id: int) -> dict[str, Any] | None: ...


class RemediationPort(Protocol):
    def execute(self, incident_id: int | None, approval_id: int | None, action: dict[str, Any]) -> dict[str, Any]: ...


class SignalConnector(Protocol):
    name: str

    def fetch(self, service: dict[str, Any], since: datetime, until: datetime) -> dict[str, list[dict[str, Any]]]: ...


class ActionStatsPort(Protocol):
    def get(self, category: str, action: str) -> tuple[int, int]: ...
    def record(self, category: str, action: str, success: bool) -> None: ...


@dataclass
class AgentContext:
    signals: SignalStore
    platform: PlatformData
    approvals: ApprovalPort
    remediation: RemediationPort
    stats: ActionStatsPort
    kb: KnowledgeBase | None = None
    llm: OpenAILLM | None = None
    connectors: list[SignalConnector] = field(default_factory=list)
    detection_window_minutes: int = 15
    verify_wait_seconds: int = 60
    verify_poll_seconds: int = 10
    max_remediation_attempts: int = 2
    auto_approve_max_risk: str = "none"
    sleep: Callable[[float], None] = time.sleep
