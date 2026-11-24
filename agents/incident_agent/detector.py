"""Pure anomaly detection over normalized signals (shared by detection and verification)."""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime
from typing import Any

from agents.common.taxonomy import (
    ALERT_HINTS,
    INCIDENT_SCORE_THRESHOLD,
    LOG_PATTERNS,
    METRIC_RULES,
    SEVERITY_WEIGHT,
)
from agents.common.timeutil import parse_ts

# patterns that are significant on a single occurrence; others need repetition to avoid noise
SINGLE_HIT_PATTERNS = {"oom", "crashloop", "disk", "image_pull", "node", "tls"}
GENERIC_ERROR_LOG_MIN = 5


def _anomaly(kind: str, service: str, name: str, severity: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"{kind}:{service}:{name}",
        "type": kind,
        "service": service,
        "name": name,
        "severity": severity,
        "weight": SEVERITY_WEIGHT[severity],
        "category_hint": None,
        **extra,
    }


def _after(items: list[dict[str, Any]], since: datetime | None) -> list[dict[str, Any]]:
    if since is None:
        return items
    return [i for i in items if parse_ts(i["timestamp"]) >= since]


def _breach_fn(above: bool) -> Callable[[float, float], bool]:
    return (lambda v, limit: v >= limit) if above else (lambda v, limit: v <= limit)


def detect_metric_anomalies(metrics: list[dict[str, Any]], since: datetime | None, current_only: bool) -> list[dict[str, Any]]:
    series: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for m in _after(metrics, since):
        series[(m["service"], m["name"])].append(m)
    out = []
    for (service, name), samples in series.items():
        samples.sort(key=lambda s: s["timestamp"])
        rule = METRIC_RULES.get(name)
        if not rule:
            continue
        above = rule.direction == "above"
        breaches = _breach_fn(above)

        values = [s["value"] for s in samples]
        breached = [s for s in samples if breaches(s["value"], rule.warning)]
        latest_breach = breaches(values[-1], rule.warning)
        sustained = latest_breach if current_only else (latest_breach or len(breached) >= 2)
        if sustained and breached:
            peak = max(values) if above else min(values)
            severity = "critical" if breaches(peak, rule.critical) else "warning"
            threshold = rule.critical if severity == "critical" else rule.warning
            out.append(_anomaly(
                "metric", service, name, severity, value=round(peak, 2), latest=round(values[-1], 2),
                threshold=threshold, first_seen=breached[0]["timestamp"], last_seen=breached[-1]["timestamp"],
                count=len(breached), category_hint=rule.category_hint,
                description=f"{name} reached {peak:.1f}{rule.unit} (threshold {rule.warning}{rule.unit})",
            ))
            continue
        if not current_only and len(values) >= 12 and above:  # statistical anomaly vs. baseline
            split = int(len(values) * 0.6)
            base = values[:split]
            mean, stdev = statistics.fmean(base), statistics.pstdev(base)
            if stdev > 0 and values[-1] > mean * 1.5 and (values[-1] - mean) / stdev >= 4:
                out.append(_anomaly(
                    "metric", service, name, "warning", value=round(values[-1], 2), latest=round(values[-1], 2),
                    threshold=round(mean + 4 * stdev, 2), first_seen=samples[split]["timestamp"],
                    last_seen=samples[-1]["timestamp"], count=len(values) - split, category_hint=rule.category_hint,
                    description=f"{name} deviates from baseline ({mean:.1f} -> {values[-1]:.1f})",
                ))
    return out


