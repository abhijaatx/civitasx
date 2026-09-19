from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CIVITAS_AUTH_MODE", "local")
    monkeypatch.setenv("CIVITAS_STORAGE", "sqlite")
    monkeypatch.setenv("CIVITAS_SQLITE_PATH", str(tmp_path / "civitas.sqlite3"))
    monkeypatch.setenv("CIVITAS_FRONTEND_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("CIVITAS_LOCAL_RECOVERY_CODE", "pilot-recovery-test-code")

    from civitas_api import config

    config.get_settings.cache_clear()
    from civitas_api import main

    main.get_store.cache_clear()
    main.get_research_index.cache_clear()
    main.get_live_registry.cache_clear()
    main.get_community_store.cache_clear()
    main.get_agent.cache_clear()
    with TestClient(main.app) as test_client:
        yield test_client
    main.get_store.cache_clear()
    main.get_research_index.cache_clear()
    main.get_live_registry.cache_clear()
    main.get_community_store.cache_clear()
    main.get_agent.cache_clear()
    config.get_settings.cache_clear()


def register(client: TestClient, name: str, email: str) -> str:
    response = client.post(
        "/api/auth/register",
        json={"name": name, "email": email, "password": "correct horse battery staple"},
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_mcp_cors_preflight_is_not_blocked_by_auth(client: TestClient):
    response = client.options(
        "/mcp",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code in {200, 204}
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_case_survives_reload_and_fresh_login(client: TestClient):
    token = register(client, "Asha Rao", "asha@example.com")
    created = client.post(
        "/api/cases",
        headers=auth(token),
        json={"goal": "Understand the proposed metro extension near my commute"},
    )
    assert created.status_code == 201, created.text
    case = created.json()
    assert case["status"] == "saved"
    assert case["version"] == 1

    client.post("/api/auth/logout", headers=auth(token))
    logged_in = client.post(
        "/api/auth/login",
        json={"email": "ASHA@example.com", "password": "correct horse battery staple"},
    )
    assert logged_in.status_code == 200
    fresh_token = logged_in.json()["access_token"]
    listed = client.get("/api/cases", headers=auth(fresh_token))
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == case["id"]
    fetched = client.get(f"/api/cases/{case['id']}", headers=auth(fresh_token))
    assert fetched.status_code == 200


def test_local_password_recovery_rotates_password_and_sessions(client: TestClient):
    assert client.get("/api/config").json()["auth"]["local_recovery_enabled"] is True
    old_token = register(client, "Recovery Resident", "recovery@example.com")
    assert client.get("/api/me", headers=auth(old_token)).status_code == 200

    invalid = client.post(
        "/api/auth/recover",
        json={
            "email": "recovery@example.com",
            "recovery_code": "wrong-code",
            "password": "new correct horse battery staple",
        },
    )
    assert invalid.status_code == 401

    recovered = client.post(
        "/api/auth/recover",
        json={
            "email": "RECOVERY@example.com",
            "recovery_code": "pilot-recovery-test-code",
            "password": "new correct horse battery staple",
        },
    )
    assert recovered.status_code == 200, recovered.text
    new_token = recovered.json()["access_token"]
    assert client.get("/api/me", headers=auth(old_token)).status_code == 401
    assert client.get("/api/me", headers=auth(new_token)).status_code == 200
    assert client.post(
        "/api/auth/login",
        json={"email": "recovery@example.com", "password": "correct horse battery staple"},
    ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"email": "recovery@example.com", "password": "new correct horse battery staple"},
    ).status_code == 200


def test_case_and_artifact_are_owner_scoped(client: TestClient):
    first = register(client, "First Resident", "first@example.com")
    second = register(client, "Second Resident", "second@example.com")
    created = client.post(
        "/api/cases",
        headers=auth(first),
        json={"goal": "Report a broken streetlight"},
    )
    case_id = created.json()["id"]
    artifact = client.post(
        f"/api/cases/{case_id}/artifacts",
        headers=auth(first),
        json={"name": "notes.txt", "content": "Fixture note", "kind": "note"},
    )
    assert artifact.status_code == 201
    artifact_id = artifact.json()["id"]

    assert client.get(f"/api/cases/{case_id}", headers=auth(second)).status_code == 404
    assert (
        client.get(f"/api/cases/{case_id}/artifacts", headers=auth(second)).status_code
        == 404
    )
    artifact_response = client.get(
        f"/api/cases/{case_id}/artifacts/{artifact_id}", headers=auth(second)
    )
    assert artifact_response.status_code == 404


def test_case_update_uses_optimistic_version(client: TestClient):
    token = register(client, "Version Tester", "version@example.com")
    case = client.post(
        "/api/cases", headers=auth(token), json={"goal": "Track a public works decision"}
    ).json()
    update = client.patch(
        f"/api/cases/{case['id']}",
        headers=auth(token),
        json={"notes": "Remember the source URL", "version": 1},
    )
    assert update.status_code == 200
    assert update.json()["version"] == 2
    stale = client.patch(
        f"/api/cases/{case['id']}",
        headers=auth(token),
        json={"notes": "Stale edit", "version": 1},
    )
    assert stale.status_code == 409


def test_usage_reservation_is_fail_closed_and_owner_scoped(client: TestClient):
    token = register(client, "Usage Tester", "usage@example.com")
    case = client.post(
        "/api/cases", headers=auth(token), json={"goal": "Test a bounded model operation"}
    ).json()

    from civitas_api.config import get_settings
    from civitas_api.main import get_store

    reservation = get_store().reserve_usage(
        owner_id=client.get("/api/me", headers=auth(token)).json()["id"],
        case_id=case["id"],
        kind="model",
        amount_usd=1.0,
        global_budget_usd=get_settings().global_budget_usd,
        browser_concurrency=get_settings().browser_concurrency,
    )
    get_store().settle_usage(
        owner_id=client.get("/api/me", headers=auth(token)).json()["id"],
        reservation_id=reservation,
        actual_cost_usd=0.35,
        input_tokens=100,
        output_tokens=50,
    )
    usage = client.get(f"/api/cases/{case['id']}/usage", headers=auth(token))
    assert usage.status_code == 200
    assert usage.json()["estimated_cost_usd"] == pytest.approx(0.35)
    assert usage.json()["reserved_cost_usd"] == 0
    assert usage.json()["items"][0]["input_tokens"] == 100


def test_usage_limits_reserve_browser_slots_and_budget(client: TestClient):
    first_token = register(client, "Browser One", "browser-one@example.com")
    second_token = register(client, "Browser Two", "browser-two@example.com")
    first_case = client.post(
        "/api/cases", headers=auth(first_token), json={"goal": "First browser task"}
    ).json()
    second_case = client.post(
        "/api/cases", headers=auth(second_token), json={"goal": "Second browser task"}
    ).json()

    from civitas_api.config import get_settings
    from civitas_api.errors import RateLimitError
    from civitas_api.main import get_store

    settings = get_settings()
    first_user = client.get("/api/me", headers=auth(first_token)).json()["id"]
    second_user = client.get("/api/me", headers=auth(second_token)).json()["id"]
    first_reservation = get_store().reserve_usage(
        owner_id=first_user,
        case_id=first_case["id"],
        kind="browser",
        amount_usd=0.10,
        global_budget_usd=settings.global_budget_usd,
        browser_concurrency=1,
    )
    with pytest.raises(RateLimitError):
        get_store().reserve_usage(
            owner_id=second_user,
            case_id=second_case["id"],
            kind="browser",
            amount_usd=0.10,
            global_budget_usd=settings.global_budget_usd,
            browser_concurrency=1,
        )
    get_store().release_usage(owner_id=first_user, reservation_id=first_reservation)

    budget_reservation = get_store().reserve_usage(
        owner_id=first_user,
        case_id=first_case["id"],
        kind="model",
        amount_usd=settings.global_budget_usd,
        global_budget_usd=settings.global_budget_usd,
        browser_concurrency=settings.browser_concurrency,
    )
    with pytest.raises(RateLimitError):
        get_store().reserve_usage(
            owner_id=second_user,
            case_id=second_case["id"],
            kind="model",
            amount_usd=0.01,
            global_budget_usd=settings.global_budget_usd,
            browser_concurrency=settings.browser_concurrency,
        )
    get_store().release_usage(owner_id=first_user, reservation_id=budget_reservation)
