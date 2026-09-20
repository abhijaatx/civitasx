from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from civitas_api.connector_contracts import ConnectorResult
from civitas_api.connectors import get_connector
from civitas_api.official_api_connector import OfficialApiConnector
from civitas_api.research import ResearchIndex


def test_official_api_connector_requires_https_endpoint():
    with pytest.raises(ValueError, match="HTTPS"):
        OfficialApiConnector(
            authority_id="gba",
            endpoint_url="http://authority.example/complaints",
            api_token="scoped-token",
        )


def test_certified_api_profile_is_opt_in():
    index = ResearchIndex(Path(__file__).parents[1] / "data" / "corpus" / "manifest.json")
    disabled = get_connector(index, "gba")
    enabled = get_connector(
        index,
        "gba",
        official_api_authority_id="gba",
        official_api_configured=True,
        official_api_url="https://authority.example/complaints",
    )
    assert disabled is not None and disabled.provider == "local_fixture"
    assert enabled is not None
    assert enabled.provider == "official_api"
    assert enabled.submission_enabled is True


@pytest.mark.asyncio
async def test_official_api_connector_returns_verified_reference_and_idempotency():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer scoped-token"
        assert request.headers["Idempotency-Key"] == "hash-123"
        assert request.headers["X-Civitas-Approval-Hash"] == "hash-123"
        body = json.loads(request.content)
        assert body["approval_hash"] == "hash-123"
        assert body["complaint"]["title"] == "Broken streetlight"
        return httpx.Response(
            201,
            json={
                "grievance_id": "G-123",
                "tracking_url": "https://authority.example/track/G-123",
                "message": "Accepted",
            },
        )

    connector = OfficialApiConnector(
        authority_id="gba",
        endpoint_url="https://authority.example/complaints",
        api_token="scoped-token",
        transport=httpx.MockTransport(handler),
    )
    result = await connector.submit(
        {"title": "Broken streetlight"},
        approval="hash-123",
        idempotency_key="hash-123",
    )

    assert result.status == "submitted"
    assert result.external_reference_id == "G-123"
    assert result.tracking_url == "https://authority.example/track/G-123"


@pytest.mark.asyncio
async def test_official_api_connector_never_claims_success_without_reference():
    connector = OfficialApiConnector(
        authority_id="gba",
        endpoint_url="https://authority.example/complaints",
        api_token="scoped-token",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"message": "queued"})),
    )
    result = await connector.submit(
        {"title": "Broken streetlight"},
        approval="hash-123",
        idempotency_key="hash-123",
    )

    assert result.status == "outcome_unknown"
    assert result.external_reference_id is None


def test_official_api_submission_route_records_verified_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVITAS_AUTH_MODE", "local")
    monkeypatch.setenv("CIVITAS_STORAGE", "sqlite")
    monkeypatch.setenv("CIVITAS_SQLITE_PATH", str(tmp_path / "civitas.sqlite3"))
    monkeypatch.setenv("CIVITAS_OFFICIAL_API_URL", "https://authority.example/complaints")
    monkeypatch.setenv("CIVITAS_OFFICIAL_API_TOKEN", "scoped-token")
    monkeypatch.setenv("CIVITAS_OFFICIAL_API_AUTHORITY_ID", "gba")

    from civitas_api import config, main

    config.get_settings.cache_clear()
    main.get_store.cache_clear()
    main.get_research_index.cache_clear()
    main.get_live_registry.cache_clear()
    main.get_community_store.cache_clear()
    main.get_agent.cache_clear()

    async def fake_submit(self, payload, *, approval, idempotency_key):
        assert payload["content_hash"] == approval == idempotency_key
        return ConnectorResult(
            status="submitted",
            authority_id="gba",
            message="Accepted by the certified authority API",
            external_reference_id="G-API-123",
            tracking_url="https://authority.example/track/G-API-123",
            receipt={"transport": "test"},
        )

    monkeypatch.setattr(OfficialApiConnector, "submit", fake_submit)
    try:
        with TestClient(main.app) as client:
            auth = client.post(
                "/api/auth/register",
                json={
                    "name": "API Resident",
                    "email": "api-resident@example.com",
                    "password": "correct horse battery staple",
                },
            )
            token = auth.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            ticket = client.post(
                "/api/tickets",
                headers=headers,
                json={
                    "title": "Broken streetlight",
                    "description": "The streetlight has been broken for three nights.",
                    "authority_id": "gba",
                    "locality": "Indiranagar",
                },
            ).json()["ticket"]
            prepared = client.post(
                f"/api/tickets/{ticket['id']}/prepare", headers=headers, json={}
            ).json()
            assert prepared["missing_fields"] == []
            approved = client.post(
                f"/api/tickets/{ticket['id']}/preparation/approve",
                headers=headers,
                json={"content_hash": prepared["content_hash"]},
            )
            assert approved.status_code == 200

            submitted = client.post(f"/api/tickets/{ticket['id']}/submit", headers=headers)
            assert submitted.status_code == 200, submitted.text
            assert submitted.json()["status"] == "submitted"
            assert submitted.json()["external_reference_id"] == "G-API-123"
    finally:
        main.get_store.cache_clear()
        main.get_research_index.cache_clear()
        main.get_live_registry.cache_clear()
        main.get_community_store.cache_clear()
        main.get_agent.cache_clear()
        config.get_settings.cache_clear()
