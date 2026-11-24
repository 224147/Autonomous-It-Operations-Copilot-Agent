"""End-to-end API workflows: ingestion -> incident -> multi-agent analysis -> approval -> remediation -> learning."""
from __future__ import annotations

from datetime import timedelta

from agents.common.timeutil import iso, utcnow
from agents.evaluation.scenarios import NORMAL, build_scenario
from tests.conftest import drain, make_user

API = "/api/v1"
INGEST = {"X-API-Key": "ingest-key-1"}


def _inject(client, kind: str):
    scenario = build_scenario(kind, utcnow())
    for dep in scenario["deployments"]:
        assert client.post(f"{API}/deployments", json=dep, headers=INGEST).status_code == 201
    assert client.post(f"{API}/ingest/metrics", json={"metrics": scenario["signals"]["metrics"]}, headers=INGEST).status_code == 200
    if scenario["signals"]["logs"]:
        assert client.post(f"{API}/ingest/logs", json={"logs": scenario["signals"]["logs"]}, headers=INGEST).status_code == 200
    if scenario["signals"]["traces"]:
        assert client.post(f"{API}/ingest/traces", json={"spans": scenario["signals"]["traces"]}, headers=INGEST).status_code == 200
    return scenario


def test_ingest_requires_credentials(client):
    assert client.post(f"{API}/ingest/logs", json={"logs": []}).status_code == 401
    assert client.post(f"{API}/ingest/logs", json={"logs": []}, headers={"X-API-Key": "wrong"}).status_code == 401


def test_ingest_key_is_accepted_as_bearer_token_for_webhook_senders(client):
    resp = client.post(f"{API}/ingest/alerts/alertmanager", json={"alerts": []},
                       headers={"Authorization": "Bearer ingest-key-1"})
    assert resp.status_code == 200
    assert client.post(f"{API}/ingest/alerts/alertmanager", json={"alerts": []},
                       headers={"Authorization": "Bearer not-a-key"}).status_code == 401


def test_full_incident_lifecycle(client, container, admin_headers):
    scenario = _inject(client, "memory_exhaustion")
    # the detection sweep (Incident Trigger Service) turns signals into an incident and queues the workflow
    assert container.trigger.sweep()
    incidents = client.get(f"{API}/incidents?active=true", headers=admin_headers).json()
    assert incidents["total"] == 1
    incident_id = incidents["items"][0]["id"]
    assert incidents["items"][0]["service"] == scenario["service"]

    assert drain(container) == 1
    detail = client.get(f"{API}/incidents/{incident_id}", headers=admin_headers).json()
    assert detail["status"] == "awaiting_approval" and detail["severity"] == "P1"
    assert detail["rca"]["category"] == "memory_exhaustion" and detail["rca"]["confidence"] > 0.5
    assert any(e["stage"] == "approval" for e in detail["events"])
    approval = detail["approvals"][0]
    assert approval["status"] == "pending" and approval["plan"][0]["action"] == "restart_pod"
    assert detail["executions"] == [] or all(e["action"] == "open_ticket" for e in detail["executions"]) is False

    # approval by an approver resumes the workflow (dry-run mode: simulated execution)
    approver = make_user(client, admin_headers, "approver")
    assert client.post(f"{API}/remediation/approvals/{approval['id']}/decision", headers=approver,
                       json={"decision": "approve", "comment": "go"}).status_code == 200
    assert client.post(f"{API}/remediation/approvals/{approval['id']}/decision", headers=approver,
                       json={"decision": "approve"}).status_code == 409  # cannot decide twice
    assert drain(container) == 1
    detail = client.get(f"{API}/incidents/{incident_id}", headers=admin_headers).json()
    assert detail["status"] == "monitoring"
    kinds = {(e["action"], e["status"]) for e in detail["executions"]}
    assert ("restart_pod", "simulated") in kinds and ("open_ticket", "succeeded") in kinds
    assert detail["verification"]["status"] == "simulated"
    assert detail["tickets"] and detail["tickets"][0]["priority"] == "P1"

    # fresh healthy metrics -> auto-resolution sweep verifies recovery and resolves
    now = utcnow() + timedelta(seconds=1)
    healthy = [{"service": scenario["service"], "name": n, "value": v, "timestamp": iso(now)} for n, v in NORMAL.items()]
    client.post(f"{API}/ingest/metrics", json={"metrics": healthy}, headers=INGEST)
    from sqlalchemy import update

    from backend.models import Incident

    with container.db.session() as session:  # make the incident old enough for the sweep
        session.execute(update(Incident).values(updated_at=utcnow() - timedelta(minutes=10)))
    assert container.agents.auto_resolve_sweep() == [incident_id]
    resolved = client.get(f"{API}/incidents/{incident_id}", headers=admin_headers).json()
    assert resolved["status"] == "resolved" and resolved["resolved_at"]

    # learning: the resolved incident is now in the knowledge base and findable
    hit = client.post(f"{API}/knowledge/search", headers=admin_headers,
                      json={"query": "payment-service OOMKilled memory", "doc_types": ["incident"]}).json()["results"]
    assert hit and hit[0]["id"] == f"inc-{incident_id}" and hit[0]["metadata"]["outcome"] == "resolved"

    # resolved incident must not be re-opened by the old samples still in the window
    assert container.trigger.sweep() == []

    # reporting & analytics
    report = client.get(f"{API}/reporting/incidents/{incident_id}", headers=admin_headers).json()
    assert "Memory exhaustion" in report["markdown"] and report["mttr_seconds"] is not None
    stats = client.get(f"{API}/incidents/stats", headers=admin_headers).json()
    assert stats["total_incidents"] == 1 and stats["resolved_incidents"] == 1 and stats["by_severity"] == {"P1": 1}
    rca_id = resolved["rca"]["id"]
    operator = make_user(client, admin_headers, "operator")
    fb = client.post(f"{API}/rca/{rca_id}/feedback", headers=operator, json={"result": "correct", "notes": "confirmed"})
    assert fb.status_code == 200 and client.get(f"{API}/rca/accuracy", headers=operator).json()["accuracy"] == 1.0

    audit = client.get(f"{API}/audit?limit=200", headers=admin_headers).json()["items"]
    actions = {a["action"] for a in audit}
    assert {"incident.created", "approval.requested", "approval.approved", "remediation.restart_pod", "incident.resolved"} <= actions
    assert client.get(f"{API}/audit/verify", headers=admin_headers).json()["valid"] is True


