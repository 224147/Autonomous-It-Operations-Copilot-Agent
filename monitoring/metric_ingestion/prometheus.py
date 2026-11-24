"""Metric ingestion: Prometheus pull connector and OTLP/JSON metric parser."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from agents.common.timeutil import iso, parse_ts

logger = logging.getLogger(__name__)

SAFE = re.compile(r"^[a-zA-Z0-9_.\-]*$")
SEL = 'namespace="$namespace",pod=~"$deployment-.*"'
DEFAULT_QUERIES: dict[str, str] = {
    "cpu_usage_percent": (
        f'100 * sum(rate(container_cpu_usage_seconds_total{{{SEL},container!=""}}[2m])) '
        f'/ sum(kube_pod_container_resource_limits{{{SEL},resource="cpu"}})'
    ),
    "memory_usage_percent": (
        f'100 * sum(container_memory_working_set_bytes{{{SEL},container!=""}}) '
        f'/ sum(kube_pod_container_resource_limits{{{SEL},resource="memory"}})'
    ),
    "restart_count": f"sum(increase(kube_pod_container_status_restarts_total{{{SEL}}}[10m]))",
    "error_rate_percent": (
        '100 * sum(rate(http_requests_total{namespace="$namespace",service="$service",code=~"5.."}[2m])) '
        '/ sum(rate(http_requests_total{namespace="$namespace",service="$service"}[2m]))'
    ),
    "latency_p99_ms": (
        '1000 * histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket'
        '{namespace="$namespace",service="$service"}[2m])))'
    ),
    "request_rate": 'sum(rate(http_requests_total{namespace="$namespace",service="$service"}[2m]))',
}


def load_queries(path: str = "") -> dict[str, str]:
    if path and Path(path).is_file():
        return json.loads(Path(path).read_text())
    return dict(DEFAULT_QUERIES)


class PrometheusConnector:
    name = "prometheus"

    def __init__(self, url: str, queries: dict[str, str] | None = None, step_seconds: int = 30,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._url = url.rstrip("/")
        self._queries = queries or dict(DEFAULT_QUERIES)
        self._step = step_seconds
        self._transport = transport

    def fetch(self, service: dict[str, Any], since: datetime, until: datetime) -> dict[str, list[dict[str, Any]]]:
        name = str(service.get("name", ""))
        values = {"namespace": service.get("namespace") or "default", "deployment": service.get("deployment") or name,
                  "service": name}
        if not all(SAFE.match(str(v)) for v in values.values()):
            raise ValueError("unsafe characters in service metadata")
        metrics: list[dict[str, Any]] = []
        with httpx.Client(timeout=15.0, transport=self._transport) as client:
            for metric, template in self._queries.items():
                query = template
                for key, val in values.items():
                    query = query.replace(f"${key}", str(val))
                try:
                    resp = client.get(f"{self._url}/api/v1/query_range", params={
                        "query": query, "start": since.timestamp(), "end": until.timestamp(), "step": self._step})
                    resp.raise_for_status()
                    body = resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    logger.warning("Prometheus query %s failed for %s: %s", metric, name, exc)
                    continue
                for series in body.get("data", {}).get("result", []):
                    for ts, val in series.get("values", []):
                        try:
                            number = float(val)
                        except ValueError:
                            continue
                        if number == number and abs(number) != float("inf"):
                            metrics.append({"service": name.lower(), "name": metric, "value": number,
                                            "timestamp": iso(parse_ts(float(ts)))})
        return {"logs": [], "metrics": metrics, "traces": []}


def otlp_attrs(items: list[dict[str, Any]] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items or []:
        value = item.get("value", {})
        out[item.get("key", "")] = next(iter(value.values()), None) if value else None
    return out


def parse_otlp_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """OTLP/HTTP JSON metrics (gauge and sum data points)."""
    out = []
    for rm in payload.get("resourceMetrics", []):
        service = str(otlp_attrs(rm.get("resource", {}).get("attributes")).get("service.name") or "").lower()
        for sm in rm.get("scopeMetrics", []):
            for metric in sm.get("metrics", []):
                points = (metric.get("gauge") or metric.get("sum") or {}).get("dataPoints", [])
                for dp in points:
                    value = dp.get("asDouble", dp.get("asInt"))
                    if value is None or not service:
                        continue
                    out.append({"service": service, "name": metric.get("name", ""), "value": float(value),
                                "timestamp": iso(parse_ts(dp.get("timeUnixNano"))), "labels": otlp_attrs(dp.get("attributes"))})
    return out
