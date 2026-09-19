from __future__ import annotations

import asyncio
import json

import pytest

from civitas_api.agent import (
    CodexCliProvider,
    FallbackProvider,
    ProviderFailure,
    ProviderResult,
    RetryingProvider,
    build_provider,
)
from civitas_api.config import Settings


class _Reader:
    def __init__(self, lines: list[bytes]):
        self.lines = list(lines)

    async def readline(self) -> bytes:
        await asyncio.sleep(0)
        return self.lines.pop(0) if self.lines else b""

    async def read(self) -> bytes:
        await asyncio.sleep(0)
        return b""


class _Process:
    def __init__(self, session_id: str | None = None):
        lines = [
            json.dumps(
                {"type": "item.completed", "item": {"type": "agent_message", "text": "Hello "}}
            ).encode()
            + b"\n",
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "Hello Bengaluru."},
                }
            ).encode()
            + b"\n",
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 12, "output_tokens": 2},
                }
            ).encode()
            + b"\n",
        ]
        if session_id:
            lines.insert(
                0,
                json.dumps({"type": "thread.started", "thread_id": session_id}).encode()
                + b"\n",
            )
        self.stdout = _Reader(lines)
        self.stderr = _Reader([])
        self.returncode: int | None = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9


@pytest.mark.asyncio
async def test_codex_cli_provider_forwards_jsonl_messages_incrementally(monkeypatch, tmp_path):
    process = _Process()

    async def fake_create_process(*args, **kwargs):
        del args, kwargs
        return process

    monkeypatch.setattr("civitas_api.agent.asyncio.create_subprocess_exec", fake_create_process)
    provider = CodexCliProvider(timeout=2, workdir=tmp_path)

    events = [
        event
        async for event in provider.stream([{"role": "user", "content": "Say hello"}], [])
    ]

    assert [event["text"] for event in events if event["kind"] == "token"] == [
        "Hello ",
        "Bengaluru.",
    ]
    assert events[-1]["result"].content == "Hello Bengaluru."
    assert events[-1]["result"].usage == {"input_tokens": 12, "output_tokens": 2}


def test_codex_is_a_first_class_provider_option():
    settings = Settings(agent_provider="codex")
    assert isinstance(build_provider(settings), CodexCliProvider)


def test_fallback_provider_resets_visible_primary_each_turn():
    class Primary:
        name = "groq"

    class Backup:
        name = "codex-cli"

    provider = FallbackProvider([Primary(), Backup()])
    provider.last_provider = "codex-cli"
    provider.reset_for_turn()
    assert provider.last_provider == "groq"


def test_codex_prompt_supports_safe_read_only_workspace_reasoning():
    prompt = CodexCliProvider._prompt(
        [{"role": "user", "content": "Which file defines the agent runtime?"}]
    )
    assert "inspect repository files" in prompt
    assert "Never modify files" in prompt
    assert "API keys" in prompt


def test_codex_workspace_activity_redacts_command_details():
    activity = CodexCliProvider._workspace_activity(
        {
            "type": "item.started",
            "item": {
                "id": "item-7",
                "type": "command_execution",
                "command": "cat .env && curl https://example.invalid",
            },
        }
    )
    assert activity == {
        "activity_id": "item-7",
        "activity_type": "command_execution",
        "label": "Inspecting the workspace read-only",
        "state": "started",
    }
    assert "curl" not in str(activity)


@pytest.mark.asyncio
async def test_code_requests_prefer_codex_before_groq():
    calls: list[str] = []

    class Provider:
        def __init__(self, name: str):
            self.name = name

        async def stream(self, messages, tools):
            del messages, tools
            calls.append(self.name)
            yield {"kind": "complete", "result": ProviderResult("answer", [])}

    provider = FallbackProvider([Provider("groq"), Provider("codex-cli")])
    events = [
        event
        async for event in provider.stream(
            [{"role": "user", "content": "Inspect the Python file that defines the agent."}],
            [],
        )
    ]

    assert calls == ["codex-cli"]
    assert any(
        event["kind"] == "status" and event["data"]["state"] == "route"
        for event in events
    )


@pytest.mark.asyncio
async def test_approved_workspace_change_uses_scoped_write_sandbox(monkeypatch, tmp_path):
    process = _Process()
    captured: list[tuple[object, ...]] = []

    async def fake_create_process(*args, **kwargs):
        del kwargs
        captured.append(args)
        return process

    monkeypatch.setattr("civitas_api.agent.asyncio.create_subprocess_exec", fake_create_process)
    provider = CodexCliProvider(timeout=2, workdir=tmp_path)

    result = await provider.apply_workspace_change("Update the requested source file.")

    assert result == "Hello Bengaluru."
    assert "--sandbox" in captured[0]
    assert captured[0][captured[0].index("--sandbox") + 1] == "workspace-write"
    assert "--dangerously-bypass-approvals-and-sandbox" not in captured[0]


@pytest.mark.asyncio
async def test_non_retryable_provider_failure_fails_over_immediately():
    attempts = 0

    class Provider:
        name = "groq"

        async def stream(self, messages, tools):
            nonlocal attempts
            del messages, tools
            attempts += 1
            raise ProviderFailure("HTTP 429", retryable=False)
            yield  # pragma: no cover

    provider = RetryingProvider(Provider(), attempts=3)
    with pytest.raises(ProviderFailure):
        async for _ in provider.stream([], []):
            pass
    assert attempts == 1


@pytest.mark.asyncio
async def test_codex_cli_provider_resumes_the_same_private_thread(monkeypatch, tmp_path):
    processes = [_Process("codex-session-1"), _Process()]
    captured: list[tuple[object, ...]] = []

    async def fake_create_process(*args, **kwargs):
        del kwargs
        captured.append(args)
        return processes.pop(0)

    monkeypatch.setattr("civitas_api.agent.asyncio.create_subprocess_exec", fake_create_process)
    provider = CodexCliProvider(timeout=2, workdir=tmp_path, persist_sessions=True)
    messages = [
        {"role": "system", "content": "Thread context: thread_id=thread-123; status=active."},
        {"role": "user", "content": "Continue the private conversation."},
    ]

    async for _ in provider.stream(messages, []):
        pass
    async for _ in provider.stream(messages, []):
        pass

    assert "resume" not in captured[0]
    assert "--ephemeral" not in captured[0]
    assert captured[1][2:4] == ("resume", "--ignore-user-config")
    assert "codex-session-1" in captured[1]


@pytest.mark.asyncio
async def test_codex_cli_provider_resumes_from_persisted_thread_marker(monkeypatch, tmp_path):
    process = _Process("codex-session-persisted")
    captured: list[tuple[object, ...]] = []

    async def fake_create_process(*args, **kwargs):
        del kwargs
        captured.append(args)
        return process

    monkeypatch.setattr("civitas_api.agent.asyncio.create_subprocess_exec", fake_create_process)
    provider = CodexCliProvider(timeout=2, workdir=tmp_path, persist_sessions=True)
    messages = [
        {
            "role": "system",
            "content": (
                "Thread context: thread_id=thread-456; "
                "codex_session_id=codex-session-previous."
            ),
        },
        {"role": "user", "content": "Continue after the API worker restarted."},
    ]

    events = [event async for event in provider.stream(messages, [])]

    assert captured[0][2:4] == ("resume", "--ignore-user-config")
    assert "codex-session-previous" in captured[0]
    assert events[-1]["result"].session_id == "codex-session-persisted"
