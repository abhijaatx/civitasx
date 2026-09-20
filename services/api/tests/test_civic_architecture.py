from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path

from civitas_api.civic_architecture import CivicWorkflow
from civitas_api.civic_tools import CivicToolRuntime
from civitas_api.models import AgentLocation, SendAgentMessageRequest
from civitas_api.policy import PolicyEngine
from civitas_api.spatial import POSTGIS_SCHEMA_SQL, SpatialMatch


def test_capability_workflow_resolves_police_and_preserves_safe_ambiguity():
    workflow = CivicWorkflow()

    police = workflow.plan("My phone was stolen in HSR Layout")
    assert police.capability_id == "police.jurisdiction"
    assert police.reliable_now is True
    assert "resolve_police_jurisdiction" in police.tools

    missing_location = workflow.plan("There is a pothole")
    assert missing_location.needs_clarification is True
    assert "incident_location" in missing_location.missing_slots


def test_policy_fallback_denies_external_submission_without_receipt():
    decision = PolicyEngine().decide(
        "submit",
        {"approval": True, "receipt_verified": False},
    )
    assert decision.allow is False
    assert decision.engine == "local-rego-compatible-fallback"


def test_submission_policy_separates_start_and_receipt_gates():
    start = PolicyEngine().decide(
        "start_submission",
        {
            "approval": True,
            "connector_enabled": True,
            "resident_confirmation": True,
        },
    )
    assert start.allow is True

    missing_receipt = PolicyEngine().decide(
        "record_receipt",
        {
            "submission_started": True,
            "receipt_verified": False,
            "content_hash_match": True,
        },
    )
    assert missing_receipt.allow is False

    verified = PolicyEngine().decide(
        "record_receipt",
        {
            "submission_started": True,
            "receipt_verified": True,
            "content_hash_match": True,
        },
    )
    assert verified.allow is True


def test_postgis_schema_has_jurisdiction_geometry_and_spatial_indexes():
    assert "geometry(MultiPolygon, 4326)" in POSTGIS_SCHEMA_SQL
    assert "civic_jurisdictions_boundary_gix" in POSTGIS_SCHEMA_SQL
    assert "USING gist (boundary)" in POSTGIS_SCHEMA_SQL


def test_structured_location_request_preserves_coordinates_and_language():
    request = SendAgentMessageRequest(
        content="There is a pothole here",
        location=AgentLocation(
            latitude=12.9116,
            longitude=77.6389,
            accuracy_m=18,
            source="browser",
        ),
        response_language="kn",
    )
    assert request.location is not None
    assert request.location.latitude == 12.9116
    assert request.response_language == "kn"

    typed = SendAgentMessageRequest(
        content="There is a pothole here",
        location=AgentLocation(
            label="HSR Layout near Microsoft office",
            address="HSR Layout near Microsoft office",
            source="user",
        ),
    )
    assert typed.location is not None
    assert typed.location.latitude is None


def test_coordinate_jurisdiction_result_is_evidence_backed():
    class FakeSpatialStore:
        def resolve_point(self, latitude: float, longitude: float):
            assert (latitude, longitude) == (12.9116, 77.6389)
            return [
                SpatialMatch(
                    record_id="ward-1",
                    name="HSR Layout Ward",
                    kind="ward",
                    authority_id="gba",
                    contact={"route": "official"},
                    source={"source_id": "ward-boundaries-2026"},
                )
            ]

    runtime = object.__new__(CivicToolRuntime)
    runtime.spatial_store = FakeSpatialStore()
    result = asyncio.run(
        runtime.execute(
            "resolve_ward_and_authority",
            {
                "location_text": "near Microsoft office, HSR Layout",
                "latitude": 12.9116,
                "longitude": 77.6389,
                "source": "browser",
            },
        )
    )
    assert result["spatial_status"] == "ok"
    assert result["resolution"]["ward"] == "HSR Layout Ward"
    assert result["spatial_matches"][0]["source"]["source_id"] == "ward-boundaries-2026"


def test_resident_architecture_eval_has_no_unclassified_cases():
    cases_path = Path(__file__).parents[1] / "evals" / "resident_issues_100.jsonl"
    cases = [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(cases) == 100
    workflow = CivicWorkflow()
    plans = [workflow.plan(case["prompt"]) for case in cases]
    outcomes = Counter(
        "reliable_now"
        if plan.reliable_now
        else "needs_clarification"
        if plan.needs_clarification
        else plan.mode
        for plan in plans
    )
    assert outcomes["reliable_now"] >= 4
    assert outcomes["unsupported"] == 0
