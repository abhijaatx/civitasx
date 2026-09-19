from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from civitas_api.agent import ProviderResult, ReActAgent


class ScriptedProvider:
    """Test-only model transport that exercises the real ReAct dispatcher."""

    name = "test-provider"

    async def stream(self, messages, tools):
        del tools
        latest_user = next(
            (item["content"] for item in reversed(messages) if item.get("role") == "user"),
            "",
        )
        tool_messages = [item for item in messages if item.get("role") == "tool"]
        if "The light is broken." in latest_user:
            yield {
                "kind": "complete",
                "result": ProviderResult(
                    "I still need the locality or nearby landmark before I can prepare the ticket.",
                    [],
                ),
            }
            return
        if "Submit this complaint" in latest_user:
            yield {
                "kind": "complete",
                "result": ProviderResult(
                    "I cannot submit a complaint from this local build. I can prepare it for "
                    "your review.",
                    [],
                ),
            }
            return
        if "What changed between" in latest_user:
            if not tool_messages:
                calls = [
                    {
                        "id": "compare-1",
                        "name": "compare_official_documents",
                        "arguments": {
                            "current_doc_id": "gba-budget-revised-2024-25",
                            "baseline_doc_id": "gba-budget-2024-25",
                        },
                    }
                ]
                text = ""
            else:
                calls = []
                text = (
                    "I compared the two official budget records and attached the page-linked "
                    "changes."
                )
            yield {
                "kind": "complete",
                "result": ProviderResult(text, calls),
            }
            return
        if "broken streetlight near Indiranagar" in latest_user and not tool_messages:
            calls = [
                {
                    "id": "resolve-1",
                    "name": "resolve_ward_and_authority",
                    "arguments": {"location_text": "Indiranagar 12th Main"},
                }
            ]
            text = ""
        elif "broken streetlight near Indiranagar" in latest_user and len(tool_messages) == 1:
            calls = [
                {
                    "id": "draft-1",
                    "name": "draft_complaint_ticket",
                    "arguments": {
                        "title": "Broken streetlight near Indiranagar",
                        "description": latest_user,
                        "locality": "Indiranagar",
                        "authority_id": "gba",
                    },
                }
            ]
            text = ""
        else:
            calls = []
            text = "I could not complete the scripted test turn."
        yield {
            "kind": "complete",
            "result": ProviderResult(text, calls),
        }


class AttachmentAwareFallbackProvider:
    """Fallback-shaped provider that proves preloaded private evidence is usable."""

    name = "codex-cli"

    async def stream(self, messages, tools):
        del tools
        tool_message = next(
            (item for item in reversed(messages) if item.get("role") == "tool"),
            None,
        )
        evidence = json.loads(tool_message["content"]) if tool_message else {}
        attachment = (evidence.get("attachments") or [{}])[0]
        text = attachment.get("text", "")
        yield {
            "kind": "complete",
            "result": ProviderResult(
                f"I read the private report: {text}",
                [],
            ),
        }


@pytest.fixture()
def community_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CIVITAS_AUTH_MODE", "local")
    monkeypatch.setenv("CIVITAS_STORAGE", "sqlite")
    monkeypatch.setenv("CIVITAS_SQLITE_PATH", str(tmp_path / "civitas.sqlite3"))
    monkeypatch.setenv("CIVITAS_FRONTEND_ORIGIN", "http://localhost:5173")

    from civitas_api import config, main

    config.get_settings.cache_clear()
    main.get_store.cache_clear()
    main.get_research_index.cache_clear()
    main.get_live_registry.cache_clear()
    main.get_community_store.cache_clear()
    main.get_agent.cache_clear()
    test_agent = ReActAgent(
        main.get_research_index(),
        main.get_community_store(),
        ScriptedProvider(),
    )
    monkeypatch.setattr(main, "get_agent", lambda: test_agent)
    with TestClient(main.app) as client:
        yield client
    main.get_store.cache_clear()
    main.get_research_index.cache_clear()
    main.get_live_registry.cache_clear()
    main.get_community_store.cache_clear()
    getattr(main.get_agent, "cache_clear", lambda: None)()
    config.get_settings.cache_clear()


