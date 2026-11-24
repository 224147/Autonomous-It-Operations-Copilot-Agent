"""Root Cause Analysis Agent: ranks probable causes from correlated evidence and scores confidence."""
from __future__ import annotations

import json
import logging
import math
from collections import Counter
from typing import Any

from agents.common.ports import AgentContext
from workflows.state import WorkflowState, event

logger = logging.getLogger(__name__)

TYPE_WEIGHT = {"log": 1.5, "metric": 1.0, "alert": 1.0, "trace": 0.8}
DEPLOYMENT_RELATED = {"memory_exhaustion", "crash_loop", "config_error", "image_pull_failure", "cpu_saturation"}
GENERIC_HINTS = {None, "unknown"}

ROOT_CAUSE_TEXT = {
    "memory_exhaustion": "Memory exhaustion on {svc}: containers are running out of memory and being OOM-killed",
    "cpu_saturation": "CPU saturation on {svc}: sustained CPU usage is throttling request processing",
    "traffic_spike": "Traffic surge on {svc}: request volume exceeded provisioned capacity and saturated CPU",
    "crash_loop": "Crash loop on {svc}: containers repeatedly fail on startup and are restarted",
    "db_connectivity": "Database connectivity problem for {svc}: connection pool/limit exhausted or database unreachable",
    "db_contention": "Database lock contention affecting {svc}: transactions are blocking each other",
    "disk_full": "Disk exhaustion on {svc}: no free space remains on the volume",
    "dependency_failure": "Failure of dependency {svc}: calls to it are failing or timing out and errors propagate to callers",
    "deployment_regression": "Deployment regression on {svc}: a recent release introduced errors or latency",
    "config_error": "Configuration error on {svc}: the service is failing because of invalid or missing configuration",
    "image_pull_failure": "Image pull failure on {svc}: the container image cannot be pulled",
    "certificate_issue": "TLS certificate problem affecting {svc}: certificates are expired or invalid",
    "node_failure": "Node/host failure underlying {svc}: the node is unavailable or evicting workloads",
    "cache_failure": "Cache failure for {svc}: the cache layer is unavailable or misbehaving",
    "unknown": "Unclassified degradation on {svc}: evidence does not match a known failure signature",
}


def confidence_from(scores: Counter[str]) -> tuple[str, float, list[dict[str, Any]]]:
    ranked = scores.most_common()
    if not ranked:
        return "unknown", 0.2, []
    top_cat, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = (top - second) / top if top else 0.0
    confidence = min(0.98, (1 - math.exp(-0.6 * top)) * (0.65 + 0.35 * margin))
    alternatives = [
        {"category": c, "score": round(s, 2), "confidence": round(min(0.98, (1 - math.exp(-0.6 * s)) * 0.5), 2)}
        for c, s in ranked[1:4]
    ]
    return top_cat, round(confidence, 3), alternatives


