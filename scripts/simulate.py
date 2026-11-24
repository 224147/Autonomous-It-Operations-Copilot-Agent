"""Demo/validation simulator: injects a synthetic incident scenario through the public ingestion API.

Usage:
  python -m scripts.simulate --scenario memory_exhaustion --approve
Credentials come from the environment (SIM_EMAIL / SIM_PASSWORD) or an ingest key (INGEST_API_KEY).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import httpx

from agents.common.timeutil import utcnow
from agents.evaluation.scenarios import INCIDENT_SCENARIOS, build_scenario


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("COPILOT_URL", "http://localhost:8000"))
    parser.add_argument("--scenario", default="memory_exhaustion", choices=INCIDENT_SCENARIOS + ["healthy"])
    parser.add_argument("--approve", action="store_true", help="approve the remediation plan as the logged-in user")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    email, password = os.getenv("SIM_EMAIL"), os.getenv("SIM_PASSWORD")
    api = f"{args.base_url.rstrip('/')}/api/v1"
    with httpx.Client(timeout=30) as client:
        headers: dict[str, str] = {}
        if email and password:
            tokens = client.post(f"{api}/auth/login", json={"email": email, "password": password})
            tokens.raise_for_status()
            headers["Authorization"] = f"Bearer {tokens.json()['access_token']}"
        elif os.getenv("INGEST_API_KEY"):
            headers["X-API-Key"] = os.environ["INGEST_API_KEY"]
        else:
            print("Set SIM_EMAIL/SIM_PASSWORD or INGEST_API_KEY", file=sys.stderr)
            return 2

        scenario = build_scenario(args.scenario, utcnow())
        for dep in scenario["deployments"]:
            client.post(f"{api}/deployments", json=dep, headers=headers).raise_for_status()
        client.post(f"{api}/ingest/metrics", json={"metrics": scenario["signals"]["metrics"]}, headers=headers).raise_for_status()
        if scenario["signals"]["logs"]:
            client.post(f"{api}/ingest/logs", json={"logs": scenario["signals"]["logs"]}, headers=headers).raise_for_status()
        if scenario["signals"]["traces"]:
            client.post(f"{api}/ingest/traces", json={"spans": scenario["signals"]["traces"]}, headers=headers).raise_for_status()
        if scenario["alerts"]:
            client.post(f"{api}/ingest/alerts", json=scenario["alerts"], headers=headers).raise_for_status()
        print(f"Injected scenario '{args.scenario}' for service {scenario['service']}")

        deadline = time.time() + args.timeout
        approved: set[int] = set()
        while time.time() < deadline:
            time.sleep(3)
            items = client.get(f"{api}/incidents", params={"service": scenario["service"], "limit": 1}, headers=headers).json()["items"]
            if not items:
                continue
            incident = client.get(f"{api}/incidents/{items[0]['id']}", headers=headers).json()
            print(f"  INC-{incident['id']} status={incident['status']} workflow={incident['workflow_status']} "
                  f"severity={incident['severity']} category={incident['category']}")
            for approval in incident["approvals"]:
                if approval["status"] == "pending" and args.approve and approval["id"] not in approved:
                    resp = client.post(f"{api}/remediation/approvals/{approval['id']}/decision",
                                       json={"decision": "approve", "comment": "simulator"}, headers=headers)
                    print("  approved approval", approval["id"], resp.status_code)
                    approved.add(approval["id"])
            if incident["status"] in ("monitoring", "resolved", "escalated", "false_positive"):
                if incident["rca"]:
                    print(f"  RCA: {incident['rca']['root_cause']} (confidence {incident['rca']['confidence']:.0%})")
                print("Done.")
                return 0
        print("Timed out waiting for the workflow", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
