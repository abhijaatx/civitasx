#!/usr/bin/env python3
"""Read-only AWS access and eligibility preflight for CivitasX.

This script never invokes a model, starts a browser, creates resources, or
prints credentials. It checks control-plane visibility only; successful model
catalog discovery is not proof of invocation access or promotional-credit
coverage.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Check:
    name: str
    status: str
    detail: str


def configured_region() -> str | None:
    if os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"):
        return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    try:
        output = subprocess.run(
            ["aws", "configure", "get", "region"], capture_output=True, text=True, check=False
        ).stdout.strip()
        return output or None
    except OSError:
        return None


def run_check(name: str, fn) -> Check:  # type: ignore[no-untyped-def]
    try:
        detail = fn()
        return Check(name, "pass", detail)
    except Exception as exc:  # noqa: BLE001 - a preflight reports each failure
        return Check(name, "unavailable", f"{type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    args = parser.parse_args()

    region = configured_region()
    checks: list[Check] = [
        Check(
            "region",
            "pass" if region else "unavailable",
            region or "Set AWS_REGION or a default CLI region",
        )
    ]

    if region:
        try:
            import boto3

            sts = boto3.client("sts", region_name=region)
            identity = sts.get_caller_identity()
            arn = str(identity.get("Arn", ""))
            checks.append(
                Check(
                    "credentials",
                    "pass",
                    f"Authenticated principal {arn.rsplit('/', 1)[-1] or 'available'}",
                )
            )

            def model_catalog() -> str:
                response = boto3.client("bedrock", region_name=region).list_foundation_models(
                    byProvider="Amazon"
                )
                ids = [
                    model.get("modelId", "")
                    for model in response.get("modelSummaries", [])
                    if any(key in model.get("modelId", "").lower() for key in ("nova", "titan-embed"))
                ]
                return f"{len(ids)} Amazon Nova/Titan embedding catalog entries visible"

            checks.append(run_check("bedrock-model-catalog", model_catalog))

            def browser_control_plane() -> str:
                client = boto3.client("bedrock-agentcore-control", region_name=region)
                response = client.list_browsers(maxResults=1)
                return f"AgentCore Browser control plane reachable; {len(response.get('browsers', []))} browser(s) listed"

            checks.append(run_check("agentcore-browser-control-plane", browser_control_plane))

            def s3_vectors_control_plane() -> str:
                client = boto3.client("s3vectors", region_name=region)
                response = client.list_vector_buckets(maxResults=1)
                return f"S3 Vectors control plane reachable; {len(response.get('vectorBuckets', []))} bucket(s) listed"

            checks.append(run_check("s3-vectors-control-plane", s3_vectors_control_plane))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("boto3-control-plane", "unavailable", f"{type(exc).__name__}: {exc}"))

    checks.append(
        Check(
            "credit-eligibility",
            "manual",
            "AWS credits are account- and program-specific; confirm the eligible-services list in Billing before paid calls.",
        )
    )
    checks.append(
        Check(
            "paid-smoke-calls",
            "blocked",
            "Model invocation and browser session creation require an explicit --allow-paid smoke run.",
        )
    )
    result: dict[str, Any] = {
        "service": "civitasx",
        "phase": 2,
        "region": region,
        "checks": [asdict(check) for check in checks],
        "safe_next_step": "Resolve unavailable checks and confirm credit eligibility before a bounded paid smoke test.",
    }
    print(json.dumps(result, indent=2) if args.json else "\n".join(f"[{c.status}] {c.name}: {c.detail}" for c in checks))
    return 0 if all(check.status in {"pass", "manual", "blocked"} for check in checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
