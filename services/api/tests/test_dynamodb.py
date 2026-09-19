from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from civitas_api.errors import NotFoundError
from civitas_api.store import DynamoStore


@pytest.fixture()
def dynamo_store(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="ap-south-1")
        resource.create_table(
            TableName="civitasx-test",
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
        yield DynamoStore("civitasx-test", "ap-south-1")


def test_dynamo_case_artifact_event_and_usage(dynamo_store: DynamoStore):
    case = dynamo_store.create_case("user-1", "Understand the city budget")
    assert dynamo_store.get_case("user-1", case.id).goal == "Understand the city budget"
    assert dynamo_store.list_cases("user-1")[0].id == case.id
    assert dynamo_store.list_cases("user-2") == []

    artifact = dynamo_store.create_artifact("user-1", case.id, "notes.txt", "hello", "note")
    assert dynamo_store.get_artifact("user-1", case.id, artifact.id).content == "hello"
    with pytest.raises(NotFoundError):
        dynamo_store.list_artifacts("user-2", case.id)

    reservation = dynamo_store.reserve_usage(
        owner_id="user-1",
        case_id=case.id,
        kind="model",
        amount_usd=1.0,
        global_budget_usd=80,
        browser_concurrency=2,
    )
    dynamo_store.settle_usage(
        owner_id="user-1",
        reservation_id=reservation,
        actual_cost_usd=0.25,
        input_tokens=5,
        output_tokens=3,
    )
    usage = dynamo_store.get_usage("user-1", case.id)
    assert usage.estimated_cost_usd == pytest.approx(0.25)
    assert usage.items[0].input_tokens == 5
