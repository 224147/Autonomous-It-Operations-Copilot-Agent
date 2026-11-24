"""Kubernetes remediation primitives (rollout restart, pod restart, rollback, scale, backup job)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

REVISION_ANNOTATION = "deployment.kubernetes.io/revision"
MAX_REPLICAS = 50


class KubernetesError(RuntimeError):
    pass


class KubernetesClient:
    def __init__(self, apps=None, core=None, batch=None, api_client=None) -> None:  # noqa: ANN001
        self._apps, self._core, self._batch, self._api = apps, core, batch, api_client

    def _init(self) -> None:
        if self._apps is not None:
            return
        try:
            from kubernetes import client, config
        except ImportError as exc:  # pragma: no cover
            raise KubernetesError("kubernetes package is not installed") from exc
        try:
            config.load_incluster_config()
        except Exception:
            try:
                config.load_kube_config()
            except Exception as exc:
                raise KubernetesError(f"no Kubernetes configuration available: {exc}") from exc
        self._apps, self._core, self._batch = client.AppsV1Api(), client.CoreV1Api(), client.BatchV1Api()
        self._api = client.ApiClient()

    # ------------------------------------------------------------------ helpers
    def _patch_workload(self, kind: str, namespace: str, name: str, body: dict[str, Any]) -> None:
        if kind == "statefulset":
            self._apps.patch_namespaced_stateful_set(name, namespace, body)
        else:
            self._apps.patch_namespaced_deployment(name, namespace, body)

    # ------------------------------------------------------------------ actions
    def rollout_restart(self, namespace: str, name: str, kind: str = "deployment") -> dict[str, Any]:
        self._init()
        stamp = datetime.now(UTC).isoformat()
        body = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": stamp}}}}}
        self._patch_workload(kind, namespace, name, body)
        return {"restarted": f"{kind}/{name}", "namespace": namespace, "restartedAt": stamp}

    def restart_pod(self, namespace: str, deployment: str, kind: str = "deployment") -> dict[str, Any]:
        """Delete the least healthy pod of the workload; its controller recreates it."""
        self._init()
        workload = (
            self._apps.read_namespaced_stateful_set(deployment, namespace)
            if kind == "statefulset" else self._apps.read_namespaced_deployment(deployment, namespace)
        )
        labels = workload.spec.selector.match_labels or {}
        selector = ",".join(f"{k}={v}" for k, v in labels.items())
        pods = self._core.list_namespaced_pod(namespace, label_selector=selector).items
        if not pods:
            raise KubernetesError(f"no pods found for {kind}/{deployment}")

        def badness(pod: Any) -> tuple[int, int]:
            statuses = pod.status.container_statuses or []
            not_ready = sum(1 for s in statuses if not s.ready)
            restarts = sum(s.restart_count or 0 for s in statuses)
            return (not_ready, restarts)

        victim = max(pods, key=badness)
        self._core.delete_namespaced_pod(victim.metadata.name, namespace)
        return {"deleted_pod": victim.metadata.name, "namespace": namespace, "candidates": len(pods)}

    def scale(self, namespace: str, name: str, delta: int, kind: str = "deployment") -> dict[str, Any]:
        self._init()
        if kind == "statefulset":
            scale = self._apps.read_namespaced_stateful_set_scale(name, namespace)
        else:
            scale = self._apps.read_namespaced_deployment_scale(name, namespace)
        current = int(scale.spec.replicas or 0)
        target = max(1, min(MAX_REPLICAS, current + delta))
        body = {"spec": {"replicas": target}}
        if kind == "statefulset":
            self._apps.patch_namespaced_stateful_set_scale(name, namespace, body)
        else:
            self._apps.patch_namespaced_deployment_scale(name, namespace, body)
        return {"from": current, "to": target, "namespace": namespace}

    def rollback(self, namespace: str, name: str) -> dict[str, Any]:
        """Equivalent of `kubectl rollout undo`: re-apply the pod template of the previous ReplicaSet revision."""
        self._init()
        deployment = self._apps.read_namespaced_deployment(name, namespace)
        selector = ",".join(f"{k}={v}" for k, v in (deployment.spec.selector.match_labels or {}).items())
        replica_sets = self._apps.list_namespaced_replica_set(namespace, label_selector=selector).items
        owned = [
            rs for rs in replica_sets
            if any(ref.kind == "Deployment" and ref.name == name for ref in (rs.metadata.owner_references or []))
        ]

        def revision(rs: Any) -> int:
            return int((rs.metadata.annotations or {}).get(REVISION_ANNOTATION, 0))

        owned.sort(key=revision, reverse=True)
        if len(owned) < 2:
            raise KubernetesError(f"deployment {name} has no previous revision to roll back to")
        previous = owned[1]
        template = self._api.sanitize_for_serialization(previous.spec.template)
        (template.get("metadata", {}).get("labels") or {}).pop("pod-template-hash", None)
        self._apps.patch_namespaced_deployment(name, namespace, {"spec": {"template": template}})
        return {"rolled_back_to_revision": revision(previous), "from_revision": revision(owned[0]), "namespace": namespace}

    def trigger_backup(self, namespace: str, cronjob: str) -> dict[str, Any]:
        self._init()
        from kubernetes import client

        cron = self._batch.read_namespaced_cron_job(cronjob, namespace)
        job_name = f"{cronjob}-manual-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"[:63]
        job = client.V1Job(
            metadata=client.V1ObjectMeta(name=job_name, annotations={"cronjob.kubernetes.io/instantiate": "manual"}),
            spec=cron.spec.job_template.spec,
        )
        self._batch.create_namespaced_job(namespace, job)
        return {"job": job_name, "namespace": namespace}
