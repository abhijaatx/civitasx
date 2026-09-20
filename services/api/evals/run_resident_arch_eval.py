"""Evaluate the data-driven architecture against 100 resident scenarios.

This is a structural reliability evaluation, not a prose-quality benchmark.
It asks whether the architecture can produce a supported first response,
identify the missing slot, route to a live connector, or enforce approval.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from civitas_api.civic_architecture import CivicWorkflow
from civitas_api.police_jurisdiction import PoliceStationRegistry

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "resident_issues_100.jsonl"


def load_cases() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in CASES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def outcome(plan: Any, police_registry: PoliceStationRegistry) -> str:
    if plan.capability_id == "police.jurisdiction":
        if not plan.location:
            return "needs_clarification"
        resolution = police_registry.resolve(plan.location)
        if resolution.get("status") != "ok":
            return "needs_clarification"
    if plan.reliable_now:
        return "reliable_now"
    if plan.needs_clarification:
        return "needs_clarification"
    if plan.mode == "connector_required":
        return "safe_route_or_draft"
    if plan.mode == "live":
        return "live_connector_required"
    if plan.mode == "approval_required":
        return "approval_required"
    return "unsupported_or_unclassified"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Print the complete case report")
    args = parser.parse_args()
    workflow = CivicWorkflow()
    police_registry = PoliceStationRegistry()
    results: list[dict[str, Any]] = []
    for case in load_cases():
        plan = workflow.plan(case["prompt"])
        results.append(
            {
                "id": case["id"],
                "prompt": case["prompt"],
                "outcome": outcome(plan, police_registry),
                "capability": plan.capability_id,
                "mode": plan.mode,
                "confidence": plan.confidence,
                "location": plan.location,
                "missing_slots": list(plan.missing_slots),
                "tools": list(plan.tools),
                "reason": plan.reason,
            }
        )
    counts = Counter(item["outcome"] for item in results)
    summary = {
        "total": len(results),
        "reliable_now": counts["reliable_now"],
        "safe_first_response": sum(
            counts[key]
            for key in (
                "reliable_now",
                "safe_route_or_draft",
                "needs_clarification",
                "approval_required",
            )
        ),
        "requires_live_connector": counts["live_connector_required"],
        "unsupported_or_unclassified": counts["unsupported_or_unclassified"],
        "outcomes": dict(counts),
    }
    print(json.dumps({"summary": summary, "results": results} if args.json else summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
