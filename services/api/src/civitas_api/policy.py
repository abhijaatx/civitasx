"""OPA-compatible policy decisions with a deterministic local fallback.

When ``CIVITAS_OPA_URL`` is configured, decisions are evaluated by an OPA
server using the Rego policy in ``services/api/policies/civitas.rego``. Local
development remains safe when OPA is not running: the fallback implements the
same deny-by-default rules and records which engine made the decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class PolicyDecision:
    allow: bool
    action: str
    reason: str
    engine: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "allow": self.allow,
            "action": self.action,
            "reason": self.reason,
            "engine": self.engine,
        }


class PolicyEngine:
    def __init__(self, opa_url: str | None = None, timeout_seconds: float = 2.0):
        self.opa_url = opa_url.rstrip("/") if opa_url else None
        self.timeout_seconds = timeout_seconds

    def decide(self, action: str, input_data: dict[str, Any]) -> PolicyDecision:
        payload = {"action": action, **input_data}
        if self.opa_url:
            try:
                response = httpx.post(
                    f"{self.opa_url}/v1/data/civitas/decision",
                    json={"input": payload},
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                result = response.json().get("result") or {}
                return PolicyDecision(
                    allow=bool(result.get("allow")),
                    action=action,
                    reason=str(result.get("reason") or "OPA decision returned"),
                    engine="opa",
                )
            except (httpx.HTTPError, ValueError, TypeError):
                pass
        return self._local_decision(action, payload)

    @staticmethod
    def _local_decision(action: str, input_data: dict[str, Any]) -> PolicyDecision:
        allow = False
        if action == "answer":
            allow = bool(input_data.get("evidence_verified"))
        elif action == "name_station":
            allow = bool(input_data.get("station_evidence"))
        elif action == "draft_private":
            allow = bool(input_data.get("private")) and not bool(
                input_data.get("submission_requested")
            )
        elif action == "start_submission":
            # Starting a submission is a different decision from recording a
            # successful receipt.  Requiring a receipt here creates a circular
            # gate: the connector cannot obtain a receipt until it is allowed
            # to submit.  The caller must still provide an exact preparation
            # approval, an enabled/verified connector, and an explicit user
            # confirmation for this run.
            allow = (
                bool(input_data.get("approval"))
                and bool(input_data.get("connector_enabled"))
                and bool(input_data.get("resident_confirmation"))
            )
        elif action == "record_receipt":
            allow = (
                bool(input_data.get("submission_started"))
                and bool(input_data.get("receipt_verified"))
                and bool(input_data.get("content_hash_match"))
            )
        elif action == "submit":
            # Keep the legacy ``submit`` action for clients that still emit it;
            # new callers should use the more precise receipt decision.
            allow = bool(input_data.get("approval")) and bool(
                input_data.get("receipt_verified")
            )
        elif action == "publish":
            allow = bool(input_data.get("approval")) and bool(
                input_data.get("redaction_reviewed")
            )
        return PolicyDecision(
            allow=allow,
            action=action,
            reason=(
                "evidence and policy requirements satisfied"
                if allow
                else "policy requirements are not satisfied"
            ),
            engine="local-rego-compatible-fallback",
        )
