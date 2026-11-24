"""Synthetic labelled incident scenarios used for evaluation, tests and the demo simulator."""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

from agents.common.timeutil import iso, utcnow

DEMO_SERVICES: list[dict[str, Any]] = [
    {"name": "api-gateway", "tier": 1, "team": "platform", "kind": "deployment", "namespace": "production",
     "deployment": "api-gateway", "depends_on": ["orders-service"], "cache_prefix": "", "description": "Public API gateway"},
    {"name": "orders-service", "tier": 1, "team": "commerce", "kind": "deployment", "namespace": "production",
     "deployment": "orders-service", "depends_on": ["payment-service", "postgres", "redis"], "cache_prefix": "orders:",
     "description": "Order management"},
    {"name": "payment-service", "tier": 1, "team": "payments", "kind": "deployment", "namespace": "production",
     "deployment": "payment-service", "depends_on": ["postgres", "redis"], "cache_prefix": "payments:",
     "description": "Payment processing"},
    {"name": "inventory-service", "tier": 3, "team": "commerce", "kind": "deployment", "namespace": "production",
     "deployment": "inventory-service", "depends_on": ["postgres"], "cache_prefix": "", "description": "Stock levels"},
    {"name": "notification-service", "tier": 4, "team": "engagement", "kind": "deployment", "namespace": "production",
     "deployment": "notification-service", "depends_on": [], "cache_prefix": "", "description": "Email/SMS notifications"},
    {"name": "postgres", "tier": 1, "team": "dba", "kind": "statefulset", "namespace": "production",
     "deployment": "postgres", "depends_on": [], "backup_cronjob": "postgres-backup", "description": "Primary database"},
    {"name": "redis", "tier": 2, "team": "platform", "kind": "statefulset", "namespace": "production",
     "deployment": "redis", "depends_on": [], "description": "Cache"},
    {"name": "legacy-batch", "tier": 3, "team": "data", "kind": "vm", "namespace": "", "deployment": "",
     "vm_instance_id": "i-0abc123demo", "depends_on": ["postgres"], "description": "Batch VM"},
]

NORMAL = {
    "cpu_usage_percent": 35.0, "memory_usage_percent": 55.0, "error_rate_percent": 0.4, "latency_p99_ms": 250.0,
    "restart_count": 0.0, "disk_usage_percent": 60.0, "db_connection_utilization_percent": 40.0, "request_rate": 120.0,
}
POINTS = 24
STEP_SECONDS = 30


def metric_series(service: str, now: datetime, rng: random.Random,
                  ramps: dict[str, tuple[int, float]] | None = None) -> list[dict[str, Any]]:
    """Normal metrics with optional ramps: {metric: (start_index, final_value)}."""
    out = []
    ramps = ramps or {}
    for name, base in NORMAL.items():
        for i in range(POINTS):
            ts = now - timedelta(seconds=(POINTS - 1 - i) * STEP_SECONDS)
            value = base * (1 + rng.uniform(-0.04, 0.04))
            if name in ramps:
                start, final = ramps[name]
                if i >= start:
                    frac = (i - start + 1) / (POINTS - start)
                    value = base + (final - base) * frac
            out.append({"service": service, "name": name, "value": round(value, 2), "timestamp": iso(ts)})
    return out


def logs_at(service: str, now: datetime, messages: list[str], level: str = "error",
            start_minutes_ago: float = 6.0) -> list[dict[str, Any]]:
    gap = (start_minutes_ago * 60) / max(len(messages), 1)
    return [
        {"service": service, "message": m, "severity": level,
         "timestamp": iso(now - timedelta(seconds=start_minutes_ago * 60 - i * gap))}
        for i, m in enumerate(messages)
    ]


def spans(service: str, now: datetime, total: int, errors: int, peer: str = "", duration: float = 120.0) -> list[dict[str, Any]]:
    out = []
    for i in range(total):
        failed = i < errors
        out.append({
            "service": service, "trace_id": f"t{service}{i}", "span_id": f"s{i}", "name": f"call {peer or 'handler'}",
            "duration_ms": duration * (6 if failed else 1), "status": "error" if failed else "ok",
            "peer_service": peer if failed else "", "timestamp": iso(now - timedelta(seconds=(total - i) * 10)),
        })
    return out


def _deployment(service: str, now: datetime, minutes_ago: float, version: str = "v2.4.0") -> dict[str, Any]:
    return {"service": service, "version": version, "status": "succeeded",
            "deployed_at": iso(now - timedelta(minutes=minutes_ago)), "environment": "production", "source": "scenario"}


