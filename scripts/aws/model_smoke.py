#!/usr/bin/env python3
"""Bounded, opt-in Bedrock smoke test.

Dry-run is the default. A paid invocation requires both `--allow-paid` and
`CIVITAS_ALLOW_PAID_SMOKE=1`; use the smallest supported model and a tiny
prompt. In a deployed environment, reserve usage in the application before
calling this script and settle the actual token counts afterward.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--region", default=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-south-1")
    parser.add_argument("--model-id", default="apac.amazon.nova-micro-v1:0")
    args = parser.parse_args()

    plan = {
        "region": args.region,
        "model_id": args.model_id,
        "input": "Reply with the single word READY.",
        "max_output_tokens": 8,
        "paid": bool(args.allow_paid),
    }
    if not args.allow_paid:
        print(json.dumps({"mode": "dry-run", "plan": plan}, indent=2))
        return 0
    if os.getenv("CIVITAS_ALLOW_PAID_SMOKE") != "1":
        raise SystemExit("Refusing paid smoke test: set CIVITAS_ALLOW_PAID_SMOKE=1 explicitly")

    import boto3

    client = boto3.client("bedrock-runtime", region_name=args.region)
    response: dict[str, Any] = client.converse(
        modelId=args.model_id,
        messages=[{"role": "user", "content": [{"text": plan["input"]}]}],
        inferenceConfig={"maxTokens": 8, "temperature": 0},
    )
    usage = response.get("usage", {})
    text = "".join(part.get("text", "") for part in response.get("output", {}).get("message", {}).get("content", []))
    print(json.dumps({"mode": "paid-smoke", "region": args.region, "model_id": args.model_id, "text": text, "usage": usage}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
