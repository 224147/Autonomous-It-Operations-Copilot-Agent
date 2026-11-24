"""Trace ingestion: OTLP/HTTP JSON trace parser."""
from __future__ import annotations

from typing import Any

from agents.common.timeutil import iso, parse_ts
from monitoring.metric_ingestion.prometheus import otlp_attrs


def parse_otlp_traces(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for rs in payload.get("resourceSpans", []):
        service = str(otlp_attrs(rs.get("resource", {}).get("attributes")).get("service.name") or "").lower()
        if not service:
            continue
        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                start = int(span.get("startTimeUnixNano") or 0)
                end = int(span.get("endTimeUnixNano") or start)
                attrs = otlp_attrs(span.get("attributes"))
                peer = attrs.get("peer.service") or attrs.get("server.address") or attrs.get("net.peer.name") or ""
                out.append({
                    "service": service, "trace_id": span.get("traceId", ""), "span_id": span.get("spanId", ""),
                    "parent_span_id": span.get("parentSpanId", ""), "name": span.get("name", ""),
                    "duration_ms": max(end - start, 0) / 1e6,
                    "status": "error" if (span.get("status") or {}).get("code") in (2, "2", "STATUS_CODE_ERROR") else "ok",
                    "peer_service": str(peer).lower(), "timestamp": iso(parse_ts(start or None)),
                    "attributes": {k: v for k, v in attrs.items() if isinstance(v, (str, int, float, bool))},
                })
    return out
