from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from civitas_api.community_store import LocalCommunityStore
from civitas_api.models import AgentMessageRole, TicketVisibility


def test_ticket_numbers_are_monotonic_under_concurrency_and_deletes(tmp_path):
    store = LocalCommunityStore(tmp_path / "community.sqlite3")
    try:
        def create(index: int):
            return store.create_ticket(
                f"owner-{index}",
                title=f"Issue {index}",
                description="A sufficiently detailed civic issue description.",
                visibility=TicketVisibility.PRIVATE,
            ).ticket.civitas_ticket_id

        with ThreadPoolExecutor(max_workers=8) as executor:
            ids = list(executor.map(create, range(24)))
        assert len(ids) == len(set(ids))
        deleted = store.create_ticket(
            "owner-delete",
            title="Deleted issue",
            description="A sufficiently detailed civic issue description.",
        ).ticket
        store._conn.execute("DELETE FROM complaint_tickets WHERE id=?", (deleted.id,))
        next_ticket = store.create_ticket(
            "owner-next",
            title="Next issue",
            description="A sufficiently detailed civic issue description.",
        ).ticket
        assert int(next_ticket.civitas_ticket_id.rsplit("-", 1)[1]) > max(
            int(ticket_id.rsplit("-", 1)[1]) for ticket_id in ids
        )
    finally:
        store.close()


def test_feed_ranks_all_candidates_before_paging(tmp_path):
    store = LocalCommunityStore(tmp_path / "community.sqlite3")
    try:
        important = store.create_ticket(
            "important-owner",
            title="Important unresolved issue",
            description="A sufficiently detailed civic issue description.",
        ).ticket
        important_post = store.publish_ticket(
            "important-owner",
            important.id,
            title="Important unresolved issue",
            body="A public description with strong community evidence.",
            locality="Indiranagar",
            visibility="locality",
            author_name="Resident",
        )
        for index in range(40):
            ticket = store.create_ticket(
                f"recent-owner-{index}",
                title=f"Recent issue {index}",
                description="A sufficiently detailed civic issue description.",
            ).ticket
            store.publish_ticket(
                f"recent-owner-{index}",
                ticket.id,
                title=f"Recent issue {index}",
                body="A recent public issue.",
                locality="Indiranagar",
                visibility="locality",
                author_name=f"Recent {index}",
            )
        for index in range(500):
            store.vote_post(f"voter-{index}", important_post.id, 1)
        page, next_cursor = store.list_feed_page("viewer", sort="recommended", limit=1)
        assert page[0].id == important_post.id
        assert next_cursor is not None
    finally:
        store.close()


def test_comment_counts_and_erasure_remain_reversible(tmp_path):
    store = LocalCommunityStore(tmp_path / "community.sqlite3")
    try:
        posts = store.list_feed("viewer")
        post = posts[0]
        comment = store.create_comment("owner", post.id, "Resident", "A useful local note")
        assert store.get_post(post.id, "viewer").comment_count == post.comment_count + 1
        old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        store._conn.execute("UPDATE post_comments SET created_at=? WHERE id=?", (old, comment.id))
        edited = store.edit_comment("owner", comment.id, "The corrected local note")
        assert edited.body == "The corrected local note"
        store.delete_comment("owner", comment.id)
        assert store.get_post(post.id, "viewer").comment_count == post.comment_count
        assert all(item.id != comment.id for item in store.list_comments(post.id, "viewer"))
    finally:
        store.close()


def test_comment_moderation_updates_count_in_both_directions(tmp_path):
    store = LocalCommunityStore(tmp_path / "community.sqlite3")
    try:
        post = store.list_feed("viewer")[0]
        comment = store.create_comment("owner", post.id, "Resident", "A useful local note")
        counted = store.get_post(post.id, "viewer").comment_count
        store.moderate("moderator", "comment", comment.id, "hide")
        assert store.get_post(post.id, "viewer").comment_count == counted - 1
        store.moderate("moderator", "comment", comment.id, "restore")
        assert store.get_post(post.id, "viewer").comment_count == counted
    finally:
        store.close()


def test_public_evidence_is_a_publication_snapshot(tmp_path):
    store = LocalCommunityStore(tmp_path / "community.sqlite3")
    try:
        thread = store.create_thread("owner", "Civic issue")
        store.add_message(
            "owner",
            thread.id,
            AgentMessageRole.ASSISTANT,
            "First source",
            [
                {
                    "type": "citation",
                    "text": "First source",
                    "data": {
                        "source_id": "source-1",
                        "title": "First source",
                        "authority": "GBA",
                        "status": "adopted",
                        "passage": "Public passage",
                    },
                }
            ],
        )
        ticket = store.create_ticket(
            "owner",
            title="Civic issue",
            description="A sufficiently detailed civic issue description.",
            thread_id=thread.id,
        ).ticket
        post = store.publish_ticket(
            "owner",
            ticket.id,
            title="Civic issue",
            body="A public description.",
            locality="Indiranagar",
            visibility="locality",
            author_name="Resident",
        )
        store.add_message(
            "owner",
            thread.id,
            AgentMessageRole.ASSISTANT,
            "Private follow-up",
            [
                {
                    "type": "citation",
                    "data": {
                        "source_id": "private-source",
                        "title": "Private source",
                        "authority": "GBA",
                        "status": "unknown",
                        "passage": "Private passage",
                    },
                }
            ],
        )
        assert [item.source_id for item in store.list_post_evidence(post.id)] == ["source-1"]
        assert post.evidence_count == 1
    finally:
        store.close()