def register(client: TestClient, name: str, email: str) -> str:
    response = client.post(
        "/api/auth/register",
        json={"name": name, "email": email, "password": "correct horse battery staple"},
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_feed_is_seeded_and_votes_comments_are_reversible(community_client: TestClient):
    token = register(community_client, "Feed Resident", "feed@example.com")
    auth = headers(token)

    response = community_client.get("/api/feed", headers=auth)
    assert response.status_code == 200, response.text
    posts = response.json()["items"]
    assert len(posts) == 3
    assert posts[0]["civitas_ticket_id"].startswith("CX-BLR-DEMO-")
    assert posts[0]["vote_score"] > 0
    assert posts[0]["status"] in {"in_progress", "acknowledged", "not_solved"}
    assert posts[0]["author_id"] is None
    assert posts[0]["is_owner"] is False
    for seeded_post in posts:
        evidence = community_client.get(
            f"/api/feed/{seeded_post['id']}/evidence", headers=auth
        )
        assert evidence.status_code == 200
        assert seeded_post["evidence_count"] == len(evidence.json()["items"])

    post_id = posts[0]["id"]
    upvote = community_client.post(
        f"/api/feed/{post_id}/vote", headers=auth, json={"value": 1}
    )
    assert upvote.status_code == 200
    assert upvote.json()["value"] == 1
    clear_vote = community_client.post(
        f"/api/feed/{post_id}/vote", headers=auth, json={"value": 0}
    )
    assert clear_vote.status_code == 200
    assert clear_vote.json()["value"] == 0

    follow = community_client.post(
        f"/api/feed/{post_id}/follow", headers=auth, json={"following": True}
    )
    assert follow.status_code == 200
    following = community_client.get(
        "/api/feed?sort=following", headers=auth
    ).json()["items"]
    assert any(item["id"] == post_id and item["is_following"] for item in following)
    unfollow = community_client.post(
        f"/api/feed/{post_id}/follow", headers=auth, json={"following": False}
    )
    assert unfollow.json()["following"] is False

    comment = community_client.post(
        f"/api/feed/{post_id}/comments",
        headers=auth,
        json={"body": "I can add a photo from this week."},
    )
    assert comment.status_code == 201
    assert comment.json()["author_name"] == "Feed Resident"
    assert comment.json()["author_id"] is None
    comments = community_client.get(f"/api/feed/{post_id}/comments", headers=auth)
    assert len(comments.json()["items"]) >= 2


def test_blank_inputs_and_unscoped_nearby_feed_are_rejected(community_client: TestClient):
    token = register(community_client, "Validation Resident", "validation@example.com")
    auth = headers(token)
    post_id = community_client.get("/api/feed", headers=auth).json()["items"][0]["id"]

    blank_comment = community_client.post(
        f"/api/feed/{post_id}/comments", headers=auth, json={"body": "   "}
    )
    blank_ticket = community_client.post(
        "/api/tickets",
        headers=auth,
        json={"title": "   ", "description": "          ", "authority_id": "gba"},
    )
    blank_follow = community_client.post(
        "/api/subjects/follows",
        headers=auth,
        json={"subject_type": "topic", "value": "   ", "following": True},
    )
    nearby = community_client.get("/api/feed?sort=nearby", headers=auth)

    assert blank_comment.status_code == 422
    assert blank_ticket.status_code == 422
    assert blank_follow.status_code == 422
    assert nearby.status_code == 422


def test_detached_ticket_cannot_claim_unowned_preparation_attachment(
    community_client: TestClient,
):
    token = register(community_client, "Attachment Resident", "attachment@example.com")
    auth = headers(token)
    ticket = community_client.post(
        "/api/tickets",
        headers=auth,
        json={
            "title": "Pothole by Jayanagar crossing",
            "description": "A deep pothole is affecting the crossing near the bus stop.",
            "authority_id": "gba",
            "locality": "Jayanagar",
        },
    ).json()["ticket"]

    response = community_client.post(
        f"/api/tickets/{ticket['id']}/prepare",
        headers=auth,
        json={
            "fields": {
                "description": ticket["description"],
                "locality": ticket["locality"],
            },
            "attachment_ids": ["not-owned-attachment"],
        },
    )

    assert response.status_code == 409


def test_deleted_comment_parent_cannot_receive_new_replies(community_client: TestClient):
    token = register(community_client, "Comment Resident", "comment@example.com")
    auth = headers(token)
    post_id = community_client.get("/api/feed", headers=auth).json()["items"][0]["id"]
    parent = community_client.post(
        f"/api/feed/{post_id}/comments", headers=auth, json={"body": "Parent context"}
    ).json()
    assert community_client.delete(
        f"/api/feed/{post_id}/comments/{parent['id']}", headers=auth
    ).status_code == 204

    reply = community_client.post(
        f"/api/feed/{post_id}/comments",
        headers=auth,
        json={"body": "Reply after deletion", "parent_id": parent["id"]},
    )

    assert reply.status_code == 404


def test_agent_clarifies_then_creates_private_ticket_and_can_publish_redacted_post(
    community_client: TestClient,
):
    token = register(community_client, "Agent Resident", "agent@example.com")
    auth = headers(token)

    thread = community_client.post(
        "/api/agent/threads",
        headers=auth,
        json={"title": "Streetlight issue", "goal": "Prepare a civic complaint"},
    )
    assert thread.status_code == 201, thread.text
    thread_id = thread.json()["thread"]["id"]

    clarification = community_client.post(
        f"/api/agent/threads/{thread_id}/messages",
        headers=auth,
        json={"content": "The light is broken."},
    )
    assert clarification.status_code == 200
    assert "still need" in clarification.json()["messages"][-1]["content"].casefold()

    ready = community_client.post(
        f"/api/agent/threads/{thread_id}/messages",
        headers=auth,
        json={
            "content": "There is a broken streetlight near Indiranagar 12th Main after 7 pm.",
        },
    )
    assert ready.status_code == 200
    action = next(
        part
        for part in ready.json()["messages"][-1]["parts"]
        if part["type"] == "action" and part["data"].get("action") == "create_ticket"
    )
    assert action["data"]["description"].count("broken streetlight") == 1
    ticket = community_client.post(
        "/api/tickets",
        headers=auth,
        json={
            "thread_id": thread_id,
            "title": action["data"]["title"],
            "description": action["data"]["description"],
            "locality": action["data"]["locality"],
            "authority_id": action["data"]["authority_id"],
        },
    )
    assert ticket.status_code == 201, ticket.text
    detail = ticket.json()
    assert detail["ticket"]["civitas_ticket_id"].startswith("CX-BLR-")
    assert detail["ticket"]["status"] == "draft"
    assert len(detail["history"]) == 1
    brief = community_client.get(
        f"/api/tickets/{detail['ticket']['id']}/export", headers=auth
    )
    assert brief.status_code == 200
    assert detail["ticket"]["civitas_ticket_id"] in brief.text
    assert "STATUS HISTORY" in brief.text

    blocked = community_client.post(
        f"/api/tickets/{detail['ticket']['id']}/publish",
        headers=auth,
        json={
            "title": "Streetlight near Indiranagar",
            "body": "Publicly shared description for neighbours to review.",
            "locality": "Indiranagar",
            "visibility": "locality",
        },
    )
    assert blocked.status_code == 412

    published = community_client.post(
        f"/api/tickets/{detail['ticket']['civitas_ticket_id']}/publish",
        headers=auth,
        json={
            "title": "Streetlight near Indiranagar",
            "body": "Publicly shared description for neighbours to review.",
            "locality": "Indiranagar",
            "visibility": "locality",
            "redaction_approved": True,
        },
    )
    assert published.status_code == 201, published.text
    assert published.json()["civitas_ticket_id"] == detail["ticket"]["civitas_ticket_id"]
    assert published.json()["author_id"] is None
    assert published.json()["is_owner"] is True

    status_response = community_client.patch(
        f"/api/tickets/{detail['ticket']['id']}/status",
        headers=auth,
        json={"status": "in_progress", "note": "Authority route confirmed"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["ticket"]["status"] == "in_progress"
    public = community_client.get("/api/feed", headers=auth).json()["items"]
    created_post = next(item for item in public if item["ticket_id"] == detail["ticket"]["id"])
    assert created_post["status"] == "in_progress"


def test_attachments_and_private_records_are_owner_scoped(community_client: TestClient):
    first = register(community_client, "First Resident", "attachment-first@example.com")
    second = register(community_client, "Second Resident", "attachment-second@example.com")
    first_auth = headers(first)
    second_auth = headers(second)
    thread = community_client.post(
        "/api/agent/threads", headers=first_auth, json={"goal": "Attach evidence"}
    ).json()
    thread_id = thread["thread"]["id"]

    upload = community_client.post(
        f"/api/agent/threads/{thread_id}/attachments",
        headers=first_auth,
        files={"file": ("evidence.txt", b"lamp photo note", "text/plain")},
    )
    assert upload.status_code == 201, upload.text
    attachment = upload.json()
    assert attachment["size_bytes"] == len(b"lamp photo note")
    download = community_client.get(
        f"/api/agent/attachments/{attachment['id']}", headers=first_auth
    )
    assert download.status_code == 200
    assert download.content == b"lamp photo note"
    assert (
        community_client.get(
            f"/api/agent/attachments/{attachment['id']}", headers=second_auth
        ).status_code
        == 404
    )

    other_thread = community_client.post(
        "/api/agent/threads", headers=first_auth, json={"goal": "Another conversation"}
    ).json()["thread"]["id"]
    cross_thread = community_client.post(
        f"/api/agent/threads/{other_thread}/messages",
        headers=first_auth,
        json={"content": "Reuse this file", "attachment_ids": [attachment["id"]]},
    )
    assert cross_thread.status_code == 404

    ticket = community_client.post(
        "/api/tickets",
        headers=first_auth,
        json={
            "title": "Private evidence ticket",
            "description": "A private ticket with an attached local record.",
            "locality": "Jayanagar",
        },
    ).json()["ticket"]
    assert (
        community_client.get(f"/api/tickets/{ticket['id']}", headers=second_auth).status_code
        == 404
    )


def test_followers_receive_public_interaction_notifications(community_client: TestClient):
    owner = register(community_client, "Post Owner", "post-owner@example.com")
    follower = register(community_client, "Post Follower", "post-follower@example.com")
    owner_auth = headers(owner)
    follower_auth = headers(follower)
    ticket = community_client.post(
        "/api/tickets",
        headers=owner_auth,
        json={
            "title": "A public neighbourhood issue",
            "description": (
                "A public issue that neighbours can follow and discuss. "
                "Contact resident@example.com or 9876543210."
            ),
            "locality": "Jayanagar",
        },
    ).json()["ticket"]
    post = community_client.post(
        f"/api/tickets/{ticket['id']}/publish",
        headers=owner_auth,
        json={
            "title": "A public neighbourhood issue",
            "body": (
                "A public issue that neighbours can follow and discuss. "
                "Contact resident@example.com or 9876543210."
            ),
            "locality": "Jayanagar",
            "visibility": "locality",
            "redaction_approved": True,
        },
    ).json()
    assert "[redacted email]" in post["body"]
    assert "[redacted phone]" in post["body"]
    post_id = post["id"]
    community_client.post(
        f"/api/feed/{post_id}/follow", headers=follower_auth, json={"following": True}
    )
    community_client.post(
        f"/api/feed/{post_id}/vote", headers=follower_auth, json={"value": 1}
    )
    community_client.post(
        f"/api/feed/{post_id}/comments",
        headers=follower_auth,
        json={"body": "Adding local context for the owner."},
    )
    notifications = community_client.get("/api/notifications", headers=owner_auth)
    assert notifications.status_code == 200
    kinds = {item["kind"] for item in notifications.json()["items"]}
    assert {"post_followed", "post_voted", "post_commented"}.issubset(kinds)


def test_connector_registry_explains_local_preparation_without_enabling_submission(
    community_client: TestClient,
):
    token = register(community_client, "Connector Resident", "connector@example.com")
    auth = headers(token)
    connectors = community_client.get("/api/connectors", headers=auth)
    assert connectors.status_code == 200
    items = connectors.json()["items"]
    assert {item["authority_id"] for item in items} == {"gba", "bda", "bmrcl"}
    assert all(item["provider"] == "local_fixture" for item in items)
    assert all(item["submission_enabled"] is False for item in items)
    assert any(field["key"] == "locality" for field in items[0]["requirements"])


def test_live_connector_registry_reports_public_sources_and_missing_keys(
    community_client: TestClient,
):
    token = register(community_client, "Live Source Resident", "live@example.com")
    auth = headers(token)
    response = community_client.get("/api/live/connectors", headers=auth)
    assert response.status_code == 200
    items = response.json()["items"]
    assert {item["endpoint_id"] for item in items} >= {
        "gba-home",
        "bda-town-planning",
        "bmrcl-home",
        "karnataka-open-data-portal",
    }
    data_gov = next(item for item in items if item["endpoint_id"] == "data-gov-in-api")
    assert data_gov["status"] == "requires_api_key"
    refresh = community_client.post(
        "/api/live/connectors/data-gov-in-api/refresh", headers=auth
    )
    assert refresh.status_code == 200
    assert refresh.json()["fetched"] is False
    assert "CIVITAS_DATA_GOV_API_KEY" in refresh.json()["message"]
    assert len(items) >= 30
    api_setu = next(item for item in items if item["endpoint_id"] == "api-setu-discovery")
    assert api_setu["priority"] == "P0"
    digilocker = next(
        item for item in items if item["endpoint_id"] == "digilocker-document-services"
    )
    assert digilocker["status"] == "consent_required"
    open_data = community_client.get(
        "/api/live/open-data/search?resource_id=12345678", headers=auth
    )
    assert open_data.status_code == 403
    assert "CIVITAS_DATA_GOV_API_KEY" in open_data.json()["detail"]


def test_local_ranking_controls_reports_and_moderation_queue(community_client: TestClient):
    token = register(community_client, "Ranking Resident", "ranking@example.com")
    auth = headers(token)
    posts = community_client.get("/api/feed", headers=auth).json()["items"]
    assert posts[0]["ranking_score"] is not None
    assert posts[0]["ranking_reasons"]
    post_id = posts[0]["id"]
    assert community_client.post(
        f"/api/feed/{post_id}/save", headers=auth, json={"saved": True}
    ).json()["saved"]
    assert community_client.post(
        f"/api/feed/{post_id}/mute", headers=auth, json={"muted": True}
    ).json()["muted"]
    visible_after_mute = community_client.get("/api/feed", headers=auth).json()["items"]
    assert all(item["id"] != post_id for item in visible_after_mute)
    report = community_client.post(
        f"/api/feed/{post_id}/report",
        headers=auth,
        json={"reason": "spam", "details": "Duplicate fixture"},
    )
    assert report.status_code == 201

    moderator = register(community_client, "Moderator", "moderator@civitas.local")
    moderator_auth = headers(moderator)
    queue = community_client.get("/api/moderation/reports", headers=moderator_auth)
    assert queue.status_code == 200
    assert queue.json()["items"][0]["target_id"] == post_id
    action = community_client.post(
        f"/api/moderation/post/{post_id}",
        headers=moderator_auth,
        json={"action": "hide", "note": "Duplicate fixture"},
    )
    assert action.status_code == 200
    assert community_client.get("/api/feed", headers=auth).status_code == 200
    audit = community_client.get("/api/moderation/actions", headers=moderator_auth)
    assert audit.status_code == 200
    assert audit.json()["items"][0]["action"] == "hide"
    forbidden = community_client.get("/api/moderation/reports", headers=auth)
    assert forbidden.status_code == 403


def test_preparation_hash_checkpoint_outcome_share_and_compare(community_client: TestClient):
    token = register(community_client, "Review Resident", "review@example.com")
    auth = headers(token)
    ticket = community_client.post(
        "/api/tickets",
        headers=auth,
        json={
            "title": "Pothole by Jayanagar crossing",
            "description": "A deep pothole is affecting the crossing near the bus stop.",
            "authority_id": "gba",
            "locality": "Jayanagar",
        },
    ).json()["ticket"]
    preparation = community_client.post(
        f"/api/tickets/{ticket['id']}/prepare", headers=auth, json={}
    )
    assert preparation.status_code == 200, preparation.text
    prepared = preparation.json()
    assert prepared["status"] == "ready_for_review"
    approved = community_client.post(
        f"/api/tickets/{ticket['id']}/preparation/approve",
        headers=auth,
        json={"content_hash": prepared["content_hash"]},
    )
    assert approved.status_code == 200
    assert approved.json()["submission_enabled"] is False
    blocked = community_client.post(f"/api/tickets/{ticket['id']}/submit", headers=auth)
    assert blocked.status_code == 501
    outcome = community_client.post(
        f"/api/tickets/{ticket['id']}/outcome",
        headers=auth,
        json={"status": "outcome_unknown", "note": "The portal timed out."},
    )
    assert outcome.status_code == 200
    checkpoints = community_client.get(
        f"/api/tickets/{ticket['id']}/checkpoints", headers=auth
    )
    assert len(checkpoints.json()["items"]) >= 2

    compare = community_client.post(
        "/api/sources/compare",
        headers=auth,
        json={
            "source_id": "gba-budget-revised-2024-25",
            "baseline_source_id": "gba-budget-2024-25",
        },
    )
    assert compare.status_code == 200
    assert compare.json()["changes"]

    feed_post = community_client.get("/api/feed", headers=auth).json()["items"][0]
    share = community_client.post(
        f"/api/feed/{feed_post['id']}/share", headers=auth
    )
    assert share.status_code == 200
    public = community_client.get(share.json()["url"])
    assert public.status_code == 200
    assert "civitas_ticket_id" in public.json()
    revoked = community_client.delete(
        f"/api/shares/{share.json()['token']}", headers=auth
    )
    assert revoked.status_code == 200
    assert community_client.get(share.json()["url"]).status_code == 404


def test_agent_action_boundary_and_sse_shape(community_client: TestClient):
    token = register(community_client, "Stream Resident", "stream@example.com")
    auth = headers(token)
    thread = community_client.post(
        "/api/agent/threads", headers=auth, json={"goal": "Submit a civic issue"}
    ).json()["thread"]["id"]
    response = community_client.post(
        f"/api/agent/threads/{thread}/messages",
        headers=auth,
        json={"content": "Submit this complaint to the authority."},
    )
    assert response.status_code == 200
    assert "cannot submit" in response.json()["messages"][-1]["content"].casefold()
    streamed = community_client.post(
        f"/api/agent/threads/{thread}/messages/stream",
        headers=auth,
        json={"content": "What does the official record say about pedestrian safety?"},
    )
    assert streamed.status_code == 200
    assert "event: done" in streamed.text


def test_react_sse_emits_dispatcher_owned_tool_events(community_client: TestClient):
    token = register(community_client, "ReAct Resident", "react@example.com")
    auth = headers(token)
    thread = community_client.post(
        "/api/agent/threads", headers=auth, json={"goal": "Prepare a streetlight complaint"}
    ).json()["thread"]["id"]
    streamed = community_client.post(
        f"/api/agent/threads/{thread}/messages/stream",
        headers=auth,
        json={"content": "There is a broken streetlight near Indiranagar 12th Main after 7 pm."},
    )
    assert streamed.status_code == 200
    assert "event: tool_call" in streamed.text
    assert "event: tool_result" in streamed.text
    assert "resolve_ward_and_authority" in streamed.text
    assert "event: message" in streamed.text
    assert "event: done" in streamed.text
    assert "event: plan" in streamed.text


def test_agent_rehydrates_tool_calls_and_results_after_reload(community_client: TestClient):
    token = register(community_client, "History Resident", "history@example.com")
    auth = headers(token)
    thread = community_client.post(
        "/api/agent/threads", headers=auth, json={"goal": "Compare budget versions"}
    ).json()["thread"]["id"]
    response = community_client.post(
        f"/api/agent/threads/{thread}/messages",
        headers=auth,
        json={"content": "What changed between the budget and revised budget?"},
    )
    assert response.status_code == 200, response.text
    from civitas_api import main

    owner_id = community_client.get("/api/me", headers=auth).json()["id"]
    messages = main.get_agent()._messages(owner_id, thread)
    assert any(item.get("role") == "assistant" and item.get("tool_calls") for item in messages)
    assert any(item.get("role") == "tool" for item in messages)


@pytest.mark.asyncio
async def test_private_attachment_tool_extracts_thread_scoped_text(community_client: TestClient):
    token = register(community_client, "Attachment Resident", "attachment@example.com")
    auth = headers(token)
    thread = community_client.post(
        "/api/agent/threads", headers=auth, json={"goal": "Inspect evidence"}
    ).json()["thread"]["id"]
    uploaded = community_client.post(
        f"/api/agent/threads/{thread}/attachments",
        headers=auth,
        files={"file": ("notes.txt", b"The streetlight failed after 7 pm.", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment_id = uploaded.json()["id"]
    from civitas_api import main

    owner_id = community_client.get("/api/me", headers=auth).json()["id"]
    result = await main.get_agent().tools.execute(
        "inspect_private_attachments",
        {"attachment_ids": [attachment_id]},
        owner_id=owner_id,
        thread_id=thread,
    )
    assert result["status"] == "ok"
    assert "streetlight failed" in result["attachments"][0]["text"]


def test_private_attachment_is_preloaded_for_fallback_provider(community_client: TestClient):
    token = register(community_client, "Fallback Resident", "fallback@example.com")
    auth = headers(token)
    thread = community_client.post(
        "/api/agent/threads", headers=auth, json={"goal": "Inspect evidence"}
    ).json()["thread"]["id"]
    uploaded = community_client.post(
        f"/api/agent/threads/{thread}/attachments",
        headers=auth,
        files={
            "file": (
                "report.txt",
                b"Reported date: 19 September 2026; locality: Indiranagar.",
                "text/plain",
            )
        },
    )
    assert uploaded.status_code == 201, uploaded.text

    from civitas_api import main

    fallback_agent = ReActAgent(
        main.get_research_index(),
        main.get_community_store(),
        AttachmentAwareFallbackProvider(),
        grounding_verification=False,
    )
    original_get_agent = main.get_agent
    try:
        main.get_agent = lambda: fallback_agent
        response = community_client.post(
            f"/api/agent/threads/{thread}/messages/stream",
            headers=auth,
            json={
                "content": "Read the attached report and tell me what it says.",
                "attachment_ids": [uploaded.json()["id"]],
            },
        )
    finally:
        main.get_agent = original_get_agent
    assert response.status_code == 200, response.text
    assert "19 September 2026" in response.text
    assert "event: tool_call" in response.text
    assert "inspect_private_attachments" in response.text


def test_agent_can_explain_what_changed_with_page_linked_comparison(community_client: TestClient):
    token = register(community_client, "Compare Resident", "compare@example.com")
    auth = headers(token)
    thread = community_client.post(
        "/api/agent/threads", headers=auth, json={"goal": "Compare budget versions"}
    ).json()["thread"]["id"]
    response = community_client.post(
        f"/api/agent/threads/{thread}/messages",
        headers=auth,
        json={"content": "What changed between the budget and revised budget?"},
    )
    assert response.status_code == 200
    message = response.json()["messages"][-1]
    comparison = next(
        part
        for part in message["parts"]
        if part["data"].get("action") == "document_comparison"
    )
    assert comparison["data"]["comparison"]["changes"]
