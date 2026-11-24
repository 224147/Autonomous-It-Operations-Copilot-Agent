"""Incident Detection Agent and Severity Classification Agent."""
from __future__ import annotations

from collections import Counter
from typing import Any

from agents.common.ports import AgentContext
from agents.incident_agent.detector import detect
from workflows.state import WorkflowState, event

TIER_BONUS = {1: 2.0, 2: 1.0, 3: 0.0, 4: -0.5}


class IncidentDetectionAgent:
    name = "incident_detection_agent"

    def run(self, state: WorkflowState) -> dict[str, Any]:
        result = detect(state.get("signals") or {})
        anomalies = result["anomalies"]
        service = str((state.get("trigger") or {}).get("service") or "").lower()
        ranked = [s for s, _ in sorted(result["service_scores"].items(), key=lambda kv: kv[1], reverse=True)]
        primary = service if service in result["service_scores"] else (ranked[0] if ranked else service)

        hints: Counter[str] = Counter()
        for a in anomalies:
            if a.get("category_hint"):
                hints[a["category_hint"]] += a["weight"] * (1.5 if a["type"] == "log" else 1.0)
        category = hints.most_common(1)[0][0] if hints else "unknown"

        top = anomalies[0] if anomalies else None
        title = (
            f"{primary}: {top['description']}" if top and result["is_incident"] else f"No incident detected for {primary}"
        )
        detection = {
            "is_incident": result["is_incident"],
            "score": result["score"],
            "anomalies": anomalies,
            "affected_services": ranked,
            "primary_service": primary,
            "category_hint": category,
            "title": title[:240],
            "summary": f"{len(anomalies)} anomalies across {len(ranked)} service(s); score {result['score']:.1f}",
        }
        msg = (
            f"Incident detected on {primary} ({category}); {detection['summary']}"
            if result["is_incident"]
            else f"No incident: anomaly score {result['score']:.1f} below threshold"
        )
        update: dict[str, Any] = {"detection": detection, "events": [event("detection", msg)]}
        if not result["is_incident"]:
            update["status"] = "no_incident"
            update["outcome"] = "no_incident"
        return update


class SeverityClassificationAgent:
    name = "severity_classification_agent"

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    def run(self, state: WorkflowState) -> dict[str, Any]:
        detection = state.get("detection") or {}
        anomalies = detection.get("anomalies", [])
        tiers = {s["name"].lower(): int(s.get("tier", 3)) for s in self.ctx.platform.services()}
        affected = detection.get("affected_services", [])
        primary = detection.get("primary_service", "")

        weights = sorted((a["weight"] for a in anomalies), reverse=True)
        base = float(weights[0]) if weights else 0.0
        extra = 0.5 * min(max(len(weights) - 1, 0), 4)
        tier = min([tiers.get(s, 3) for s in affected] or [tiers.get(primary, 3)])
        tier_bonus = TIER_BONUS.get(tier, 0.0)
        blast = 0.5 * min(max(len(affected) - 1, 0), 3)
        impact = 0.0
        for a in anomalies:
            if (a["name"] == "error_rate_percent" and a.get("value", 0) >= 20) or (
                a["name"] == "availability_percent" and a.get("value", 100) <= 95
            ):
                impact = 1.0
        score = base + extra + tier_bonus + blast + impact
        level = "P1" if score >= 6 else "P2" if score >= 4.5 else "P3" if score >= 3 else "P4"
        severity = {
            "level": level,
            "score": round(score, 2),
            "tier": tier,
            "factors": {"anomaly_severity": base, "anomaly_count": extra, "service_tier": tier_bonus,
                        "blast_radius": blast, "user_impact": impact},
            "rationale": f"{level}: tier-{tier} service(s), {len(affected)} affected, {len(anomalies)} anomalies",
        }
        return {"severity": severity, "events": [event("severity", severity["rationale"])]}