def test_rejection_escalates_with_ticket(client, container, admin_headers):
    _inject(client, "deployment_regression")
    container.trigger.sweep()
    drain(container)
    incident = client.get(f"{API}/incidents", headers=admin_headers).json()["items"][0]
    approval = client.get(f"{API}/remediation/approvals?status=pending", headers=admin_headers).json()[0]
    assert approval["plan"][0]["action"] == "rollback_deployment"
    client.post(f"{API}/remediation/approvals/{approval['id']}/decision", headers=admin_headers,
                json={"decision": "reject", "comment": "too risky right now"})
    drain(container)
    detail = client.get(f"{API}/incidents/{incident['id']}", headers=admin_headers).json()
    assert detail["status"] == "escalated"
    assert not [e for e in detail["executions"] if e["action"] != "open_ticket"]
    assert detail["tickets"]


def test_alertmanager_webhook_triggers_incident(client, container, admin_headers):
    payload = {"alerts": [{"status": "firing", "labels": {"alertname": "PodCrashLooping", "severity": "critical",
                                                          "service": "orders-service"},
                           "annotations": {"summary": "orders-service is crash looping"},
                           "startsAt": iso(utcnow()), "fingerprint": "abc123"}]}
    resp = client.post(f"{API}/ingest/alerts/alertmanager", json=payload, headers=INGEST)
    assert resp.status_code == 200 and resp.json()["accepted"] == 1
    assert len(resp.json()["incidents_triggered"]) == 1  # a critical alert alone crosses the incident threshold
    alerts = client.get(f"{API}/alerts?status=firing", headers=admin_headers).json()
    assert alerts["total"] == 1 and alerts["items"][0]["incident_id"] is not None

    # same fingerprint again de-duplicates; resolved webhook closes the alert
    client.post(f"{API}/ingest/alerts/alertmanager", json=payload, headers=INGEST)
    assert client.get(f"{API}/alerts", headers=admin_headers).json()["total"] == 1
    payload["alerts"][0]["status"] = "resolved"
    payload["alerts"][0]["endsAt"] = iso(utcnow())
    client.post(f"{API}/ingest/alerts/alertmanager", json=payload, headers=INGEST)
    assert client.get(f"{API}/alerts?status=resolved", headers=admin_headers).json()["total"] == 1
    alert_id = alerts["items"][0]["id"]
    assert client.post(f"{API}/alerts/{alert_id}/acknowledge", headers=admin_headers).status_code == 200


