"""Remediation engine safety model, Kubernetes primitives and integrations."""
from __future__ import annotations

from types import SimpleNamespace as NS

import fakeredis
import httpx
import pytest

from infrastructure.integrations import CacheClient, IntegrationError, TicketClient
from infrastructure.kubernetes import KubernetesClient, KubernetesError
from tests.conftest import make_user

API = "/api/v1"


def _approved_step(client, admin_headers, action="restart_service", target="payment-service", params=None):
    resp = client.post(f"{API}/remediation/execute", headers=admin_headers,
                       json={"action": action, "target": target, "params": params or {}, "justification": "unit test run"})
    return resp


def test_actions_without_approval_are_rejected(container):
    result = container.remediation.execute(None, None, {"id": "a1", "action": "restart_service", "target": "payment-service"})
    assert result["status"] == "rejected" and "approval" in result["error"]
    # approval exists but is still pending
    record = container.remediation.request(0, [{"id": "a1", "action": "restart_service", "target": "payment-service", "risk": "medium"}],
                                           "medium", False)
    result = container.remediation.execute(None, record["id"], {"id": "a1", "action": "restart_service", "target": "payment-service"})
    assert result["status"] == "rejected"


def test_approved_plan_cannot_be_used_for_a_different_action_or_target(container):
    plan = [{"id": "a1", "action": "restart_pod", "target": "payment-service", "risk": "low"}]
    record = container.remediation.request(0, plan, "low", True)  # auto-approved
    ok = container.remediation.execute(None, record["id"], plan[0])
    assert ok["status"] == "simulated"
    swapped_action = container.remediation.execute(None, record["id"], {**plan[0], "action": "restart_vm"})
    swapped_target = container.remediation.execute(None, record["id"], {**plan[0], "target": "postgres"})
    assert swapped_action["status"] == swapped_target["status"] == "rejected"
    assert "approved plan" in swapped_target["error"]


def test_dry_run_never_touches_infrastructure(client, admin_headers, fake_k8s):
    resp = _approved_step(client, admin_headers, "rollback_deployment")
    assert resp.status_code == 200 and resp.json()["status"] == "simulated" and resp.json()["mode"] == "dry_run"
    assert fake_k8s.calls == []


def test_live_mode_executes_every_action_type(container, settings, fake_k8s):
    live = container.remediation
    live._s = settings.model_copy(update={"remediation_mode": "live", "remediation_cooldown_seconds": 0})  # noqa: SLF001
    cases = [
        ("restart_service", "payment-service", {}, "rollout_restart"),
        ("restart_pod", "payment-service", {}, "restart_pod"),
        ("rollback_deployment", "payment-service", {}, "rollback"),
        ("scale_deployment", "payment-service", {"replicas_delta": 99}, "scale"),
        ("restart_database", "postgres", {}, "rollout_restart"),
        ("trigger_backup", "postgres", {}, "trigger_backup"),
    ]
    for idx, (action, target, params, expected) in enumerate(cases):
        step = {"id": "m1", "action": action, "target": target, "params": params, "risk": "low"}
        result = live.execute_manual("alice", "approver", None, action, target, params, "live test run")
        assert result["status"] == "succeeded", (action, result)
        assert fake_k8s.calls[idx][0] == expected, step
    assert fake_k8s.calls[3][1][2] == 5  # replicas_delta is clamped to +5
    assert fake_k8s.calls[4][1][2] == "statefulset"
    vm = live.execute_manual("alice", "approver", None, "restart_vm", "legacy-batch", {}, "reboot the batch vm")
    assert vm["status"] == "succeeded" and vm["output"]["rebooted_instance"] == "i-0abc123demo"


def test_live_mode_guards_cooldown_and_namespace(container, settings, fake_k8s):
    live = container.remediation
    live._s = settings.model_copy(update={"remediation_mode": "live", "remediation_cooldown_seconds": 300,  # noqa: SLF001
                                          "remediation_allowed_namespaces": "production"})
    first = live.execute_manual("alice", "approver", None, "restart_service", "orders-service", {}, "first restart")
    second = live.execute_manual("alice", "approver", None, "restart_service", "orders-service", {}, "second restart")
    assert first["status"] == "succeeded"
    assert second["status"] == "rejected" and "cooldown" in second["error"]
    assert len(fake_k8s.calls) == 1

    from backend.schemas import ServiceIn

    container.inventory.upsert_service(ServiceIn(name="sandbox-app", namespace="sandbox", deployment="sandbox-app"))
    blocked = live.execute_manual("alice", "approver", None, "restart_service", "sandbox-app", {}, "outside allow list")
    assert blocked["status"] == "rejected" and "REMEDIATION_ALLOWED_NAMESPACES" in blocked["error"]


