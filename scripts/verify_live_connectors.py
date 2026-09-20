#!/usr/bin/env python3
"""Verify every configured CivitasX live connector with a bounded GET.

The connector registry itself decides whether a request is safe to send. API
key, approval, consent, and non-refreshable entries are reported without a
network request. Successful public reads are persisted through the normal live
source cache so the Agent can use them as evidence on the next turn.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

API_SRC = Path(__file__).resolve().parents[1] / "services" / "api" / "src"
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(API_SRC) not in sys.path:
    sys.path.insert(0, str(API_SRC))

from civitas_api.live_connectors import LIVE_ENDPOINTS, LiveConnectorRegistry  # noqa: E402
from civitas_api.research import ResearchIndex  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="Per-request timeout in seconds (default: 20)",
    )
    parser.add_argument(
        "--endpoint",
        action="append",
        dest="endpoints",
        help="Verify only this endpoint ID; may be supplied more than once",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    return parser.parse_args()


def run(args: argparse.Namespace) -> list[dict[str, Any]]:
    root = REPO_ROOT / ".civitas"
    root.mkdir(parents=True, exist_ok=True)
    index = ResearchIndex(
        REPO_ROOT / "services/api/data/corpus/manifest.json",
        live_cache_path=root / "live-sources.json",
    )
    registry = LiveConnectorRegistry(
        timeout_seconds=max(3, min(args.timeout, 120)),
        status_path=root / "live-connector-status.json",
    )
    selected = set(args.endpoints or [])
    specs = [spec for spec in LIVE_ENDPOINTS if not selected or spec.endpoint_id in selected]
    known = {spec.endpoint_id for spec in LIVE_ENDPOINTS}
    unknown = sorted(selected - known)
    if unknown:
        raise SystemExit(f"Unknown endpoint ID(s): {', '.join(unknown)}")

    results: list[dict[str, Any]] = []
    for spec in specs:
        try:
            result = registry.refresh(spec.endpoint_id, index)
            results.append(
                {
                    "endpoint_id": spec.endpoint_id,
                    "url": str(spec.url),
                    "access_mode": spec.access_mode,
                    "status": result.endpoint.status,
                    "fetched": result.fetched,
                    "bytes_fetched": result.bytes_fetched,
                    "pages_indexed": result.pages_indexed,
                    "source_id": result.source_id,
                    "error": result.endpoint.last_error,
                    "message": result.message,
                }
            )
        except Exception as exc:  # noqa: BLE001 - report each connector independently
            results.append(
                {
                    "endpoint_id": spec.endpoint_id,
                    "url": str(spec.url),
                    "access_mode": spec.access_mode,
                    "status": "runner_error",
                    "fetched": False,
                    "bytes_fetched": 0,
                    "pages_indexed": 0,
                    "source_id": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "message": "Connector verification could not complete",
                }
            )
    return results


def main() -> int:
    args = parse_args()
    results = run(args)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(
                f"{result['endpoint_id']}: {result['status']} "
                f"fetched={result['fetched']} bytes={result['bytes_fetched']} "
                f"pages={result['pages_indexed']}"
            )
            if result["error"]:
                print(f"  {result['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