def build_scenario(kind: str, now: datetime | None = None, seed: int = 7) -> dict[str, Any]:
    """Return {name, service, expected_incident, expected_category, signals, alerts, deployments}."""
    now = now or utcnow()
    rng = random.Random(f"{kind}-{seed}")
    svc = "payment-service"
    metrics: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    deployments: list[dict[str, Any]] = []
    expected_incident, category = True, kind

    def base(ramps: dict[str, tuple[int, float]] | None = None, target: str | None = None) -> None:
        metrics.extend(metric_series(target or svc, now, rng, ramps))

    if kind == "memory_exhaustion":
        base({"memory_usage_percent": (14, 98.0), "restart_count": (16, 5.0), "latency_p99_ms": (16, 3200.0)})
        logs += logs_at(svc, now, ["Container killed: OOMKilled (exit code 137)",
                                   "java.lang.OutOfMemoryError: Java heap space", "Back-off restarting failed container"])
        deployments.append(_deployment(svc, now, 25))
    elif kind == "cpu_saturation":
        base({"cpu_usage_percent": (12, 99.0), "latency_p99_ms": (14, 4000.0), "error_rate_percent": (16, 8.0)})
        alerts.append({"service": svc, "name": "HighCPUUsage", "severity": "critical", "summary": "CPU above 95%", "source": "prometheus"})
    elif kind == "traffic_spike":
        base({"cpu_usage_percent": (12, 99.0), "request_rate": (12, 900.0), "latency_p99_ms": (14, 4200.0)})
        alerts.append({"service": svc, "name": "HighCPUUsage", "severity": "critical", "summary": "CPU above 95%", "source": "prometheus"})
    elif kind == "crash_loop":
        base({"restart_count": (14, 9.0), "error_rate_percent": (14, 35.0)})
        logs += logs_at(svc, now, ["Back-off restarting failed container", "CrashLoopBackOff: container exited with code 1"])
        deployments.append(_deployment(svc, now, 8, "v3.0.1"))
    elif kind == "db_connectivity":
        svc = "orders-service"
        base({"error_rate_percent": (12, 30.0), "latency_p99_ms": (12, 6000.0), "db_connection_utilization_percent": (10, 100.0)})
        logs += logs_at(svc, now, ["FATAL: too many connections for role orders",
                                   "timeout acquiring connection from pool", "connection pool exhausted"])
    elif kind == "disk_full":
        svc = "postgres"
        base({"disk_usage_percent": (10, 99.5)})
        logs += logs_at(svc, now, ["PANIC: could not write to file: No space left on device"], level="critical")
    elif kind == "dependency_failure":
        svc = "orders-service"
        base({"error_rate_percent": (12, 25.0), "latency_p99_ms": (12, 5500.0)})
        traces += spans(svc, now, 20, 14, peer="fraud-check-api")
        logs += logs_at(svc, now, ["upstream request timeout calling fraud-check-api",
                                   "dial tcp: connection refused fraud-check-api",
                                   "upstream request timeout calling fraud-check-api"])
    elif kind == "cache_failure":
        svc = "orders-service"
        base({"error_rate_percent": (12, 15.0), "latency_p99_ms": (12, 3000.0)})
        logs += logs_at(svc, now, ["redis connection refused", "redis connection reset by peer",
                                   "cache unavailable, falling back to db"])
    elif kind == "deployment_regression":
        base({"error_rate_percent": (14, 28.0), "latency_p99_ms": (14, 4500.0)})
        deployments.append(_deployment(svc, now, 10, "v5.1.0"))
    elif kind == "config_error":
        svc = "inventory-service"
        base({"error_rate_percent": (14, 40.0)})
        logs += logs_at(svc, now, ["invalid configuration: missing required config value INVENTORY_DB_HOST",
                                   "configuration error: unable to parse settings.yaml", "KeyError: 'warehouse_id'"])
        deployments.append(_deployment(svc, now, 6, "v1.9.0"))
    elif kind == "node_failure":
        svc = "legacy-batch"
        base({"error_rate_percent": (14, 22.0)})
        logs += logs_at(svc, now, ["Node NotReady: kubelet stopped posting node status", "Pod evicted due to node failure"])
    elif kind == "certificate_issue":
        svc = "api-gateway"
        base({"error_rate_percent": (12, 45.0)})
        logs += logs_at(svc, now, ["tls handshake error: x509: certificate has expired",
                                   "x509: certificate has expired or is not yet valid"])
    elif kind == "healthy":
        base()
        expected_incident, category = False, "none"
    elif kind == "noisy_healthy":
        base({"cpu_usage_percent": (20, 88.0)})  # a single warning-level breach with no other evidence
        logs += logs_at(svc, now, ["slow query detected"], level="warning")
        expected_incident, category = False, "none"
    else:
        raise ValueError(f"unknown scenario {kind}")

    return {"name": kind, "service": svc, "expected_incident": expected_incident, "expected_category": category,
            "signals": {"metrics": metrics, "logs": logs, "traces": traces}, "alerts": alerts, "deployments": deployments}


INCIDENT_SCENARIOS = [
    "memory_exhaustion", "cpu_saturation", "traffic_spike", "crash_loop", "db_connectivity", "disk_full",
    "dependency_failure", "cache_failure", "deployment_regression", "config_error", "node_failure", "certificate_issue",
]
NEGATIVE_SCENARIOS = ["healthy", "noisy_healthy"]
