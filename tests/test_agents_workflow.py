"""Detection, normalization and workflow orchestration tests (pure agent logic with in-memory ports)."""
from __future__ import annotations

import random
from datetime import timedelta

import pytest

from agents.common.timeutil import iso, utcnow
from agents.evaluation.fakes import FakePlatform, FakeRemediation, make_context
from agents.evaluation.harness import evaluate, run_scenario
from agents.evaluation.scenarios import build_scenario, metric_series
from agents.incident_agent.detector import detect
from agents.monitoring_agent.normalizer import NormalizationAgent
from workflows.incident_workflow import build_workflow, run_workflow


def test_normalizer_maps_aliases_and_drops_invalid():
    state = {"raw_signals": {
        "metrics": [{"service": "A", "name": "CPU", "value": "91", "timestamp": utcnow().isoformat()},
                    {"service": "a", "name": "cpu", "value": "nan", "timestamp": utcnow().isoformat()},
                    {"service": "", "name": "cpu", "value": 1}],
        "logs": [{"service": "a", "message": "FATAL: boom", "timestamp": utcnow().isoformat()}],
    }}
    out = NormalizationAgent().run(state)["signals"]
    assert [m["name"] for m in out["metrics"]] == ["cpu_usage_percent"]
    assert out["metrics"][0]["value"] == 91.0 and out["metrics"][0]["service"] == "a"
    assert out["logs"][0]["severity"] == "critical"


def test_single_warning_breach_is_not_an_incident():
    now = utcnow()
    scenario = build_scenario("noisy_healthy", now)
    signals = NormalizationAgent().run({"raw_signals": scenario["signals"]})["signals"]
    assert detect(signals)["is_incident"] is False


def test_oom_logs_and_memory_breach_form_an_incident():
    scenario = build_scenario("memory_exhaustion", utcnow())
    signals = NormalizationAgent().run({"raw_signals": scenario["signals"]})["signals"]
    result = detect(signals)
    assert result["is_incident"]
    assert {a["category_hint"] for a in result["anomalies"]} >= {"memory_exhaustion"}


def test_current_only_ignores_recovered_metrics():
    now = utcnow()
    rng = random.Random(1)
    series = metric_series("svc", now, rng, {"memory_usage_percent": (10, 150.0)})
    # replace the last points with healthy values (recovery)
    for m in series:
        if m["name"] == "memory_usage_percent" and m["timestamp"] >= iso(now - timedelta(seconds=60)):
            m["value"] = 50.0
    signals = NormalizationAgent().run({"raw_signals": {"metrics": series}})["signals"]
    assert detect(signals, current_only=False)["anomalies"]
    assert detect(signals, current_only=True)["anomalies"] == []


def _scenario_ctx(kind: str, auto_approve: str = "none", remediation: FakeRemediation | None = None):
    scenario = build_scenario(kind, utcnow())
    ctx = make_context(platform=FakePlatform(deployments=scenario["deployments"], alerts=scenario["alerts"]),
                       auto_approve=auto_approve, remediation=remediation)
    for sig_kind, key in (("metric", "metrics"), ("log", "logs"), ("trace", "traces")):
        ctx.signals.add(sig_kind, scenario["signals"][key])
    return scenario, ctx


def _initial(scenario):
    return {"incident_id": 7, "trigger": {"service": scenario["service"], "window_minutes": 15},
            "executions": [], "events": [], "errors": [], "attempts": 0}


def test_workflow_pauses_for_human_approval_then_resumes_after_approval():
    scenario, ctx = _scenario_ctx("memory_exhaustion")
    graph = build_workflow(ctx)
    state = run_workflow(graph, _initial(scenario))
    assert state["status"] == "awaiting_approval"
    assert not ctx.remediation.calls
    approval_id = state["approval"]["id"]

    ctx.approvals.decide(approval_id, "approved", approved_actions=[], by="alice")
    resumed = run_workflow(graph, {**state, "resume_stage": "approval", "status": "running"})
    recovery = [e for e in resumed["executions"] if e["action"] != "open_ticket"]
    assert recovery and recovery[0]["action"] == "restart_pod"
    assert resumed["report"]["root_cause"].startswith("Memory exhaustion")
    assert resumed["learning"]["indexed"] is False  # no KB configured in this context


def test_rejected_plan_never_executes_and_is_reported():
    scenario, ctx = _scenario_ctx("memory_exhaustion")
    graph = build_workflow(ctx)
    state = run_workflow(graph, _initial(scenario))
    ctx.approvals.decide(state["approval"]["id"], "rejected", by="bob")
    final = run_workflow(graph, {**state, "resume_stage": "approval", "status": "running"})
    assert final["status"] == "rejected"
    assert all(e["action"] == "open_ticket" for e in final["executions"])
    assert not [c for c in ctx.remediation.calls if c["action"] != "open_ticket"]


