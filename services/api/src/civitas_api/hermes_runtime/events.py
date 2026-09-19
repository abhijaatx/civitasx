"""Structured agent events adapted from Hermes Agent's stream_events.py.

The events are transport-only.  CivitasX stores the resulting civic evidence
and action cards in its own message history, while the SSE endpoint uses these
types to render live progress in the Agent UI.

Copyright (c) 2025 Nous Research. Licensed under the MIT License. See
``NOTICE-HERMES-MIT.txt`` and ``THIRD_PARTY_NOTICES.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MessageChunk:
    text: str


@dataclass(frozen=True)
class MessageStop:
    final: bool = False


@dataclass(frozen=True)
class Commentary:
    text: str


@dataclass(frozen=True)
class ToolCallChunk:
    tool_name: str
    preview: str | None = None
    args: dict[str, Any] | None = None
    index: int = 0


@dataclass(frozen=True)
class ToolCallFinished:
    tool_name: str
    duration: float = 0.0
    ok: bool = True
    index: int = 0


@dataclass(frozen=True)
class GatewayNotice:
    kind: str
    text: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "Commentary",
    "GatewayNotice",
    "MessageChunk",
    "MessageStop",
    "ToolCallChunk",
    "ToolCallFinished",
]