def detect_log_anomalies(logs: list[dict[str, Any]], since: datetime | None) -> list[dict[str, Any]]:
    hits: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    generic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for log in _after(logs, since):
        matched = False
        for pattern in LOG_PATTERNS:
            if pattern.regex.search(log["message"]):
                hits[(log["service"], pattern.name)].append(log)
                matched = True
        if not matched and log["severity"] in ("error", "critical"):
            generic[log["service"]].append(log)
    by_name = {p.name: p for p in LOG_PATTERNS}
    out = []
    for (service, pname), items in hits.items():
        pattern = by_name[pname]
        if len(items) < (1 if pname in SINGLE_HIT_PATTERNS else 2):
            continue
        out.append(_anomaly(
            "log", service, pname, pattern.severity, first_seen=items[0]["timestamp"],
            last_seen=items[-1]["timestamp"], count=len(items), category_hint=pattern.category,
            sample=items[-1]["message"][:300], description=f"{pattern.description} ({len(items)} log lines)",
        ))
    for service, items in generic.items():
        if len(items) >= GENERIC_ERROR_LOG_MIN:
            out.append(_anomaly(
                "log", service, "error_logs", "error" if len(items) >= 15 else "warning",
                first_seen=items[0]["timestamp"], last_seen=items[-1]["timestamp"], count=len(items),
                sample=items[-1]["message"][:300], description=f"{len(items)} error-level log lines",
            ))
    return out


def detect_trace_anomalies(traces: list[dict[str, Any]], since: datetime | None) -> list[dict[str, Any]]:
    by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for span in _after(traces, since):
        by_service[span["service"]].append(span)
    out = []
    for service, spans in by_service.items():
        errors = [s for s in spans if s["status"] == "error"]
        if len(spans) >= 3 and len(errors) >= 3 and len(errors) / len(spans) >= 0.2:
            peers = Counter(s["peer_service"] for s in errors if s["peer_service"] and s["peer_service"] != service)
            out.append(_anomaly(
                "trace", service, "span_errors", "error", first_seen=errors[0]["timestamp"],
                last_seen=errors[-1]["timestamp"], count=len(errors), peers=dict(peers),
                category_hint="dependency_failure" if peers else None,
                description=f"{len(errors)}/{len(spans)} spans failed" + (f"; failing calls to {', '.join(peers)}" if peers else ""),
            ))
        durations = sorted(s["duration_ms"] for s in spans)
        if len(durations) >= 5 and durations[int(0.95 * (len(durations) - 1))] >= 3000:
            out.append(_anomaly(
                "trace", service, "slow_spans", "warning", first_seen=spans[0]["timestamp"],
                last_seen=spans[-1]["timestamp"], count=len(durations), value=durations[int(0.95 * (len(durations) - 1))],
                description="p95 span duration above 3s",
            ))
    return out


def detect_alert_anomalies(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for alert in alerts:
        if alert.get("status") == "resolved" or not alert["service"]:
            continue
        key = (alert["service"], alert["name"])
        if key in seen:
            continue
        hint = next((c for rx, c in ALERT_HINTS if rx.search(alert["name"])), None)
        seen[key] = _anomaly(
            "alert", alert["service"], alert["name"], alert["severity"], first_seen=alert["timestamp"],
            last_seen=alert["timestamp"], count=1, category_hint=hint, source=alert.get("source", ""),
            description=alert.get("summary") or f"Alert {alert['name']} firing",
        )
    return list(seen.values())


def detect(signals: dict[str, list[dict[str, Any]]], since: datetime | None = None, current_only: bool = False) -> dict[str, Any]:
    anomalies = (
        detect_metric_anomalies(signals.get("metrics", []), since, current_only)
        + detect_log_anomalies(signals.get("logs", []), since)
        + detect_trace_anomalies(signals.get("traces", []), since)
        + ([] if current_only else detect_alert_anomalies(signals.get("alerts", [])))
    )
    anomalies.sort(key=lambda a: (a["first_seen"], -a["weight"]))
    score = float(sum(a["weight"] for a in anomalies))
    per_service: Counter[str] = Counter()
    for a in anomalies:
        per_service[a["service"]] += a["weight"]
    return {
        "anomalies": anomalies,
        "score": score,
        "is_incident": score >= INCIDENT_SCORE_THRESHOLD,
        "service_scores": dict(per_service),
    }