def test_partial_approval_only_runs_selected_actions():
    scenario, ctx = _scenario_ctx("memory_exhaustion")
    graph = build_workflow(ctx)
    state = run_workflow(graph, _initial(scenario))
    plan = state["recommendations"]["actions"]
    chosen = next(a["id"] for a in plan if a["action"] == "scale_deployment")
    ctx.approvals.decide(state["approval"]["id"], "approved", approved_actions=[chosen])
    final = run_workflow(graph, {**state, "resume_stage": "approval", "status": "running"})
    ran = [e["action"] for e in final["executions"] if e["action"] != "open_ticket"]
    assert ran == ["scale_deployment"]


def test_auto_approve_low_risk_plan_runs_without_human():
    scenario, ctx = _scenario_ctx("traffic_spike", auto_approve="low")
    state = run_workflow(build_workflow(ctx), _initial(scenario))
    assert state["approval"]["status"] == "approved" and state["approval"]["decided_by"] == "policy"
    assert any(e["action"] == "scale_deployment" for e in state["executions"])


def test_verification_healthy_marks_resolved_and_learns():
    remediation = FakeRemediation(status="succeeded", mode="live")
    scenario, ctx = _scenario_ctx("traffic_spike", auto_approve="low", remediation=remediation)
    original = remediation.execute

    def execute_and_recover(incident_id, approval_id, action):
        result = original(incident_id, approval_id, action)
        future = utcnow() + timedelta(seconds=30)
        ctx.signals.add("metric", [{"service": scenario["service"], "name": n, "value": v, "timestamp": iso(future)}
                                   for n, v in (("cpu_usage_percent", 40.0), ("latency_p99_ms", 200.0))])
        return result

    remediation.execute = execute_and_recover  # type: ignore[method-assign]
    state = run_workflow(build_workflow(ctx), _initial(scenario))
    assert state["verification"]["status"] == "healthy"
    assert state["status"] == "resolved"
    assert ctx.stats.get("traffic_spike", "scale_deployment") == (1, 1)


def test_failed_remediation_tries_next_action_then_escalates():
    remediation = FakeRemediation(status="failed", mode="live")
    scenario, ctx = _scenario_ctx("memory_exhaustion", auto_approve="none", remediation=remediation)
    graph = build_workflow(ctx)
    state = run_workflow(graph, _initial(scenario))
    ctx.approvals.decide(state["approval"]["id"], "approved")
    final = run_workflow(graph, {**state, "resume_stage": "approval", "status": "running"})
    attempts = [e for e in final["executions"] if e["action"] != "open_ticket"]
    assert len(attempts) == ctx.max_remediation_attempts  # bounded retries
    assert final["status"] == "escalated"
    assert ctx.stats.get("memory_exhaustion", "restart_pod") == (0, 1)


def test_dry_run_is_not_reported_as_resolved():
    remediation = FakeRemediation(status="simulated", mode="dry_run")
    scenario, ctx = _scenario_ctx("traffic_spike", auto_approve="low", remediation=remediation)
    state = run_workflow(build_workflow(ctx), _initial(scenario))
    assert state["verification"]["status"] == "simulated"
    assert state["status"] == "monitoring"


def test_agent_exception_is_contained_and_reported():
    scenario, ctx = _scenario_ctx("memory_exhaustion")
    ctx.platform = None  # type: ignore[assignment]  # monitoring agent will crash
    state = run_workflow(build_workflow(ctx), _initial(scenario))
    assert state["status"] == "error"
    assert state["errors"][0]["stage"] == "monitoring_agent"
    assert state["report"]["status"] == "error"


def test_no_incident_short_circuits_to_report():
    scenario, ctx = _scenario_ctx("healthy")
    state = run_workflow(build_workflow(ctx), _initial(scenario))
    assert state["status"] == "no_incident"
    assert "rca" not in state


@pytest.mark.parametrize("kind,category,root", [
    ("dependency_failure", "dependency_failure", "fraud-check-api"),
    ("db_connectivity", "db_connectivity", "orders-service"),
    ("disk_full", "disk_full", "postgres"),
])
def test_rca_identifies_expected_cause_and_service(kind, category, root):
    result = run_scenario(kind, seed=3)
    rca = result["state"]["rca"]
    assert rca["category"] == category and rca["root_service"] == root
    assert 0 < rca["confidence"] <= 0.98


def test_severity_scales_with_service_tier():
    p1 = run_scenario("memory_exhaustion", 1)["state"]["severity"]["level"]
    p_low = run_scenario("config_error", 1)["state"]["severity"]["level"]  # tier-3 service
    assert p1 == "P1" and p_low > p1  # a tier-3 service ranks below a tier-1 outage


def test_success_criteria_on_synthetic_scenarios():
    report = evaluate(seeds=3)
    assert report["detection_accuracy"] >= 0.90
    assert report["rca_accuracy"] >= 0.85
    assert report["automated_remediation_coverage"] >= 0.70, report["failures"]