class RCAAgent:
    name = "rca_agent"

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    def run(self, state: WorkflowState) -> dict[str, Any]:
        detection = state.get("detection") or {}
        corr = state.get("correlation") or {}
        anomalies: list[dict[str, Any]] = detection.get("anomalies", [])
        candidates = corr.get("root_candidates", [])
        monitored = [c for c in candidates if not c.get("unmonitored_suspect")]
        suspects = [c for c in candidates if c.get("unmonitored_suspect")]
        root = monitored[0]["service"] if monitored else (suspects[0]["service"] if suspects else detection.get("primary_service", ""))

        scores = self._score(anomalies, root)
        top_cat = scores.most_common(1)[0][0] if scores else None
        if suspects and top_cat in (None, "unknown", "dependency_failure"):
            root = suspects[0]["service"]
            scores = Counter({"dependency_failure": 2.0 + 0.25 * sum(suspects[0]["evidence"].values())})
        unmonitored = bool(suspects) and root == suspects[0]["service"]

        deployments = [d for d in corr.get("recent_deployments", []) if d["service"] in (root, detection.get("primary_service"))]
        factors: list[str] = []
        evidence = self._evidence(anomalies, root, corr, unmonitored)
        if deployments:
            latest = deployments[-1]
            scores["deployment_regression"] += 2.0
            factors.append(
                f"Deployment of {latest['service']} {latest.get('version') or ''} occurred "
                f"{latest['minutes_before_anomaly']:.0f} min before the first anomaly".replace("  ", " ")
            )
            evidence.append({"type": "deployment", "service": latest["service"], "detail": factors[-1],
                             "timestamp": latest["deployed_at"]})
        surge = self._traffic_surge(state, root)
        if surge:
            scores["traffic_spike"] += 2.5
            factors.append(surge)

        category, confidence, alternatives = confidence_from(scores)
        if category == "cpu_saturation" and surge:
            category = "traffic_spike"
        if category in DEPLOYMENT_RELATED and deployments:
            factors.append("The failure began shortly after a deployment; a regression in the release is likely")
        victims = sorted(
            i["service"] for i in corr.get("service_impact", []) if root in (i.get("affected_dependencies") or [])
        )
        if victims:
            factors.append(f"Failure propagated downstream to: {', '.join(victims)}")

        root_cause = ROOT_CAUSE_TEXT[category].format(svc=root or "the affected service")
        rca = {
            "root_cause": root_cause,
            "category": category,
            "root_service": root,
            "confidence": confidence,
            "contributing_factors": factors,
            "evidence": evidence[:25],
            "alternatives": alternatives,
            "affected_services": detection.get("affected_services", []),
            "propagation_paths": corr.get("propagation_paths", []),
            "deployment_related": bool(deployments),
            "recent_deployments": deployments,
        }
        rca["explanation"] = self._explain(rca)
        return {"rca": rca, "events": [event("rca", f"Root cause: {category} on {root} (confidence {confidence:.0%})")]}

    @staticmethod
    def _score(anomalies: list[dict[str, Any]], root: str) -> Counter[str]:
        scores: Counter[str] = Counter()
        for a in anomalies:
            if a["service"] != root:
                continue
            hint = a.get("category_hint")
            if hint in GENERIC_HINTS:
                scores["unknown"] += 0.2 * a["weight"]
            else:
                scores[hint] += a["weight"] * TYPE_WEIGHT.get(a["type"], 1.0)
        if any(c != "unknown" for c in scores):  # generic symptoms only matter when nothing specific matched
            scores.pop("unknown", None)
        return scores

    @staticmethod
    def _evidence(anomalies: list[dict[str, Any]], root: str, corr: dict[str, Any], unmonitored: bool) -> list[dict[str, Any]]:
        items = []
        for a in anomalies:
            if a["service"] == root or unmonitored:
                item = {"type": a["type"], "service": a["service"], "detail": a["description"], "timestamp": a["first_seen"],
                        "severity": a["severity"]}
                if a.get("sample"):
                    item["sample"] = a["sample"]
                items.append(item)
        for call in corr.get("failing_calls", []):
            if call["callee"] == root:
                items.append({"type": "trace", "service": call["caller"],
                              "detail": f"{call['errors']} failed calls from {call['caller']} to {root}", "timestamp": None})
        return items

    @staticmethod
    def _traffic_surge(state: WorkflowState, root: str) -> str | None:
        rates = [m for m in (state.get("signals") or {}).get("metrics", []) if m["service"] == root and m["name"] == "request_rate"]
        if len(rates) < 6:
            return None
        values = [m["value"] for m in rates]
        half = len(values) // 2
        base = sum(values[:half]) / half
        recent = sum(values[half:]) / (len(values) - half)
        if base > 0 and recent / base >= 2:
            return f"Request rate rose {recent / base:.1f}x versus the preceding baseline"
        return None

    def _explain(self, rca: dict[str, Any]) -> str:
        fallback = self._template_explanation(rca)
        if not self.ctx.llm:
            return fallback
        payload = {k: rca[k] for k in ("root_cause", "category", "root_service", "confidence", "contributing_factors")}
        payload["evidence"] = [e["detail"] for e in rca["evidence"][:12]]
        text = self.ctx.llm.chat(
            "You are an SRE writing a root-cause explanation. Use ONLY the facts provided; do not invent metrics, "
            "services or timings. Write 3-5 concise sentences.",
            json.dumps(payload, default=str),
            max_tokens=400,
        )
        return text.strip() if text else fallback

    @staticmethod
    def _template_explanation(rca: dict[str, Any]) -> str:
        parts = [f"{rca['root_cause']}. Confidence is {rca['confidence']:.0%}."]
        if rca["evidence"]:
            parts.append("Key evidence: " + "; ".join(e["detail"] for e in rca["evidence"][:4]) + ".")
        if rca["contributing_factors"]:
            parts.append("Contributing factors: " + "; ".join(rca["contributing_factors"]) + ".")
        if rca["alternatives"]:
            alt = rca["alternatives"][0]
            parts.append(f"The next most likely alternative is {alt['category']} (score {alt['score']}).")
        return " ".join(parts)