def test_unknown_targets_actions_and_param_validation(client, admin_headers):
    assert _approved_step(client, admin_headers, target="ghost").json()["status"] == "rejected"
    assert client.post(f"{API}/remediation/execute", headers=admin_headers,
                       json={"action": "rm_rf", "target": "payment-service", "justification": "bad action"}).status_code == 422
    assert _approved_step(client, admin_headers, "execute_runbook", params={}).json()["status"] == "rejected"
    assert client.post(f"{API}/remediation/execute", headers=admin_headers,
                       json={"action": "restart_pod", "target": "payment-service", "justification": "x"}).status_code == 422


def test_runbook_execution_dry_run_and_live(client, container, admin_headers, settings, fake_k8s):
    dry = _approved_step(client, admin_headers, "execute_runbook", "payment-service", {"runbook_id": "rb-memory-exhaustion"}).json()
    assert dry["status"] == "simulated" and [s["action"] for s in dry["output"]["steps"]] == ["restart_pod", "scale_deployment"]
    missing = _approved_step(client, admin_headers, "execute_runbook", "payment-service", {"runbook_id": "nope"}).json()
    assert missing["status"] == "rejected"

    container.remediation._s = settings.model_copy(update={"remediation_mode": "live", "remediation_cooldown_seconds": 0})  # noqa: SLF001
    live = _approved_step(client, admin_headers, "execute_runbook", "payment-service", {"runbook_id": "rb-memory-exhaustion"}).json()
    assert live["status"] == "succeeded"
    assert [c[0] for c in fake_k8s.calls] == ["restart_pod", "scale"]


def test_runbook_with_unsupported_step_is_refused(client, admin_headers):
    doc = {"id": "rb-evil", "doc_type": "runbook", "title": "Evil", "content": "x",
           "metadata": {"steps": [{"action": "execute_runbook"}, {"action": "shell", "cmd": "rm -rf /"}]}}
    assert client.post(f"{API}/knowledge/documents", json=doc, headers=admin_headers).status_code == 201
    result = _approved_step(client, admin_headers, "execute_runbook", "payment-service", {"runbook_id": "rb-evil"}).json()
    assert result["status"] == "rejected" and "unsupported action" in result["error"]


def test_approval_expiry_and_partial_selection(client, container, admin_headers):
    plan = [{"id": "a1", "action": "restart_pod", "target": "payment-service", "risk": "low"},
            {"id": "a2", "action": "scale_deployment", "target": "payment-service", "risk": "low"}]
    record = container.remediation.request(0, plan, "low", False)
    approver = make_user(client, admin_headers, "approver")
    bad = client.post(f"{API}/remediation/approvals/{record['id']}/decision", headers=approver,
                      json={"decision": "approve", "approved_actions": ["zzz"]})
    assert bad.status_code == 422
    ok = client.post(f"{API}/remediation/approvals/{record['id']}/decision", headers=approver,
                     json={"decision": "approve", "approved_actions": ["a2"]})
    assert ok.json()["approved_actions"] == ["a2"] and ok.json()["decided_by"] == "approver@test.example"
    assert container.remediation.execute(None, record["id"], plan[0])["status"] == "rejected"  # a1 was not selected
    assert container.remediation.execute(None, record["id"], plan[1])["status"] == "simulated"

    from datetime import timedelta

    from sqlalchemy import update

    from agents.common.timeutil import utcnow
    from backend.models import ApprovalRequest

    stale = container.remediation.request(0, plan, "low", False)
    with container.db.session() as session:
        session.execute(update(ApprovalRequest).where(ApprovalRequest.id == stale["id"]).values(expires_at=utcnow() - timedelta(minutes=1)))
    assert client.post(f"{API}/remediation/approvals/{stale['id']}/decision", headers=approver,
                       json={"decision": "approve"}).status_code == 409


def test_ticket_webhook_forwarding_and_local_fallback(container):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"], seen["body"] = request.headers.get("authorization"), request.read()
        return httpx.Response(200, json={"key": "OPS-42"})

    container.remediation._x.tickets = TicketClient("https://itsm.example/hook", "tkn", httpx.MockTransport(handler))  # noqa: SLF001
    ticket = container.remediation.create_ticket(None, "Disk full", "details", "P2")
    assert ticket.external_ref == "OPS-42" and seen["auth"] == "Bearer tkn"

    failing = TicketClient("https://itsm.example/hook", "", httpx.MockTransport(lambda r: httpx.Response(500)))
    container.remediation._x.tickets = failing  # noqa: SLF001
    local = container.remediation.create_ticket(None, "Still tracked", "x", "P3")
    assert local.id and local.external_ref == ""  # an ITSM outage never loses the ticket


