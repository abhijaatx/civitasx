"""Uniform connector contract for lookup, preparation, submission, and status."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field


class ConnectorEvidence(BaseModel):
    source_id: str
    source_url: str | None = None
    retrieved_at: datetime
    valid_until: datetime | None = None
    content_hash: str | None = None
    confidence: float = Field(ge=0, le=1)
    claims: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)


class ConnectorResult(BaseModel):
    status: str
    authority_id: str
    message: str
    evidence: list[ConnectorEvidence] = Field(default_factory=list)
    external_reference_id: str | None = None
    tracking_url: str | None = None
    receipt: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False


class CivicConnector(Protocol):
    authority_id: str

    async def lookup(self, payload: dict[str, Any]) -> ConnectorResult: ...

    async def prepare(self, payload: dict[str, Any]) -> ConnectorResult: ...

    async def submit(
        self,
        payload: dict[str, Any],
        *,
        approval: str,
        idempotency_key: str | None = None,
    ) -> ConnectorResult: ...

    async def status(self, reference_id: str) -> ConnectorResult: ...


class DeclarativeConnector:
    """Safe default adapter for a connector profile with no live mutation."""

    def __init__(self, authority_id: str, contact_route: str):
        self.authority_id = authority_id
        self.contact_route = contact_route

    async def lookup(self, payload: dict[str, Any]) -> ConnectorResult:
        del payload
        return ConnectorResult(
            status="unavailable",
            authority_id=self.authority_id,
            message=f"Live lookup is not configured. Use {self.contact_route}.",
            retryable=False,
        )

    async def prepare(self, payload: dict[str, Any]) -> ConnectorResult:
        del payload
        return ConnectorResult(
            status="prepared_locally",
            authority_id=self.authority_id,
            message="The payload can be reviewed locally; no external submission occurred.",
        )

    async def submit(
        self,
        payload: dict[str, Any],
        *,
        approval: str,
        idempotency_key: str | None = None,
    ) -> ConnectorResult:
        del payload, approval, idempotency_key
        return ConnectorResult(
            status="blocked",
            authority_id=self.authority_id,
            message=(
                "Submission is disabled until a verified connector and receipt path are configured."
            ),
            retryable=False,
        )

    async def status(self, reference_id: str) -> ConnectorResult:
        return ConnectorResult(
            status="unavailable",
            authority_id=self.authority_id,
            message=f"No live status connector is configured for {reference_id}.",
        )
