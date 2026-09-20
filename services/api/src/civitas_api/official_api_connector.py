"""Certified official-API submission connector.

This adapter is intentionally configuration-gated.  It does not guess an
authority endpoint, scrape a portal, or turn a public read-only URL into a
write path.  A deployment must provide an endpoint and scoped credential that
the authority or its API gateway has issued for the use case.
"""

from __future__ import annotations

from typing import Any

import httpx

from .connector_contracts import ConnectorResult
from .models import AgentRun, AgentRunStatus, TicketDetail, TicketPreparation, TicketStatus
from .policy import PolicyEngine


class OfficialApiConnectorError(RuntimeError):
    """The configured official API could not accept or verify a request."""


class OfficialApiConnector:
    """Small, strict JSON adapter for an authority-issued submission API.

    The authority-specific contract remains outside CivitasX.  The normalized
    response must expose one of ``reference_id``, ``grievance_id``,
    ``registration_number``, or ``id`` so a successful request can be proven.
    """

    def __init__(
        self,
        *,
        authority_id: str,
        endpoint_url: str,
        api_token: str,
        status_url: str | None = None,
        timeout_seconds: int = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not endpoint_url.strip() or not api_token.strip():
            raise ValueError("An official API endpoint and scoped token are required")
        parsed_endpoint = httpx.URL(endpoint_url.strip())
        if parsed_endpoint.scheme != "https" or not parsed_endpoint.host:
            raise ValueError("The official API endpoint must be an absolute HTTPS URL")
        if status_url:
            parsed_status = httpx.URL(status_url.strip())
            if parsed_status.scheme != "https" or not parsed_status.host:
                raise ValueError("The official status endpoint must be an absolute HTTPS URL")
        self.authority_id = authority_id
        self.endpoint_url = endpoint_url.strip()
        self.api_token = api_token.strip()
        self.status_url = status_url.strip() if status_url else None
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def _headers(self, *, approval: str, idempotency_key: str) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_token}",
            "Idempotency-Key": idempotency_key,
            "X-Civitas-Approval-Hash": approval,
        }

    @staticmethod
    def _payload_value(payload: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if payload.get(key) is not None:
                return payload[key]
        for container_key in ("data", "result", "response"):
            nested = payload.get(container_key)
            if not isinstance(nested, dict):
                continue
            for key in keys:
                if nested.get(key) is not None:
                    return nested[key]
        return None

    async def submit(
        self,
        payload: dict[str, Any],
        *,
        approval: str,
        idempotency_key: str | None = None,
    ) -> ConnectorResult:
        idempotency_key = idempotency_key or approval
        request_body = {
            "complaint": payload,
            "approval_hash": approval,
            "idempotency_key": idempotency_key,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    self.endpoint_url,
                    headers=self._headers(
                        approval=approval,
                        idempotency_key=idempotency_key,
                    ),
                    json=request_body,
                )
            response.raise_for_status()
            response_body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return ConnectorResult(
                status="outcome_unknown" if isinstance(exc, httpx.TimeoutException) else "failed",
                authority_id=self.authority_id,
                message=f"The official API did not return a usable response: {exc}",
                retryable=isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)),
            )

        if not isinstance(response_body, dict):
            return ConnectorResult(
                status="outcome_unknown",
                authority_id=self.authority_id,
                message="The official API returned a non-object response; receipt is unverified.",
                retryable=False,
            )
        reference_id = self._payload_value(
            response_body,
            "reference_id",
            "grievance_id",
            "registration_number",
            "id",
        )
        if not isinstance(reference_id, (str, int)) or not str(reference_id).strip():
            return ConnectorResult(
                status="outcome_unknown",
                authority_id=self.authority_id,
                message="The official API response contained no verifiable reference number.",
                receipt={"response_keys": sorted(response_body.keys())[:30]},
                retryable=False,
            )
        tracking_url_value = self._payload_value(response_body, "tracking_url", "status_url")
        tracking_url = None
        if isinstance(tracking_url_value, str):
            parsed_tracking_url = httpx.URL(tracking_url_value)
            if parsed_tracking_url.scheme == "https" and parsed_tracking_url.host:
                tracking_url = tracking_url_value
        acknowledgement = self._payload_value(
            response_body, "acknowledgement", "acknowledgment", "message"
        )
        return ConnectorResult(
            status="submitted",
            authority_id=self.authority_id,
            message=str(acknowledgement or "The official API accepted the complaint."),
            external_reference_id=str(reference_id),
            tracking_url=str(tracking_url) if tracking_url else None,
            receipt={
                "reference_id": str(reference_id),
                "tracking_url": tracking_url,
                "acknowledgement": str(acknowledgement) if acknowledgement else None,
                "transport": "official_api",
            },
        )

    async def status(self, reference_id: str) -> ConnectorResult:
        if not self.status_url:
            return ConnectorResult(
                status="unavailable",
                authority_id=self.authority_id,
                message="No official status endpoint is configured.",
            )
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.get(
                    self.status_url,
                    params={"reference_id": reference_id},
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {self.api_token}",
                    },
                )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return ConnectorResult(
                status="unavailable",
                authority_id=self.authority_id,
                message=f"The official status endpoint could not be checked: {exc}",
                retryable=True,
            )
        return ConnectorResult(
            status="status_checked",
            authority_id=self.authority_id,
            message="The official status endpoint returned a response.",
            external_reference_id=reference_id,
            receipt={"status_response": body if isinstance(body, dict) else {}},
        )