def test_cascading_failure_correlates_to_root_service(client, container, admin_headers):
    """api-gateway and orders-service both degrade because postgres ran out of disk."""
    now = utcnow()
    scenario = build_scenario("disk_full", now)  # root cause on postgres
    victims = build_scenario("db_connectivity", now)  # orders-service errors
    metrics = scenario["signals"]["metrics"] + victims["signals"]["metrics"]
    client.post(f"{API}/ingest/metrics", json={"metrics": metrics}, headers=INGEST)
    client.post(f"{API}/ingest/logs", json={"logs": scenario["signals"]["logs"] + victims["signals"]["logs"]}, headers=INGEST)
    created = container.trigger.sweep()
    assert len(created) == 1  # one correlated incident, not one per service
    drain(container)
    detail = client.get(f"{API}/incidents/{created[0]}", headers=admin_headers).json()
    assert set(detail["affected_services"]) >= {"postgres", "orders-service"}
    assert detail["rca"]["category"] == "disk_full"
    assert "postgres" in detail["rca"]["root_cause"]


def test_manual_incident_and_workflow_run(client, container, admin_headers):
    resp = client.post(f"{API}/agents/workflows/run", headers=admin_headers, json={"service": "inventory-service"})
    assert resp.status_code == 202
    drain(container)
    detail = client.get(f"{API}/incidents/{resp.json()['incident_id']}", headers=admin_headers).json()
    assert detail["status"] == "false_positive"  # no anomalous data -> no incident
    assert client.post(f"{API}/agents/workflows/run", headers=admin_headers, json={"service": "nope"}).status_code == 422


def test_chat_is_grounded_in_platform_data(client, container, admin_headers):
    _inject(client, "memory_exhaustion")
    container.trigger.sweep()
    drain(container)
    resp = client.post(f"{API}/chat", headers=admin_headers, json={"message": "What is the root cause of INC-1?"})
    body = resp.json()
    assert resp.status_code == 200 and body["llm_used"] is False
    assert "Memory exhaustion" in body["answer"] and {"type": "incident", "id": 1} in body["sources"]
    again = client.post(f"{API}/chat", headers=admin_headers,
                        json={"message": "which incidents are active?", "session_id": body["session_id"]}).json()
    assert "active incident" in again["answer"]
    history = client.get(f"{API}/chat/{body['session_id']}", headers=admin_headers).json()
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    runbook = client.post(f"{API}/chat", headers=admin_headers, json={"message": "how do I handle a crash loop runbook?"}).json()
    assert any(s["id"] == "rb-crash-loop" for s in runbook["sources"])


def test_knowledge_endpoints_and_permissions(client, admin_headers):
    operator = make_user(client, admin_headers, "operator")
    doc = {"id": "rb-custom", "doc_type": "runbook", "title": "Custom runbook", "content": "Step 1: restart the widget service.",
           "metadata": {"category": "widget_failure", "steps": [{"action": "restart_service"}]}}
    assert client.post(f"{API}/knowledge/documents", json=doc, headers=operator).status_code == 403
    assert client.post(f"{API}/knowledge/documents", json=doc, headers=admin_headers).status_code == 201
    found = client.post(f"{API}/knowledge/search", json={"query": "widget", "filters": {"category": "widget_failure"}},
                        headers=operator).json()["results"]
    assert [r["id"] for r in found] == ["rb-custom"]
    assert client.delete(f"{API}/knowledge/documents/rb-custom", headers=admin_headers).status_code == 204
    assert client.get(f"{API}/knowledge/documents/rb-custom", headers=admin_headers).status_code == 404


def test_service_health_reflects_signals(client, admin_headers):
    _inject(client, "memory_exhaustion")
    health = {h["name"]: h for h in client.get(f"{API}/services/health", headers=admin_headers).json()}
    assert health["payment-service"]["state"] == "critical"
    assert health["notification-service"]["state"] == "unknown"
    assert "memory_usage_percent" in health["payment-service"]["metrics"]


def test_input_validation_rejects_bad_payloads(client, admin_headers):
    assert client.post(f"{API}/ingest/metrics", json={"metrics": [{"service": "x", "name": "cpu", "value": "abc"}]},
                       headers=INGEST).status_code == 422
    assert client.post(f"{API}/ingest/alerts/alertmanager", content=b"not json", headers=INGEST).status_code == 422
    assert client.put(f"{API}/services/Bad Name", json={"name": "bad name"}, headers=admin_headers).status_code == 422
    assert client.get(f"{API}/incidents/99999", headers=admin_headers).status_code == 404
    assert client.get(f"{API}/incidents?limit=100000", headers=admin_headers).status_code == 422
