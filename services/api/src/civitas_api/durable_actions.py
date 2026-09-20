"""Optional Temporal boundary for durable external civic actions."""

from __future__ import annotations

from typing import Any

try:
    from temporalio import workflow
except ImportError:  # pragma: no cover - minimal install fallback
    workflow = None  # type: ignore[assignment]


TEMPORAL_TASK_QUEUE = "civitas-civic-actions"


if workflow is not None:  # pragma: no cover - exercised by Temporal integration tests

    @workflow.defn
    class CivicActionWorkflow:
        @workflow.run
        async def run(self, action: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "waiting_for_activity_worker",
                "action": action,
                "task_queue": TEMPORAL_TASK_QUEUE,
            }

else:

    class CivicActionWorkflow:  # type: ignore[no-redef]
        """Import-safe placeholder when Temporal is not installed."""


class DurableActionDispatcher:
    def __init__(self, temporal_target: str | None = None):
        self.temporal_target = temporal_target

    def status(self) -> dict[str, Any]:
        if not self.temporal_target or workflow is None:
            return {
                "enabled": False,
                "reason": "Temporal target or architecture dependency is not configured",
                "task_queue": TEMPORAL_TASK_QUEUE,
            }
        return {
            "enabled": True,
            "target": self.temporal_target,
            "task_queue": TEMPORAL_TASK_QUEUE,
        }
