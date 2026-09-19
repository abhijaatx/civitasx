"""Replay the agent quality cases against a running local API.

Usage:
    uv run python evals/run_agent_eval.py --base-url http://127.0.0.1:8000 --limit 3

The runner is intentionally provider-agnostic. It scores the observable
contract (tool events, completion, citations, forbidden claims, and latency)
instead of judging prose by a single opaque pass/fail.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "agent_quality.jsonl"


def contains_direct_claim(answer: str, phrase: str) -> bool:
    """Avoid treating explicit negation as the prohibited claim itself."""

    normalized_answer = re.sub(r"[^a-z0-9]+", " ", answer.casefold()).strip()
    normalized_phrase = re.sub(r"[^a-z0-9]+", " ", phrase.casefold()).strip()
    start = 0
    while True:
        match_at = normalized_answer.find(normalized_phrase, start)
        if match_at < 0:
            return False
        context = normalized_answer[max(0, match_at - 80) : match_at].strip()
        if not re.search(
            r"(?:\bnot\b|\bnever\b|\bno\b|\bwithout\b|\bcannot\b|\bcan t\b|"
            r"\bdoes not\b|\bdoesn t\b|\bhas not\b|\bhasn t\b|\bnot yet\b)"
            r"(?:\s+\w+){0,8}\s*$",
            context,
        ):
            return True
        start = match_at + len(normalized_phrase)


def load_cases(limit: int | None) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line]
    return cases[:limit] if limit else cases


def register(client: httpx.Client) -> str:
    suffix = uuid.uuid4().hex[:12]
    response = client.post(
        "/api/auth/register",
        json={
            "name": "Agent Eval Runner",
            "email": f"agent-eval-{suffix}@example.com",
            "password": "AgentEval!2026Local",
        },
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def stream_turn(
    client: httpx.Client,
    token: str,
    prompt: str,
    thread_id: str,
    attachment_ids: list[str] | None = None,
) -> dict[str, Any]:
    events: list[tuple[str, dict[str, Any]]] = []
    current_event = "message"
    started = time.monotonic()
    with client.stream(
        "POST",
        f"/api/agent/threads/{thread_id}/messages/stream",
        headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
        json={
            "content": prompt,
            "attachment_ids": attachment_ids or [],
            "client_message_id": uuid.uuid4().hex,
        },
        timeout=180,
    ) as response:
        response.raise_for_status()
        data_lines: list[str] = []
        for line in response.iter_lines():
            if not line:
                if data_lines:
                    try:
                        events.append((current_event, json.loads("\n".join(data_lines))))
                    except json.JSONDecodeError:
                        pass
                    data_lines = []
                continue
            if line.startswith("event:"):
                current_event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
    elapsed_ms = round((time.monotonic() - started) * 1000)
    tool_names = [
        str(data.get("tool_name"))
        for event, data in events
        if event == "tool_call" and data.get("tool_name")
    ]
    delta_parts: list[str] = []
    final_content = ""
    for event, data in events:
        if event != "message":
            continue
        if isinstance(data.get("delta"), str):
            delta_parts.append(data["delta"])
        message = data.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            final_content = message["content"]
    return {
        "events": events,
        "tool_names": list(dict.fromkeys(tool_names)),
        "answer": final_content or "".join(delta_parts),
        "completed": any(event == "done" for event, _ in events),
        "citations": sum(
            1
            for event, data in events
            if event == "message"
            and isinstance(data.get("message"), dict)
            and any(
                part.get("type") == "citation"
                for part in data["message"].get("parts", [])
            )
        ),
        "latency_ms": elapsed_ms,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    cases = load_cases(args.limit)
    results: list[dict[str, Any]] = []
    with httpx.Client(base_url=args.base_url.rstrip("/")) as client:
        token = register(client)
        headers = {"Authorization": f"Bearer {token}"}
        for case in cases:
            thread_response = client.post(
                "/api/agent/threads",
                headers=headers,
                json={"goal": case["prompt"][:160]},
            )
            thread_response.raise_for_status()
            thread_id = str(thread_response.json()["thread"]["id"])
            attachment_ids: list[str] = []
            if case["id"] == "attachment-grounding":
                attachment_response = client.post(
                    f"/api/agent/threads/{thread_id}/attachments",
                    headers=headers,
                    files={
                        "file": (
                            "agent-eval-report.txt",
                            b"Reported date: 19 September 2026; locality: Indiranagar 12th Main.",
                            "text/plain",
                        )
                    },
                )
                attachment_response.raise_for_status()
                attachment_ids = [str(attachment_response.json()["id"])]
            result = stream_turn(
                client,
                token,
                case["prompt"],
                thread_id,
                attachment_ids,
            )
            answer = result["answer"].casefold()
            missing_tools = [
                tool for tool in case.get("must_use", []) if tool not in result["tool_names"]
            ]
            forbidden_claim = contains_direct_claim(answer, case.get("must_not_claim", ""))
            result.update(
                {
                    "id": case["id"],
                    "missing_tools": missing_tools,
                    "forbidden_claim_found": forbidden_claim,
                    "passed": bool(
                        result["completed"] and not missing_tools and not forbidden_claim
                    ),
                }
            )
            results.append(result)
    passed = sum(1 for item in results if item["passed"])
    print(json.dumps({"passed": passed, "total": len(results), "results": results}, indent=2))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
