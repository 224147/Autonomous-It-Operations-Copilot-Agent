"""LangGraph orchestration of the multi-agent incident workflow.

START -> Monitoring -> Data Normalization -> Incident Detection -> Severity Classification -> Correlation
      -> Root Cause Analysis -> Knowledge Retrieval -> Recommendation -> Human Approval -> Remediation
      -> Verification -> Learning -> Reporting -> END

The Human Approval node pauses the graph (ends the run with status ``awaiting_approval``). The persisted
state is later resumed with ``resume_stage="approval"``, which re-enters the graph at the approval node.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.approval_agent.agent import ApprovalAgent
from agents.common.ports import AgentContext
from agents.correlation_agent.agent import CorrelationAgent
from agents.incident_agent.agent import IncidentDetectionAgent, SeverityClassificationAgent
from agents.knowledge_agent.agent import KnowledgeAgent
from agents.learning_agent.agent import LearningAgent
from agents.monitoring_agent.agent import MonitoringAgent
from agents.monitoring_agent.normalizer import NormalizationAgent
from agents.rca_agent.agent import RCAAgent
from agents.recommendation_agent.agent import RecommendationAgent
from agents.remediation_agent.agent import RemediationAgent
from agents.reporting_agent.agent import ReportingAgent
from agents.verification_agent.agent import VerificationAgent
from backend import metrics
from workflows.state import WorkflowState, event

logger = logging.getLogger(__name__)

NodeFn = Callable[[WorkflowState], dict[str, Any]]


def safe_node(name: str, fn: NodeFn, fatal: bool = True, fallback: dict[str, Any] | None = None) -> NodeFn:
    """Time the node, record metrics and convert exceptions into workflow errors."""

    def wrapper(state: WorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            return fn(state)
        except Exception as exc:
            logger.exception("Agent %s failed", name)
            metrics.AGENT_ERRORS.labels(agent=name).inc()
            update: dict[str, Any] = {
                "errors": [{"stage": name, "error": f"{type(exc).__name__}: {exc}"}],
                "events": [event(name, f"Stage failed: {type(exc).__name__}")],
            }
            if fatal:
                update["status"] = "error"
            update.update(fallback or {})
            return update
        finally:
            metrics.AGENT_LATENCY.labels(agent=name).observe(time.perf_counter() - started)

    return wrapper


def _failed(state: WorkflowState) -> bool:
    return state.get("status") == "error"


def _remaining_recovery(state: WorkflowState) -> bool:
    done = {e["action_id"] for e in state.get("executions", [])}
    return any(a["id"] not in done and a["action"] != "open_ticket" for a in state.get("approved_actions", []))


def build_workflow(ctx: AgentContext):
    """Compile the incident-response graph."""
    approval = ApprovalAgent(ctx)

    def approval_node(state: WorkflowState) -> dict[str, Any]:
        update = approval.run(state)
        update["resume_stage"] = None
        return update

    graph = StateGraph(WorkflowState)
    graph.add_node("monitoring", safe_node("monitoring_agent", MonitoringAgent(ctx).run))
    graph.add_node("normalization", safe_node("normalization_agent", NormalizationAgent().run))
    graph.add_node("incident_detection", safe_node("incident_detection_agent", IncidentDetectionAgent().run))
    graph.add_node("severity", safe_node("severity_classification_agent", SeverityClassificationAgent(ctx).run))
    graph.add_node("correlation", safe_node("correlation_agent", CorrelationAgent(ctx).run, fatal=False,
                                            fallback={"correlation": {"root_candidates": [], "timeline": [], "service_impact": [],
                                                                      "recent_deployments": [], "failing_calls": [],
                                                                      "propagation_paths": []}}))
    graph.add_node("rca", safe_node("rca_agent", RCAAgent(ctx).run))
    graph.add_node("knowledge", safe_node("knowledge_agent", KnowledgeAgent(ctx).run, fatal=False))
    graph.add_node("recommendation", safe_node("recommendation_agent", RecommendationAgent(ctx).run))
    graph.add_node("human_approval", safe_node("approval_agent", approval_node))
    graph.add_node("remediation", safe_node("remediation_agent", RemediationAgent(ctx).run))
    graph.add_node("verification", safe_node("verification_agent", VerificationAgent(ctx).run, fatal=False,
                                             fallback={"verification": {"status": "unknown", "healthy": None,
                                                                        "details": "Verification could not be completed"}}))
    graph.add_node("learning", safe_node("learning_agent", LearningAgent(ctx).run, fatal=False))
    graph.add_node("reporting", safe_node("reporting_agent", ReportingAgent(ctx).run, fatal=False))

    graph.add_conditional_edges(START, lambda s: "human_approval" if s.get("resume_stage") == "approval" else "monitoring",
                                {"human_approval": "human_approval", "monitoring": "monitoring"})

    def chain(src: str, dst: str) -> None:
        graph.add_conditional_edges(src, lambda s: "reporting" if _failed(s) else dst, {"reporting": "reporting", dst: dst})

    chain("monitoring", "normalization")
    chain("normalization", "incident_detection")
    graph.add_conditional_edges(
        "incident_detection",
        lambda s: "reporting" if _failed(s) or not (s.get("detection") or {}).get("is_incident") else "severity",
        {"reporting": "reporting", "severity": "severity"},
    )
    chain("severity", "correlation")
    chain("correlation", "rca")
    chain("rca", "knowledge")
    chain("knowledge", "recommendation")
    chain("recommendation", "human_approval")

    def after_approval(s: WorkflowState) -> str:
        if _failed(s):
            return "reporting"
        if s.get("status") == "awaiting_approval":
            return "pause"
        return "remediation" if s.get("approved_actions") and s.get("status") != "rejected" else "learning"

    graph.add_conditional_edges("human_approval", after_approval,
                                {"reporting": "reporting", "pause": END, "remediation": "remediation", "learning": "learning"})
    chain("remediation", "verification")

    def after_verification(s: WorkflowState) -> str:
        status = (s.get("verification") or {}).get("status")
        if status in ("unhealthy", "failed") and s.get("attempts", 0) < ctx.max_remediation_attempts and _remaining_recovery(s):
            return "remediation"
        return "learning"

    graph.add_conditional_edges("verification", after_verification, {"remediation": "remediation", "learning": "learning"})
    graph.add_edge("learning", "reporting")
    graph.add_edge("reporting", END)
    return graph.compile()


def run_workflow(compiled: Any, initial: WorkflowState) -> WorkflowState:
    """Execute the graph synchronously and return the final state."""
    return compiled.invoke(initial, config={"recursion_limit": 60})