async def execute_official_api_submission(
    *,
    owner_id: str,
    run: AgentRun,
    detail: TicketDetail,
    preparation: TicketPreparation,
    community: Any,
    settings: Any,
) -> AgentRun:
    """Submit one approved payload and persist only a verified outcome."""

    connector = OfficialApiConnector(
        authority_id=preparation.authority_id or detail.ticket.authority_id or "unknown",
        endpoint_url=settings.official_api_url or "",
        api_token=settings.official_api_token or "",
        status_url=settings.official_api_status_url,
        timeout_seconds=settings.official_api_timeout_seconds,
    )
    payload = {
        "civitas_ticket_id": detail.ticket.civitas_ticket_id,
        "title": detail.ticket.title,
        "description": detail.ticket.description,
        "locality": detail.ticket.locality,
        "authority_id": detail.ticket.authority_id,
        "fields": preparation.fields,
        "attachment_ids": preparation.attachment_ids,
        "content_hash": preparation.content_hash,
    }
    result = await connector.submit(
        payload,
        approval=preparation.content_hash,
        idempotency_key=preparation.content_hash,
    )
    receipt = {
        **result.receipt,
        "content_hash": preparation.content_hash,
        "connector_id": run.connector_id,
    }
    if result.external_reference_id:
        receipt_policy = PolicyEngine(settings.opa_url).decide(
            "record_receipt",
            {
                "submission_started": True,
                "receipt_verified": True,
                "content_hash_match": True,
            },
        )
        receipt["policy"] = receipt_policy.as_dict()
        if receipt_policy.allow:
            community.record_outcome(
                owner_id,
                detail.ticket.id,
                status=TicketStatus.SUBMITTED,
                external_reference_id=result.external_reference_id,
                acknowledgement=result.message,
                tracking_url=result.tracking_url,
                submitted_content_hash=preparation.content_hash,
                note="Verified official API receipt",
            )
            return community.update_run(
                owner_id,
                run.id,
                status=AgentRunStatus.SUBMITTED,
                message=result.message,
                external_reference_id=result.external_reference_id,
                receipt=receipt,
            )

    outcome_unknown = result.status == "outcome_unknown" or result.retryable
    if outcome_unknown:
        community.record_outcome(
            owner_id,
            detail.ticket.id,
            status=TicketStatus.OUTCOME_UNKNOWN,
            submitted_content_hash=preparation.content_hash,
            note="Official API outcome could not be verified; investigate before retrying",
        )
        receipt["outcome"] = "outcome_unknown"
    return community.update_run(
        owner_id,
        run.id,
        status=AgentRunStatus.FAILED,
        message=result.message,
        receipt=receipt,
    )
