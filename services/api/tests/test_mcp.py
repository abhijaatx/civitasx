from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _mcp_headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        # The MCP transport keeps DNS-rebinding protection enabled. This host
        # is in the local default allowlist.
        "Host": "localhost:8000",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _call_mcp(
    client: TestClient,
    token: str | None,
    method: str,
    params: dict[str, object] | None = None,
) -> dict:
    response = client.post(
        "/mcp/",
        headers=_mcp_headers(token),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _setup_mcp_client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CIVITAS_AUTH_MODE", "local")
    monkeypatch.setenv("CIVITAS_STORAGE", "sqlite")
    monkeypatch.setenv("CIVITAS_SQLITE_PATH", str(tmp_path / "civitas.sqlite3"))

    from civitas_api import config, main

    config.get_settings.cache_clear()
    main.get_store.cache_clear()
    main.get_research_index.cache_clear()
    main.get_live_registry.cache_clear()
    main.get_community_store.cache_clear()
    main.get_agent.cache_clear()
    return config, main


def test_mcp_requires_bearer_auth(tmp_path: Path, monkeypatch):
    config, main = _setup_mcp_client(tmp_path, monkeypatch)
    try:
        with TestClient(main.app) as client:
            response = client.post(
                "/mcp/",
                headers=_mcp_headers(),
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
    finally:
        main.get_store.cache_clear()
        main.get_research_index.cache_clear()
        main.get_live_registry.cache_clear()
        main.get_community_store.cache_clear()
        main.get_agent.cache_clear()
        config.get_settings.cache_clear()


def test_mcp_lists_tools_and_scopes_private_cases(tmp_path: Path, monkeypatch):
    config, main = _setup_mcp_client(tmp_path, monkeypatch)
    try:
        with TestClient(main.app) as client:
            auth = client.post(
                "/api/auth/register",
                json={
                    "name": "MCP Resident",
                    "email": "mcp-resident@example.com",
                    "password": "correct horse battery staple",
                },
            )
            assert auth.status_code == 201, auth.text
            token = auth.json()["access_token"]

            initialize = _call_mcp(
                client,
                token,
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            )
            assert initialize["result"]["serverInfo"]["name"] == "CivitasX"

            listed = _call_mcp(client, token, "tools/list")
            names = {tool["name"] for tool in listed["result"]["tools"]}
            assert {"get_civic_feed", "search_civic_records", "research_my_case"} <= names

            created = _call_mcp(
                client,
                token,
                "tools/call",
                {
                    "name": "create_case",
                    "arguments": {"goal": "Understand safer metro crossings"},
                },
            )
            content = created["result"]["content"][0]["text"]
            case = json.loads(content)
            assert case["goal"] == "Understand safer metro crossings"

            cases = _call_mcp(
                client,
                token,
                "tools/call",
                {"name": "list_my_cases", "arguments": {}},
            )
            case_items = json.loads(cases["result"]["content"][0]["text"])["items"]
            assert [item["id"] for item in case_items] == [case["id"]]
    finally:
        main.get_store.cache_clear()
        main.get_research_index.cache_clear()
        main.get_live_registry.cache_clear()
        main.get_community_store.cache_clear()
        main.get_agent.cache_clear()
        config.get_settings.cache_clear()


def test_mcp_agent_submission_public_post_and_profile_flow(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CIVITAS_DEMO_SUBMISSION_ENABLED", "1")
    config, main = _setup_mcp_client(tmp_path, monkeypatch)
    try:
        with TestClient(main.app) as client:
            auth = client.post(
                "/api/auth/register",
                json={
                    "name": "Action Resident",
                    "email": "action-resident@example.com",
                    "password": "correct horse battery staple",
                },
            )
            assert auth.status_code == 201, auth.text
            token = auth.json()["access_token"]

            profile = _call_mcp(
                client,
                token,
                "tools/call",
                {
                    "name": "remember_profile_value",
                    "arguments": {"key": "phone", "value": "9876543210"},
                },
            )
            assert json.loads(profile["result"]["content"][0]["text"])["confirmed"] is True

            authority = client.get("/api/authorities", headers={"Authorization": f"Bearer {token}"})
            assert authority.status_code == 200, authority.text
            authority_id = authority.json()["items"][0]["authority_id"]
            ticket = client.post(
                "/api/tickets",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "title": "Broken streetlight",
                    "description": "The streetlight has been broken for three nights.",
                    "authority_id": authority_id,
                    "locality": "Indiranagar",
                },
            )
            assert ticket.status_code == 201, ticket.text
            ticket_id = ticket.json()["ticket"]["id"]

            prepared = _call_mcp(
                client,
                token,
                "tools/call",
                {"name": "prepare_submission", "arguments": {"ticket_id": ticket_id}},
            )
            preparation = json.loads(prepared["result"]["content"][0]["text"])["preparation"]
            assert preparation["missing_fields"] == []

            approved = _call_mcp(
                client,
                token,
                "tools/call",
                {
                    "name": "approve_submission",
                    "arguments": {
                        "ticket_id": ticket_id,
                        "content_hash": preparation["content_hash"],
                    },
                },
            )
            assert json.loads(approved["result"]["content"][0]["text"])["valid"] is True

            submitted = _call_mcp(
                client,
                token,
                "tools/call",
                {
                    "name": "submit_approved_request",
                    "arguments": {
                        "ticket_id": ticket_id,
                        "content_hash": preparation["content_hash"],
                        "simulation": True,
                    },
                },
            )
            run = json.loads(submitted["result"]["content"][0]["text"])
            assert run["status"] == "submitted"
            assert run["receipt"]["simulation"] is True

            retried = _call_mcp(
                client,
                token,
                "tools/call",
                {
                    "name": "submit_approved_request",
                    "arguments": {
                        "ticket_id": ticket_id,
                        "content_hash": preparation["content_hash"],
                        "simulation": True,
                    },
                },
            )
            retried_run = json.loads(retried["result"]["content"][0]["text"])
            assert retried_run["id"] == run["id"]

            preview = _call_mcp(
                client,
                token,
                "tools/call",
                {
                    "name": "prepare_public_post",
                    "arguments": {
                        "ticket_id": ticket_id,
                        "title": "Streetlight issue",
                        "body": "Please fix the broken light near Indiranagar 12th Main.",
                    },
                },
            )
            public_preview = json.loads(preview["result"]["content"][0]["text"])
            assert public_preview["approval_required"] is True

            published = _call_mcp(
                client,
                token,
                "tools/call",
                {
                    "name": "publish_approved_post",
                    "arguments": {
                        "ticket_id": ticket_id,
                        "title": "Streetlight issue",
                        "body": "Please fix the broken light near Indiranagar 12th Main.",
                        "redaction_content_hash": public_preview["redaction_content_hash"],
                        "redaction_approved": True,
                    },
                },
            )
            post = json.loads(published["result"]["content"][0]["text"])
            assert post["ticket_id"] == ticket_id
            assert post["status"] == "submitted"
    finally:
        main.get_store.cache_clear()
        main.get_research_index.cache_clear()
        main.get_live_registry.cache_clear()
        main.get_community_store.cache_clear()
        main.get_agent.cache_clear()
        config.get_settings.cache_clear()


def test_ipgrs_connector_is_opt_in_and_requires_portal_fields(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CIVITAS_IPGRS_SUBMISSION_ENABLED", "1")
    config, main = _setup_mcp_client(tmp_path, monkeypatch)
    try:
        with TestClient(main.app) as client:
            auth = client.post(
                "/api/auth/register",
                json={
                    "name": "iPGRS Resident",
                    "email": "ipgrs-resident@example.com",
                    "password": "correct horse battery staple",
                },
            )
            token = auth.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            connector = client.get("/api/connectors/gba", headers=headers)
            assert connector.status_code == 200, connector.text
            profile = connector.json()
            assert profile["provider"] == "browser"
            assert profile["submission_enabled"] is True
            assert {field["key"] for field in profile["requirements"]} >= {
                "district",
                "taluk",
                "address",
                "pincode",
                "mobile",
            }

            ticket = client.post(
                "/api/tickets",
                headers=headers,
                json={
                    "title": "Blocked footpath",
                    "description": (
                        "A blocked footpath is forcing pedestrians into traffic every evening, "
                        "creating a repeated safety hazard near the crossing and preventing "
                        "wheelchair users from using the marked pedestrian route safely."
                    ),
                    "authority_id": "gba",
                    "locality": "Indiranagar",
                },
            ).json()["ticket"]
            prepared = client.post(
                f"/api/tickets/{ticket['id']}/prepare",
                headers=headers,
                json={"fields": {"district": "Bengaluru Urban"}},
            )
            assert prepared.status_code == 200, prepared.text
            assert set(prepared.json()["missing_fields"]) >= {
                "taluk",
                "address",
                "pincode",
                "mobile",
            }
    finally:
        main.get_store.cache_clear()
        main.get_research_index.cache_clear()
        main.get_live_registry.cache_clear()
        main.get_community_store.cache_clear()
        main.get_agent.cache_clear()
        config.get_settings.cache_clear()


def test_ipgrs_submit_opens_supervised_run_without_claiming_submission(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("CIVITAS_IPGRS_SUBMISSION_ENABLED", "1")
    config, main = _setup_mcp_client(tmp_path, monkeypatch)
    try:
        from civitas_api.ipgrs_connector import ipgrs_browser_manager
        from civitas_api.models import AgentRunStatus

        async def fake_start(**kwargs):
            return kwargs["community"].update_run(
                kwargs["owner_id"],
                kwargs["run_id"],
                status=AgentRunStatus.WAITING_FOR_USER,
                message="Portal opened for supervised review",
            )

        monkeypatch.setattr(ipgrs_browser_manager, "start", fake_start)
        with TestClient(main.app) as client:
            auth = client.post(
                "/api/auth/register",
                json={
                    "name": "Supervised Resident",
                    "email": "supervised@example.com",
                    "password": "correct horse battery staple",
                },
            )
            token = auth.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            ticket = client.post(
                "/api/tickets",
                headers=headers,
                json={
                    "title": "Unsafe crossing",
                    "description": (
                        "The crossing has no safe signal for pedestrians during the evening rush, "
                        "so residents are forced into moving traffic while children and older "
                        "people wait beside the road without a protected crossing."
                    ),
                    "authority_id": "gba",
                    "locality": "Jayanagar",
                },
            ).json()["ticket"]
            fields = {
                "district": "Bengaluru Urban",
                "taluk": "Bengaluru South",
                "address": "12th Main Road, Jayanagar",
                "pincode": "560041",
                "mobile": "9876543210",
            }
            prepared = client.post(
                f"/api/tickets/{ticket['id']}/prepare", headers=headers, json={"fields": fields}
            ).json()
            assert prepared["missing_fields"] == []
            approved = client.post(
                f"/api/tickets/{ticket['id']}/preparation/approve",
                headers=headers,
                json={"content_hash": prepared["content_hash"]},
            )
            assert approved.status_code == 200
            assert approved.json()["submission_enabled"] is True
            submitted = client.post(f"/api/tickets/{ticket['id']}/submit", headers=headers)
            assert submitted.status_code == 200, submitted.text
            assert submitted.json()["status"] == "waiting_for_user"
            assert submitted.json()["external_reference_id"] is None
    finally:
        main.get_store.cache_clear()
        main.get_research_index.cache_clear()
        main.get_live_registry.cache_clear()
        main.get_community_store.cache_clear()
        main.get_agent.cache_clear()
        config.get_settings.cache_clear()
