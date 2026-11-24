"""Knowledge Agent: retrieves historical incidents, runbooks, SOPs and postmortems (hybrid RAG search)."""
from __future__ import annotations

import json
import logging
from typing import Any

from agents.common.ports import AgentContext
from rag.store import SearchHit
from workflows.state import WorkflowState, event

logger = logging.getLogger(__name__)

GROUPS = {
    "incident": "similar_incidents",
    "runbook": "runbooks",
    "sop": "sops",
    "postmortem": "postmortems",
}


def hit_to_item(hit: SearchHit) -> dict[str, Any]:
    meta = hit.metadata
    steps: list[dict[str, Any]] = []
    if meta.get("steps"):
        try:
            steps = json.loads(meta["steps"])
        except (TypeError, ValueError):
            steps = []
    return {
        "id": hit.parent_id,
        "title": meta.get("title", hit.parent_id),
        "doc_type": meta.get("doc_type"),
        "category": meta.get("category"),
        "service": meta.get("service"),
        "outcome": meta.get("outcome"),
        "resolution_action": meta.get("resolution_action"),
        "actions_taken": [a for a in str(meta.get("actions_taken", "")).split(",") if a],
        "steps": steps,
        "score": round(hit.score, 3),
        "snippet": hit.text[:400],
    }


class KnowledgeAgent:
    name = "knowledge_agent"

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    def run(self, state: WorkflowState) -> dict[str, Any]:
        rca = dict(state.get("rca") or {})
        result: dict[str, Any] = {k: [] for k in GROUPS.values()}
        result["query"] = ""
        result["historical_support"] = False
        if not self.ctx.kb or not rca:
            return {"knowledge": result, "events": [event("knowledge", "Knowledge base unavailable; skipped retrieval")]}

        top_evidence = " ".join(e["detail"] for e in rca.get("evidence", [])[:3])
        query = f"{rca.get('category', '')} {rca.get('root_service', '')} {rca.get('root_cause', '')} {top_evidence}".strip()
        result["query"] = query
        merged: dict[str, SearchHit] = {}
        try:
            for hit in self.ctx.kb.search(query, k=10, mode="hybrid"):
                merged[hit.parent_id] = hit
            category = rca.get("category")
            if category and category != "unknown":
                for hit in self.ctx.kb.search(query, k=5, mode="hybrid", filters={"category": category}):
                    prev = merged.get(hit.parent_id)
                    if prev is None or hit.score > prev.score:
                        merged[hit.parent_id] = hit
        except Exception:
            logger.exception("Knowledge retrieval failed")
            return {"knowledge": result, "errors": [{"stage": "knowledge", "error": "retrieval failed"}],
                    "events": [event("knowledge", "Knowledge retrieval failed; continuing without history")]}

        for hit in sorted(merged.values(), key=lambda h: h.score, reverse=True):
            group = GROUPS.get(hit.metadata.get("doc_type", ""))
            if group and len(result[group]) < 5:
                result[group].append(hit_to_item(hit))

        update: dict[str, Any] = {"knowledge": result}
        support = [
            i for i in result["similar_incidents"]
            if i["category"] == rca.get("category") and i["outcome"] == "resolved" and i["score"] >= 0.4
        ]
        if support:
            result["historical_support"] = True
            rca["confidence"] = round(min(0.98, rca.get("confidence", 0.0) + 0.04), 3)
            rca["contributing_factors"] = list(rca.get("contributing_factors", [])) + [
                f"Matches {len(support)} previously resolved incident(s) with the same category"
            ]
            update["rca"] = rca
        counts = {k: len(v) for k, v in result.items() if isinstance(v, list)}
        update["events"] = [event("knowledge", f"Retrieved knowledge: {counts}")]
        return update
