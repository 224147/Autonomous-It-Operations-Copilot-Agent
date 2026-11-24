"""Shared taxonomy: incident categories, remediation actions, risk levels, log patterns and detection rules."""
from __future__ import annotations

import re
from dataclasses import dataclass

CATEGORIES = (
    "memory_exhaustion",
    "cpu_saturation",
    "crash_loop",
    "db_connectivity",
    "db_contention",
    "disk_full",
    "dependency_failure",
    "deployment_regression",
    "config_error",
    "image_pull_failure",
    "certificate_issue",
    "node_failure",
    "cache_failure",
    "traffic_spike",
    "unknown",
)

SEVERITY_WEIGHT = {"info": 0, "warning": 1, "error": 2, "critical": 3}

# --- remediation actions ----------------------------------------------------------------------
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

ACTIONS: dict[str, dict[str, str]] = {
    "restart_service": {"risk": "medium", "description": "Rolling restart of the service deployment"},
    "restart_pod": {"risk": "low", "description": "Restart the most unhealthy pod of the service"},
    "rollback_deployment": {"risk": "medium", "description": "Roll back the deployment to the previous revision"},
    "scale_deployment": {"risk": "low", "description": "Scale the deployment out"},
    "restart_vm": {"risk": "high", "description": "Restart the virtual machine hosting the service"},
    "clear_cache": {"risk": "low", "description": "Clear the service cache namespace"},
    "restart_database": {"risk": "high", "description": "Rolling restart of the database workload"},
    "trigger_backup": {"risk": "low", "description": "Trigger the service backup job"},
    "execute_runbook": {"risk": "medium", "description": "Execute the steps of a registered runbook"},
    "open_ticket": {"risk": "low", "description": "Open an ITSM ticket for the incident"},
}

RECOVERY_ACTIONS = tuple(a for a in ACTIONS if a != "open_ticket")


def max_risk(risks: list[str]) -> str:
    return max(risks, key=lambda r: RISK_ORDER.get(r, 1), default="low")


# --- log patterns -------------------------------------------------------------------------------
@dataclass(frozen=True)
class LogPattern:
    name: str
    regex: re.Pattern[str]
    category: str
    severity: str
    description: str


def _p(name: str, pattern: str, category: str, severity: str, description: str) -> LogPattern:
    return LogPattern(name, re.compile(pattern, re.I), category, severity, description)


LOG_PATTERNS: tuple[LogPattern, ...] = (
    _p("oom", r"oomkilled|out of memory|outofmemoryerror|cannot allocate memory", "memory_exhaustion", "error", "Out-of-memory kills"),
    _p("crashloop", r"crashloopbackoff|back-off restarting failed container", "crash_loop", "error", "Container crash loop"),
    _p("image_pull", r"imagepullbackoff|errimagepull|failed to pull image", "image_pull_failure", "error", "Image pull failures"),
    _p("db_conn", r"too many connections|connection pool (is )?exhausted|timeout acquiring connection|could not connect to (server|database)|remaining connection slots", "db_connectivity", "error", "Database connection failures"),
    _p("db_lock", r"deadlock detected|lock wait timeout|could not obtain lock", "db_contention", "error", "Database lock contention"),
    _p("disk", r"no space left on device|disk (is )?full|disk quota exceeded", "disk_full", "critical", "Disk exhaustion"),
    _p("tls", r"x509|certificate (has )?(expired|is not valid)|tls handshake (error|failure)|ssl: certificate", "certificate_issue", "error", "TLS certificate problems"),
    _p("node", r"node ?not ?ready|node .* (unreachable|lost)|evicted|kubelet stopped posting", "node_failure", "error", "Node failure / eviction"),
    _p("cache", r"redis.*(connection (refused|reset|timed out)|unavailable)|cache (miss storm|unavailable|corrupt)|loading dataset in memory|READONLY", "cache_failure", "error", "Cache failures"),
    _p("config", r"invalid configuration|missing (required )?(env|environment|config)|configuration error|keyerror:|no such (config|file or directory).*\.(ya?ml|json|conf)", "config_error", "error", "Configuration errors"),
    _p("upstream_timeout", r"upstream request timeout|deadline exceeded|gateway timeout|connection refused|read timed out|dial tcp.*(refused|timeout)", "dependency_failure", "error", "Upstream/dependency call failures"),
)

# alert-name keyword -> category hint
ALERT_HINTS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.I), c)
    for p, c in (
        (r"oom|memory", "memory_exhaustion"),
        (r"crashloop|restart", "crash_loop"),
        (r"cpu|throttl", "cpu_saturation"),
        (r"disk|filesystem|volume", "disk_full"),
        (r"connection|database|postgres|mysql", "db_connectivity"),
        (r"certificate|cert|tls", "certificate_issue"),
        (r"node(down|notready)|instancedown|hostdown", "node_failure"),
        (r"imagepull", "image_pull_failure"),
        (r"redis|cache", "cache_failure"),
    )
)


# --- metric detection rules ---------------------------------------------------------------------
@dataclass(frozen=True)
class MetricRule:
    name: str
    warning: float
    critical: float
    category_hint: str | None
    direction: str = "above"  # above | below
    unit: str = ""


METRIC_RULES: dict[str, MetricRule] = {
    r.name: r
    for r in (
        MetricRule("cpu_usage_percent", 85, 95, "cpu_saturation", unit="%"),
        MetricRule("memory_usage_percent", 90, 97, "memory_exhaustion", unit="%"),
        MetricRule("error_rate_percent", 5, 20, None, unit="%"),
        MetricRule("latency_p99_ms", 2000, 5000, None, unit="ms"),
        MetricRule("restart_count", 3, 6, "crash_loop"),
        MetricRule("disk_usage_percent", 90, 97, "disk_full", unit="%"),
        MetricRule("db_connection_utilization_percent", 90, 98, "db_connectivity", unit="%"),
        MetricRule("availability_percent", 99, 95, None, direction="below", unit="%"),
    )
}

METRIC_ALIASES = {
    "cpu": "cpu_usage_percent",
    "cpu_percent": "cpu_usage_percent",
    "cpu_usage": "cpu_usage_percent",
    "memory": "memory_usage_percent",
    "memory_percent": "memory_usage_percent",
    "mem_usage_percent": "memory_usage_percent",
    "error_rate": "error_rate_percent",
    "errors_percent": "error_rate_percent",
    "latency_p99": "latency_p99_ms",
    "p99_latency_ms": "latency_p99_ms",
    "restarts": "restart_count",
    "pod_restarts": "restart_count",
    "disk": "disk_usage_percent",
    "disk_percent": "disk_usage_percent",
    "db_connections_percent": "db_connection_utilization_percent",
    "availability": "availability_percent",
}

INCIDENT_SCORE_THRESHOLD = 3.0
