from __future__ import annotations

import boto3
from moto import mock_aws

from civitas_api.dynamo_community_store import DynamoCommunityStore
from civitas_api.models import AgentMessageRole, TicketVisibility


def _store() -> DynamoCommunityStore:
    resource = boto3.resource("dynamodb", region_name="ap-south-1")
    resource.create_table(
        TableName="civitasx-community-test",
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    return DynamoCommunityStore("civitasx-community-test", "ap-south-1")


def test_dynamo_community_uses_monotonic_tickets_and_evidence_snapshots():
    with mock_aws():
        store = _store()
        thread = store.create_thread("user-1", "Streetlight issue")
        store.add_message(
            "user-1",
            thread.id,
            AgentMessageRole.ASSISTANT,
            "Official record",
            [
                {
                    "type": "citation",
                    "text": "Source one",
                    "data": {
                        "source_id": "source-1",
                        "title": "Source one",
                        "authority": "GBA",
                        "status": "adopted",
                        "passage": "A page",
                    },
                }
            ],
        )
        first = store.create_ticket(
            "user-1",
            title="Broken streetlight",
            description="A streetlight is out near the junction.",
            visibility=TicketVisibility.PRIVATE,
            thread_id=thread.id,
        )
        second = store.create_ticket(
            "user-1",
            title="Another issue",
            description="A second civic issue for sequence coverage.",
        )
        assert first.ticket.civitas_ticket_id.endswith("0001")
        assert second.ticket.civitas_ticket_id.endswith("0002")

        post = store.publish_ticket(
            "user-1",
            first.ticket.id,
            title="Broken streetlight",
            body="Neighbours should know about this issue.",
            locality="Indiranagar",
            visibility="locality",
            author_name="Resident",
        )
        assert post.evidence_count == 1
        assert [item.source_id for item in store.list_post_evidence(post.id)] == ["source-1"]

        store.add_message(
            "user-1",
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
