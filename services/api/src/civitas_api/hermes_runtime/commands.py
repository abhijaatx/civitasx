"""Hermes-style slash commands for the civic Agent surface.

The command names follow the public Hermes CLI convention.  They are parsed
before civic intent detection so a user can explicitly steer a turn without
choosing an internal tool or provider.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HermesCommand:
    name: str
    argument: str = ""


COMMANDS = {
    "help",
    "new",
    "reset",
    "research",
    "complaint",
    "sources",
    "memory",
    "model",
    "stop",
}


def parse_command(value: str) -> HermesCommand | None:
    """Parse a leading ``/command`` without treating URLs as commands."""

    text = value.strip()
    if not text.startswith("/") or text.startswith("//"):
        return None
    head, _, argument = text[1:].partition(" ")
    name = head.casefold().strip()
    if name not in COMMANDS:
        return None
    return HermesCommand(name=name, argument=argument.strip())
