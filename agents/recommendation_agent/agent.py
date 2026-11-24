"""Recommendation Agent: ranks remediation actions and produces an action plan."""
from __future__ import annotations

from typing import Any

from agents.common.ports import AgentContext
from agents.common.taxonomy import ACTIONS, max_risk
from workflows.state import WorkflowState, event

# category -> [(action, prior probability that it resolves the incident)]
PRIORS: dict[str, list[tuple[str, float]]] = {
    "memory_exhaustion": [("restart_pod", 0.75), ("scale_deployment", 0.5), ("rollback_deployment", 0.4)],
    "cpu_saturation": [("scale_deployment", 0.8), ("restart_pod", 0.3), ("rollback_deployment", 0.3)],
    "traffic_spike": [("scale_deployment", 0.85), ("restart_service", 0.3)],
    "crash_loop": [("rollback_deployment", 0.7), ("restart_pod", 0.4)],
    "db_connectivity": [("restart_service", 0.65), ("restart_database", 0.3)],
    "db_contention": [("restart_service", 0.4)],
    "disk_full": [],
    "dependency_failure": [("restart_service", 0.55)],
    "deployment_regression": [("rollback_deployment", 0.85), ("restart_service", 0.3)],
    "config_error": [("rollback_deployment", 0.75)],
    "image_pull_failure": [("rollback_deployment", 0.7)],
    "certificate_issue": [],
    "node_failure": [("restart_vm", 0.7), ("restart_pod", 0.5)],
    "cache_failure": [("clear_cache", 0.75), ("restart_service", 0.5)],
    "unknown": [],
}
RISK_PENALTY = {"low": 0.0, "medium": 0.05, "high": 0.15}
MAX_RECOVERY_ACTIONS = 3
DEPLOYMENT_KINDS = {"deployment", "statefulset"}


def _default_params(action: str) -> dict[str, Any]:
    return {"replicas_delta": 2} if action == "scale_deployment" else {}


class RecommendationAgent:
    name = "recommendation_agent"

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    def run(self, state: WorkflowState) -> dict[str, Any]:
        rca = state.get("rca") or {}
        category = rca.get("category", "unknown")
        confidence = float(rca.get("confidence", 0.0))
        severity = (state.get("severity") or {}).get("level", "P3")
        knowledge = state.get("knowledge") or {}
        inventory = {s["name"].lower(): s for s in self.ctx.platform.services()}
        root = rca.get("root_service", "")
        primary = (state.get("detection") or {}).get("primary_service", root)
        # a dependency outside the inventory cannot be remediated by acting on its callers
        entry = inventory.get(root) or (None if category == "dependency_failure" else inventory.get(primary))
        target_name = (entry or {}).get("name", root).lower() if entry else root
        deployment_related = bool(rca.get("deployment_related"))

        candidates: list[dict[str, Any]] = []
        if entry:
            for action, prior in PRIORS.get(category, []):
                if not self._applicable(action, entry, inventory):
                    continue
                if action == "rollback_deployment":
                    prior = min(0.95, prior + (0.35 if deployment_related else 0.0))
                    if not deployment_related and category not in ("deployment_regression", "crash_loop", "config_error", "image_pull_failure"):
                        prior = min(prior, 0.2)
                action_target = target_name
                if action == "restart_database":
                    db = self._database_dependency(entry, inventory)
                    if not db:
                        continue
                    action_target = db["name"].lower()
                candidates.append(self._candidate(action, action_target, prior, category, confidence,
                                                  f"Typical remedy for {category.replace('_', ' ')}", knowledge))
            self._add_runbook_candidate(candidates, knowledge, category, confidence, target_name)

        candidates.sort(key=lambda c: c["score"], reverse=True)
        recovery = candidates[:MAX_RECOVERY_ACTIONS]
        actions = list(recovery)
        if severity in ("P1", "P2") or not recovery:
            actions.append(self._candidate(
                "open_ticket", target_name or primary, 0.5, category, confidence,
                "Track the incident with the owning team" if recovery else "No automated remedy applies; escalate to a human",
                knowledge, extra_params={"priority": severity},
            ))
        for rank, action in enumerate(actions, start=1):
            action["rank"] = rank
            action["id"] = f"a{rank}"
        risk = max_risk([a["risk"] for a in actions])
        plan = {
            "summary": (
                f"Recommended {len(recovery)} recovery action(s) for {category.replace('_', ' ')} on {target_name or primary}"
                if recovery else f"No automated recovery available for {category}; escalating to humans"
            ),
            "actions": actions,
            "risk": risk,
            "automatable": bool(recovery),
        }
        return {"recommendations": plan,
                "events": [event("recommendation", plan["summary"] + "; top action: " + (actions[0]["action"] if actions else "none"))]}

    @staticmethod
    def _applicable(action: str, entry: dict[str, Any], inventory: dict[str, dict[str, Any]]) -> bool:
        kind = entry.get("kind", "deployment")
        if action in ("restart_service", "restart_pod", "scale_deployment", "rollback_deployment"):
            return kind in DEPLOYMENT_KINDS and bool(entry.get("deployment"))
        if action == "restart_vm":
            return bool(entry.get("vm_instance_id"))
        if action == "clear_cache":
            return bool(entry.get("cache_prefix"))
        return True

    @staticmethod
    def _database_dependency(entry: dict[str, Any], inventory: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        if entry.get("kind") == "statefulset" and entry.get("deployment"):
            return entry
        for dep in entry.get("depends_on") or []:
            target = inventory.get(dep.lower())
            if target and target.get("kind") == "statefulset" and target.get("deployment"):
                return target
        return None

    def _candidate(self, action: str, target: str, prior: float, category: str, confidence: float, rationale: str,
                   knowledge: dict[str, Any], extra_params: dict[str, Any] | None = None) -> dict[str, Any]:
        successes, total = self.ctx.stats.get(category, action)
        success_rate = (successes + 1) / (total + 2)  # Laplace smoothing; neutral 0.5 when no history
        risk = ACTIONS[action]["risk"]
        score = 0.5 * prior + 0.3 * success_rate + 0.2 * confidence - RISK_PENALTY[risk]
        history = ""
        if total:
            history = f" Historically succeeded {successes}/{total} times for this category."
        for inc in knowledge.get("similar_incidents", []):
            if inc.get("outcome") == "resolved" and inc.get("resolution_action") == action and inc.get("score", 0) >= 0.4:
                score += 0.05
                history += f" Similar incident {inc['id']} was resolved with this action."
                break
        return {
            "action": action,
            "target": target,
            "params": {**_default_params(action), **(extra_params or {})},
            "risk": risk,
            "score": round(max(score, 0.0), 3),
            "rationale": rationale + "." + history,
            "description": ACTIONS[action]["description"],
            "requires_approval": True,
        }

    def _add_runbook_candidate(self, candidates: list[dict[str, Any]], knowledge: dict[str, Any], category: str,
                               confidence: float, target: str) -> None:
        present = {c["action"] for c in candidates}
        for rb in knowledge.get("runbooks", []):
            if rb.get("category") != category or not rb.get("steps"):
                continue
            steps = [s.get("action") for s in rb["steps"] if s.get("action") in ACTIONS]
            if any(s not in present and s != "open_ticket" for s in steps):
                cand = self._candidate("execute_runbook", target, 0.5, category, confidence,
                                       f"Runbook '{rb['title']}' covers this failure pattern", knowledge,
                                       extra_params={"runbook_id": rb["id"]})
                candidates.append(cand)
                return

