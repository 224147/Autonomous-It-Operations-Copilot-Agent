"""Evaluation harness: measures detection accuracy, RCA accuracy and automated remediation coverage on labelled scenarios."""
from __future__ import annotations

import argparse
import json
import tempfile
from typing import Any

from agents.common.timeutil import utcnow
from agents.evaluation.fakes import FakePlatform, make_context
from agents.evaluation.scenarios import INCIDENT_SCENARIOS, NEGATIVE_SCENARIOS, build_scenario
from workflows.incident_workflow import build_workflow, run_workflow


def run_scenario(kind: str, seed: int, kb=None, auto_approve: str = "low") -> dict[str, Any]:  # noqa: ANN001
    scenario = build_scenario(kind, utcnow(), seed)
    platform = FakePlatform(deployments=scenario["deployments"], alerts=scenario["alerts"])
    ctx = make_context(kb=kb, platform=platform, auto_approve=auto_approve)
    for sig_kind, key in (("metric", "metrics"), ("log", "logs"), ("trace", "traces")):
        ctx.signals.add(sig_kind, scenario["signals"][key])
    state = run_workflow(build_workflow(ctx), {
        "incident_id": None, "trigger": {"service": scenario["service"], "window_minutes": 15, "reason": "evaluation"}})
    return {"scenario": scenario, "state": state}


def evaluate(seeds: int = 5, use_kb: bool = True) -> dict[str, Any]:
    kb = None
    if use_kb:
        from rag.embeddings import HashingEmbedder
        from rag.loader import seed_knowledge_base
        from rag.store import KnowledgeBase

        kb = KnowledgeBase(HashingEmbedder(), persist_dir=tempfile.mkdtemp(prefix="eval-chroma-"))
        seed_knowledge_base(kb, "knowledge_base")

    detection_ok = detection_total = 0
    rca_ok = rca_total = 0
    auto_covered = auto_total = 0
    failures: list[dict[str, Any]] = []
    for seed in range(seeds):
        for kind in INCIDENT_SCENARIOS + NEGATIVE_SCENARIOS:
            result = run_scenario(kind, seed, kb)
            scenario, state = result["scenario"], result["state"]
            detected = bool((state.get("detection") or {}).get("is_incident"))
            detection_total += 1
            detection_ok += detected == scenario["expected_incident"]
            if detected != scenario["expected_incident"]:
                failures.append({"scenario": kind, "seed": seed, "problem": f"detected={detected}"})
            if not scenario["expected_incident"]:
                continue
            rca_total += 1
            got = (state.get("rca") or {}).get("category")
            rca_ok += got == scenario["expected_category"]
            if got != scenario["expected_category"]:
                failures.append({"scenario": kind, "seed": seed, "problem": f"rca={got}, expected={scenario['expected_category']}"})
            auto_total += 1
            actions = (state.get("recommendations") or {}).get("actions", [])
            auto_covered += any(a["action"] != "open_ticket" for a in actions)
    return {
        "cases": detection_total,
        "detection_accuracy": round(detection_ok / detection_total, 4),
        "rca_accuracy": round(rca_ok / rca_total, 4),
        "automated_remediation_coverage": round(auto_covered / auto_total, 4),
        "targets": {"detection_accuracy": 0.90, "rca_accuracy": 0.85, "automated_remediation_coverage": 0.70},
        "failures": failures[:25],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate detection/RCA/remediation coverage on synthetic scenarios")
    parser.add_argument("--seeds", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.seeds), indent=2))


if __name__ == "__main__":
    main()
