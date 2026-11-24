"""Correlation Agent: correlates metrics, logs, traces, alerts and deployments and maps service dependencies."""
from __future__ import annotations

import re
from collections import Counter, deque
from datetime import timedelta
from typing import Any

from agents.common.ports import AgentContext
from agents.common.timeutil import iso, parse_ts
from workflows.state import WorkflowState, event

DEPLOY_LOOKBACK = timedelta(minutes=60)
DEPLOY_LOOKAHEAD = timedelta(minutes=5)


class CorrelationAgent:
    name = "correlation_agent"

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    def run(self, state: WorkflowState) -> dict[str, Any]:
        detection = state.get("detection") or {}
        anomalies: list[dict[str, Any]] = detection.get("anomalies", [])
        signals = state.get("signals") or {}
        inventory = self.ctx.platform.services()
        graph = {s["name"].lower(): [d.lower() for d in s.get("depends_on") or []] for s in inventory}
        known = set(graph)

        affected = {a["service"] for a in anomalies}
        first_seen: dict[str, str] = {}
        weight: Counter[str] = Counter()
        evidence: dict[str, Counter[str]] = {}
        for a in anomalies:
            svc = a["service"]
            weight[svc] += a["weight"]
            if svc not in first_seen or a["first_seen"] < first_seen[svc]:
                first_seen[svc] = a["first_seen"]
            evidence.setdefault(svc, Counter())[a["type"]] += 1

        roots = [s for s in affected if not (self._descendants(graph, s) & affected)]
        roots.sort(key=lambda s: (first_seen.get(s, ""), -weight[s]))

        failing_calls = self._failing_calls(anomalies)
        mentions = self._log_mentions(signals.get("logs", []), affected, known)
        suspects: dict[str, dict[str, Any]] = {}

        def add_suspect(callee: str, source: str, count: int) -> None:
            entry = suspects.setdefault(
                callee, {"service": callee, "evidence": [], "count": 0, "monitored": callee in known}
            )
            entry["evidence"].append(source)
            entry["count"] += count

        for call in failing_calls:
            if call["callee"] not in affected:
                add_suspect(call["callee"], "trace", call["errors"])
        for (_caller, callee), n in mentions.items():
            if callee not in affected:
                add_suspect(callee, "log", n)
        suspect_list = sorted(suspects.values(), key=lambda s: s["count"], reverse=True)
        for s in suspect_list:
            s["evidence"] = sorted(set(s["evidence"]))

        root_candidates = [
            {"service": s, "first_seen": first_seen.get(s), "weight": weight[s], "monitored": True,
             "evidence": dict(evidence.get(s, {}))}
            for s in roots
        ] + [{"service": s["service"], "first_seen": None, "weight": 0, "monitored": s["monitored"],
              "evidence": {e: s["count"] for e in s["evidence"]}, "unmonitored_suspect": True} for s in suspect_list]

        impact = []
        for s in sorted(affected, key=lambda x: first_seen.get(x, "")):
            blockers = sorted(self._descendants(graph, s) & affected)
            impact.append({
                "service": s, "first_anomaly": first_seen.get(s), "weight": weight[s],
                "role": "downstream_victim" if blockers else "root_candidate", "affected_dependencies": blockers,
                "evidence": dict(evidence.get(s, {})),
            })
        paths = self._propagation_paths(graph, affected, [c["service"] for c in root_candidates])

        deployments = self._recent_deployments(anomalies, set(affected) | {c["service"] for c in root_candidates})
        timeline = self._timeline(anomalies, deployments, signals.get("alerts", []))

        correlation = {
            "dependency_graph": {s: graph.get(s, []) for s in sorted(affected)},
            "root_candidates": root_candidates,
            "service_impact": impact,
            "propagation_paths": paths,
            "failing_calls": failing_calls,
            "log_mentions": [{"caller": c, "callee": d, "count": n} for (c, d), n in mentions.items()],
            "recent_deployments": deployments,
            "timeline": timeline,
        }
        top = root_candidates[0]["service"] if root_candidates else "n/a"
        return {"correlation": correlation,
                "events": [event("correlation", f"Correlated {len(anomalies)} anomalies; probable origin: {top}; "
                                 f"{len(deployments)} recent deployment(s)")]}

    @staticmethod
    def _descendants(graph: dict[str, list[str]], start: str) -> set[str]:
        seen: set[str] = set()
        queue = deque(graph.get(start, []))
        while queue:
            node = queue.popleft()
            if node in seen or node == start:
                continue
            seen.add(node)
            queue.extend(graph.get(node, []))
        return seen

    @staticmethod
    def _failing_calls(anomalies: list[dict[str, Any]]) -> list[dict[str, Any]]:
        calls = []
        for a in anomalies:
            for peer, count in (a.get("peers") or {}).items():
                calls.append({"caller": a["service"], "callee": peer, "errors": count})
        return calls

    @staticmethod
    def _log_mentions(logs: list[dict[str, Any]], affected: set[str], known: set[str]) -> Counter[tuple[str, str]]:
        patterns = {k: re.compile(rf"(?<![\w-]){re.escape(k)}(?![\w-])", re.I) for k in known}
        mentions: Counter[tuple[str, str]] = Counter()
        for log in logs:
            if log["service"] not in affected or log["severity"] not in ("error", "critical"):
                continue
            for other, rx in patterns.items():
                if other != log["service"] and rx.search(log["message"]):
                    mentions[(log["service"], other)] += 1
        return Counter({k: v for k, v in mentions.items() if v >= 2})

    @staticmethod
    def _propagation_paths(graph: dict[str, list[str]], affected: set[str], roots: list[str]) -> list[list[str]]:
        paths: list[list[str]] = []
        for victim in sorted(affected):
            for root in roots:
                if victim == root:
                    continue
                queue = deque([[victim]])
                visited = {victim}
                while queue:
                    path = queue.popleft()
                    if path[-1] == root:
                        paths.append(path)
                        break
                    for nxt in graph.get(path[-1], []):
                        if nxt not in visited:
                            visited.add(nxt)
                            queue.append(path + [nxt])
        return paths

    def _recent_deployments(self, anomalies: list[dict[str, Any]], services: set[str]) -> list[dict[str, Any]]:
        if not anomalies:
            return []
        first = min(parse_ts(a["first_seen"]) for a in anomalies)
        found = self.ctx.platform.deployments(sorted(services), first - DEPLOY_LOOKBACK)
        out = []
        for d in found:
            at = parse_ts(d["deployed_at"])
            if at > first + DEPLOY_LOOKAHEAD:
                continue
            out.append({**d, "minutes_before_anomaly": round((first - at).total_seconds() / 60, 1)})
        out.sort(key=lambda d: d["deployed_at"])
        return out

    @staticmethod
    def _timeline(anomalies: list[dict[str, Any]], deployments: list[dict[str, Any]],
                  alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items = [
            {"timestamp": a["first_seen"], "type": a["type"], "service": a["service"], "severity": a["severity"],
             "description": a["description"]}
            for a in anomalies
        ]
        items += [
            {"timestamp": iso(parse_ts(d["deployed_at"])), "type": "deployment", "service": d["service"],
             "severity": "info", "description": f"Deployment {d.get('version') or ''} ({d.get('status', '')})".strip()}
            for d in deployments
        ]
        items += [
            {"timestamp": al["timestamp"], "type": "alert", "service": al["service"], "severity": al["severity"],
             "description": f"Alert fired: {al['name']}"}
            for al in alerts if al["service"]
        ]
        items.sort(key=lambda i: i["timestamp"])
        return items
