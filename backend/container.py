"""Dependency container: wires settings, datastores, integrations and services together."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from agents.common.llm import OpenAILLM, build_llm
from agents.common.ports import AgentContext, SignalConnector
from agents.evaluation.scenarios import DEMO_SERVICES
from agents.learning_agent.stats import ActionStats
from backend.config import Settings
from backend.db import Database
from backend.kv import KeyValueStore, create_kv
from backend.realtime import EventBus
from infrastructure.integrations import AwsVmClient, CacheClient, TicketClient
from infrastructure.kubernetes import KubernetesClient
from monitoring.alert_ingestion.service import AlertService, IncidentTrigger, IngestionService
from monitoring.log_ingestion.loki import LokiConnector
from monitoring.metric_ingestion.prometheus import PrometheusConnector, load_queries
from monitoring.scheduler import ConnectorPoller, Scheduler
from monitoring.signal_store import SignalStore
from rag.embeddings import build_embedder
from rag.loader import seed_knowledge_base
from rag.store import KnowledgeBase
from services.agent_service import AgentService
from services.audit_service import AuditService
from services.auth_service import AuthService
from services.chat_service import ChatService
from services.incident_service import IncidentService
from services.inventory_service import InventoryService
from services.knowledge_service import KnowledgeService
from services.rca_service import RCAService
from services.remediation_service import Executors, RemediationService
from services.reporting_service import ReportingService
from services.user_service import UserService

logger = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    db: Database
    kv: KeyValueStore
    events: EventBus
    audit: AuditService
    llm: OpenAILLM | None
    kb: KnowledgeBase | None
    signals: SignalStore
    inventory: InventoryService
    auth: AuthService
    users: UserService
    incidents: IncidentService
    rca: RCAService
    remediation: RemediationService
    agents: AgentService
    knowledge: KnowledgeService
    reporting: ReportingService
    chat: ChatService
    alerts: AlertService
    trigger: IncidentTrigger
    ingestion: IngestionService
    scheduler: Scheduler
    connectors: list[SignalConnector] = field(default_factory=list)

    def initialize(self) -> None:
        """One-time startup work: schema, bootstrap admin, knowledge seeding, optional demo data."""
        if self.settings.db_auto_create:
            self.db.create_all()
        self.auth.bootstrap_admin()
        if self.kb is not None and self.kb.count() == 0:
            seed_knowledge_base(self.kb, self.settings.kb_seed_dir)
        if self.settings.seed_demo_data:
            added = self.inventory.seed(DEMO_SERVICES)
            logger.info("Seeded %d demo services", added)
        self.rca.refresh_accuracy_gauge()

    def close(self) -> None:
        self.db.dispose()


def build_container(settings: Settings, db: Database | None = None, kv: KeyValueStore | None = None,
                    kb: KnowledgeBase | None = None, executors: Executors | None = None,
                    llm: OpenAILLM | None = None, entra_key_resolver: Any = None,
                    use_llm: bool = True) -> Container:
    db = db or Database(settings.database_url)
    kv = kv or create_kv(settings.redis_url)
    events = EventBus(settings)
    audit = AuditService(db)
    llm = llm if llm is not None else (build_llm(settings) if use_llm else None)
    if kb is None:
        try:
            kb = KnowledgeBase(build_embedder(llm), settings.chroma_persist_dir, settings.chroma_host, settings.chroma_port)
        except Exception:
            logger.exception("Knowledge base unavailable; RAG features are disabled")
            kb = None
    signals = SignalStore(kv, settings.signal_retention_per_series)
    inventory = InventoryService(db)
    incidents = IncidentService(db, events, audit)
    executors = executors or Executors(
        k8s=KubernetesClient(), aws=AwsVmClient(settings.aws_region), cache=CacheClient(settings.cache_redis_url),
        tickets=TicketClient(settings.ticket_webhook_url, settings.ticket_webhook_token.get_secret_value()),
    )
    remediation = RemediationService(settings, db, kv, kb, inventory, audit, events, executors)

    connectors: list[SignalConnector] = []
    if settings.prometheus_url:
        connectors.append(PrometheusConnector(settings.prometheus_url, load_queries(settings.prometheus_queries_file)))
    if settings.loki_url:
        connectors.append(LokiConnector(settings.loki_url))

    ctx = AgentContext(
        signals=signals, platform=inventory, approvals=remediation, remediation=remediation, stats=ActionStats(kv),
        kb=kb, llm=llm, connectors=connectors, detection_window_minutes=settings.detection_window_minutes,
        verify_wait_seconds=settings.verify_wait_seconds, verify_poll_seconds=settings.verify_poll_seconds,
        max_remediation_attempts=settings.max_remediation_attempts, auto_approve_max_risk=settings.auto_approve_max_risk,
    )
    agents = AgentService(settings, db, kv, events, incidents, remediation, audit, ctx)
    rca = RCAService(db, audit)
    alerts = AlertService(db, events, audit)
    trigger = IncidentTrigger(settings, signals, inventory, incidents, kv, agents.enqueue_analysis)
    poller = ConnectorPoller(connectors, inventory, signals, kv) if connectors else None
    scheduler = Scheduler(settings, trigger, poller, [remediation.expire_pending, agents.auto_resolve_sweep, incidents.refresh_gauges], kv)
    return Container(
        settings=settings, db=db, kv=kv, events=events, audit=audit, llm=llm, kb=kb, signals=signals, inventory=inventory,
        auth=AuthService(settings, db, kv, entra_key_resolver), users=UserService(db), incidents=incidents, rca=rca,
        remediation=remediation, agents=agents, knowledge=KnowledgeService(kb, audit),
        reporting=ReportingService(db, incidents, rca), chat=ChatService(kv, kb, llm, incidents, rca, inventory, remediation),
        alerts=alerts, trigger=trigger, ingestion=IngestionService(signals, alerts, trigger), scheduler=scheduler,
        connectors=connectors,
    )
