"""Monitoring integrations (alert/log/metric/trace parsers and connectors) and the RAG knowledge base."""
from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest

from agents.common.timeutil import utcnow
from monitoring.alert_ingestion.parsers import parse_alertmanager, parse_azure_monitor, parse_datadog, resolve_service
from monitoring.log_ingestion.loki import LokiConnector, parse_loki_push
from monitoring.metric_ingestion.prometheus import PrometheusConnector, parse_otlp_metrics
from monitoring.trace_ingestion.otlp import parse_otlp_traces
from rag.embeddings import HashingEmbedder, tokenize
from rag.store import KnowledgeBase, chunk_text


def test_alertmanager_parser_resolves_service_and_state():
    payload = {"alerts": [
        {"status": "firing", "labels": {"alertname": "HighMem", "severity": "critical", "pod": "payment-service-7d9f8-xk2lp"},
         "annotations": {"summary": "mem high"}, "startsAt": "2026-01-01T10:00:00Z", "fingerprint": "f1"},
        {"status": "resolved", "labels": {"alertname": "X", "app": "Orders"}, "endsAt": "2026-01-01T10:05:00Z"},
    ]}
    firing, resolved = parse_alertmanager(payload)
    assert firing["service"] == "payment-service" and firing["status"] == "firing" and firing["fingerprint"] == "f1"
    assert resolved["service"] == "orders" and resolved["status"] == "resolved" and resolved["ends_at"] is not None
    assert resolve_service({}) == ""


def test_azure_monitor_and_datadog_parsers():
    azure = parse_azure_monitor({"data": {"essentials": {
        "alertRule": "CPU>90", "severity": "Sev1", "monitorCondition": "Fired", "alertId": "/subs/1/alerts/9",
        "alertTargetIDs": ["/subscriptions/1/resourcegroups/rg/providers/microsoft.compute/virtualmachines/legacy-batch"],
        "firedDateTime": "2026-01-01T10:00:00Z"}}})[0]
    assert azure["service"] == "legacy-batch" and azure["severity"] == "critical" and azure["status"] == "firing"
    dd = parse_datadog({"title": "Latency high", "alert_type": "error", "tags": ["service:orders-service", "env:prod"],
                        "alert_transition": "Triggered", "id": "77"})[0]
    assert dd["service"] == "orders-service" and dd["severity"] == "critical" and dd["fingerprint"] == "77"
    assert parse_datadog({"title": "ok", "alert_type": "success", "tags": "service:a"})[0]["status"] == "resolved"
    assert parse_azure_monitor({}) == []


def test_loki_push_otlp_trace_and_metric_parsers():
    ns = str(int(utcnow().timestamp() * 1e9))
    logs = parse_loki_push({"streams": [{"stream": {"app": "orders-service", "level": "error"}, "values": [[ns, "boom"]]},
                                        {"stream": {}, "values": [[ns, "no service -> dropped"]]}]})
    assert len(logs) == 1 and logs[0]["service"] == "orders-service" and logs[0]["severity"] == "error"

    traces = parse_otlp_traces({"resourceSpans": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "Orders"}}]},
        "scopeSpans": [{"spans": [{"traceId": "t", "spanId": "s", "name": "GET", "startTimeUnixNano": "1700000000000000000",
                                   "endTimeUnixNano": "1700000000250000000", "status": {"code": 2},
                                   "attributes": [{"key": "peer.service", "value": {"stringValue": "Payments"}}]}]}]}]})
    assert traces[0]["service"] == "orders" and traces[0]["status"] == "error"
    assert traces[0]["duration_ms"] == pytest.approx(250.0) and traces[0]["peer_service"] == "payments"

    metrics = parse_otlp_metrics({"resourceMetrics": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "orders"}}]},
        "scopeMetrics": [{"metrics": [{"name": "cpu", "gauge": {"dataPoints": [{"asDouble": 91.5, "timeUnixNano": ns}]}}]}]}]})
    assert metrics[0]["name"] == "cpu" and metrics[0]["value"] == 91.5


