"""Small Hermes-compatible runtime primitives used by CivitasX.

The event and budget contracts are adapted from Nous Research's Hermes Agent
(MIT licensed).  CivitasX keeps the civic tool registry and evidence gate in
its own process so a model can never submit or publish without an explicit
application action.
"""

from .budget import IterationBudget
from .commands import HermesCommand, parse_command
from .events import (
    Commentary,
    GatewayNotice,
    MessageChunk,
    MessageStop,
    ToolCallChunk,
    ToolCallFinished,
)

__all__ = [
    "Commentary",
    "GatewayNotice",
    "HermesCommand",
    "IterationBudget",
    "MessageChunk",
    "MessageStop",
    "ToolCallChunk",
    "ToolCallFinished",
    "parse_command",
]