def test_cache_client_clears_only_the_prefix():
    server = fakeredis.FakeRedis(decode_responses=True)
    for key in ("orders:1", "orders:2", "payments:1", "other"):
        server.set(key, "x")
    result = CacheClient("", client=server).clear_prefix("orders:")
    assert result["keys_deleted"] == 2 and sorted(server.keys()) == ["other", "payments:1"]
    with pytest.raises(IntegrationError):
        CacheClient("", client=server).clear_prefix("")
    with pytest.raises(IntegrationError):
        CacheClient("").clear_prefix("orders:")


# ------------------------------------------------------------------ Kubernetes client against fake APIs
class _Apps:
    def __init__(self) -> None:
        self.patches: list[tuple] = []
        meta = lambda rev, owner="web": NS(  # noqa: E731
            metadata=NS(annotations={"deployment.kubernetes.io/revision": str(rev)},
                        owner_references=[NS(kind="Deployment", name=owner)]),
            spec=NS(template={"metadata": {"labels": {"app": "web", "pod-template-hash": str(rev)}}, "spec": {"image": f"v{rev}"}}))
        self.rs = [meta(1), meta(2), meta(3), meta(9, owner="other")]
        self.replicas = 3

    def read_namespaced_deployment(self, name, ns):  # noqa: ANN001
        return NS(spec=NS(selector=NS(match_labels={"app": "web"})))

    read_namespaced_stateful_set = read_namespaced_deployment

    def list_namespaced_replica_set(self, ns, label_selector):  # noqa: ANN001
        return NS(items=self.rs)

    def patch_namespaced_deployment(self, name, ns, body):  # noqa: ANN001
        self.patches.append(("deployment", name, body))

    def patch_namespaced_stateful_set(self, name, ns, body):  # noqa: ANN001
        self.patches.append(("statefulset", name, body))

    def read_namespaced_deployment_scale(self, name, ns):  # noqa: ANN001
        return NS(spec=NS(replicas=self.replicas))

    def patch_namespaced_deployment_scale(self, name, ns, body):  # noqa: ANN001
        self.patches.append(("scale", name, body))


class _Core:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        status = lambda ready, restarts: NS(container_statuses=[NS(ready=ready, restart_count=restarts)])  # noqa: E731
        self.pods = [NS(metadata=NS(name="web-ok"), status=status(True, 0)),
                     NS(metadata=NS(name="web-bad"), status=status(False, 7)),
                     NS(metadata=NS(name="web-flaky"), status=status(True, 3))]

    def list_namespaced_pod(self, ns, label_selector):  # noqa: ANN001
        return NS(items=self.pods)

    def delete_namespaced_pod(self, name, ns):  # noqa: ANN001
        self.deleted.append(name)


class _Api:
    def sanitize_for_serialization(self, obj):  # noqa: ANN001
        return {"metadata": {"labels": dict(obj["metadata"]["labels"])}, "spec": dict(obj["spec"])}


def test_kubernetes_rollback_restores_previous_revision_template():
    apps = _Apps()
    k8s = KubernetesClient(apps=apps, core=_Core(), batch=None, api_client=_Api())
    out = k8s.rollback("production", "web")
    assert out == {"rolled_back_to_revision": 2, "from_revision": 3, "namespace": "production"}
    patched = apps.patches[0][2]["spec"]["template"]
    assert patched["spec"]["image"] == "v2" and "pod-template-hash" not in patched["metadata"]["labels"]
    apps.rs = apps.rs[:1]
    with pytest.raises(KubernetesError):
        k8s.rollback("production", "web")


def test_kubernetes_restart_pod_targets_least_healthy_and_scale_is_bounded():
    core, apps = _Core(), _Apps()
    k8s = KubernetesClient(apps=apps, core=core, batch=None, api_client=_Api())
    assert k8s.restart_pod("production", "web")["deleted_pod"] == "web-bad"
    assert core.deleted == ["web-bad"]
    assert k8s.scale("production", "web", 2)["to"] == 5
    apps.replicas = 49
    assert k8s.scale("production", "web", 5)["to"] == 50  # hard ceiling
    apps.replicas = 2
    assert k8s.scale("production", "web", -5)["to"] == 1  # never scales to zero
    assert k8s.rollout_restart("production", "db", "statefulset")["restarted"] == "statefulset/db"
    assert apps.patches[-1][0] == "statefulset"