def test_prometheus_connector_queries_per_service_and_filters_bad_values():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["query"])
        values = [[1700000000, "42.5"], [1700000030, "NaN"], [1700000060, "+Inf"], [1700000090, "bad"]]
        return httpx.Response(200, json={"data": {"result": [{"values": values}]}})

    connector = PrometheusConnector("http://prom:9090", {"cpu_usage_percent": 'x{namespace="$namespace",pod=~"$deployment-.*"}'},
                                    transport=httpx.MockTransport(handler))
    now = utcnow()
    out = connector.fetch({"name": "Payment-Service", "namespace": "production", "deployment": "payment-service"},
                          now - timedelta(minutes=5), now)
    assert seen == ['x{namespace="production",pod=~"payment-service-.*"}']
    assert [m["value"] for m in out["metrics"]] == [42.5] and out["metrics"][0]["service"] == "payment-service"
    with pytest.raises(ValueError):  # label injection attempt
        connector.fetch({"name": 'a"} or vector(1) #', "namespace": "x"}, now, now)


def test_loki_connector_builds_safe_query_and_parses_streams():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.url.params)
        return httpx.Response(200, json={"data": {"result": [{"stream": {"pod": "a-1"}, "values": [["1700000000000000000", "OOMKilled"]]}]}})

    now = utcnow()
    out = LokiConnector("http://loki:3100", transport=httpx.MockTransport(handler)).fetch(
        {"name": "orders-service", "namespace": "production", "deployment": "orders-service"}, now - timedelta(minutes=1), now)
    assert 'namespace="production"' in captured["query"] and out["logs"][0]["message"] == "OOMKilled"


# ------------------------------------------------------------------ RAG
@pytest.fixture
def kb(tmp_path):
    base = KnowledgeBase(HashingEmbedder(), persist_dir=str(tmp_path / "chroma"))
    base.upsert("rb-1", "Runbook: pods are OOMKilled. Restart the pod and scale out.", "runbook", {"category": "memory_exhaustion", "service": "pay"})
    base.upsert("rb-2", "Runbook: database connection pool exhausted. Restart the service.", "runbook", {"category": "db_connectivity"})
    base.upsert("pm-1", "Postmortem: CrashLoopBackOff after bad release; rolled back.", "postmortem", {"category": "crash_loop"})
    return base


def test_tokenizer_splits_compound_identifiers():
    tokens = tokenize("CrashLoopBackOff in crash-loop state")
    assert {"crashloopbackoff", "crash", "loop", "back", "off", "crash-loop"} <= set(tokens)


def test_hybrid_semantic_keyword_and_filters(kb):
    assert kb.search("OOMKilled pods", k=1)[0].parent_id == "rb-1"
    assert kb.search("crash loop", k=1, mode="keyword")[0].parent_id == "pm-1"
    only_runbooks = kb.search("restart", k=5, doc_types=["runbook"])
    assert {h.parent_id for h in only_runbooks} == {"rb-1", "rb-2"}
    assert [h.parent_id for h in kb.search("restart", k=5, filters={"category": "db_connectivity"})] == ["rb-2"]
    hit = kb.search("pool exhausted", k=1, mode="semantic")[0]
    assert hit.semantic_score is not None and hit.metadata["doc_type"] == "runbook"
    assert kb.search("", k=3) == [] and kb.search("zzzz-nothing", k=3, mode="keyword") == []


def test_upsert_replaces_and_delete_removes_and_long_docs_chunk(kb):
    kb.upsert("rb-1", "Totally new content about kafka lag.", "runbook", {"category": "x"})
    assert kb.get("rb-1")["text"].startswith("Totally new") and kb.get("rb-1")["metadata"]["category"] == "x"
    assert kb.search("OOMKilled", k=3, mode="keyword", doc_types=["runbook"]) == []
    kb.delete("rb-1")
    assert kb.get("rb-1") is None
    long_text = "\n\n".join(f"Paragraph {i} " + "word " * 100 for i in range(20))
    assert len(chunk_text(long_text)) > 3
    assert kb.upsert("big", long_text, "sop") > 3 and kb.get("big")["text"].count("Paragraph") == 20
    with pytest.raises(ValueError):
        kb.upsert("empty", "   ", "sop")


def test_metadata_is_flattened_for_chroma(kb):
    kb.upsert("m", "x y z", "sop", {"tags": ["a", "b"], "n": 3, "none": None, "obj": {"k": 1}})
    meta = kb.get("m")["metadata"]
    assert meta["tags"] == "a,b" and meta["n"] == 3 and "none" not in meta and isinstance(meta["obj"], str)
    assert json.dumps(meta)
