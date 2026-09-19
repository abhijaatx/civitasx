#!/usr/bin/env python3
"""Opt-in AgentCore Browser feasibility check.

The script is intentionally a plan by default. When the optional AgentCore
Browser SDK and Playwright are installed, `--allow-paid` opens only
`https://example.com`, prints the title, and closes the session in `finally`.
Live View URLs are emitted only for the active session and are never persisted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os


async def run_remote(region: str, include_live_view: bool = False) -> dict[str, str]:
    from bedrock_agentcore.tools.browser_client import (
        browser_session,  # type: ignore[import-not-found]
    )
    from playwright.async_api import async_playwright  # type: ignore[import-not-found]

    async with async_playwright() as playwright:
        with browser_session(region) as client:
            ws_url, headers = client.generate_ws_headers()
            browser = await playwright.chromium.connect_over_cdp(ws_url, headers=headers)
            try:
                page = browser.contexts[0].pages[0]
                await page.goto("https://example.com", wait_until="domcontentloaded")
                result = {"title": await page.title(), "url": page.url}
                if include_live_view:
                    result["live_view_url"] = client.generate_live_view_url(expires=60)
                return result
            finally:
                await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument(
        "--print-live-view",
        action="store_true",
        help="Print a one-minute Live View URL while the session is active",
    )
    parser.add_argument("--region", default=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-south-1")
    args = parser.parse_args()
    plan = {"region": args.region, "url": "https://example.com", "live_view": "ephemeral"}
    if not args.allow_paid:
        print(json.dumps({"mode": "dry-run", "plan": plan}, indent=2))
        return 0
    if os.getenv("CIVITAS_ALLOW_PAID_SMOKE") != "1":
        raise SystemExit("Refusing browser smoke test: set CIVITAS_ALLOW_PAID_SMOKE=1 explicitly")
    try:
        print(
            json.dumps(
                {
                    "mode": "paid-smoke",
                    **asyncio.run(run_remote(args.region, args.print_live_view)),
                },
                indent=2,
            )
        )
    except ImportError as exc:
        raise SystemExit(
            "Install the optional bedrock-agentcore and playwright packages before the paid smoke test"
        ) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
