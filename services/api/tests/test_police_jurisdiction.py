from pathlib import Path

import pytest

from civitas_api.agent import ReActAgent
from civitas_api.civic_tools import CivicToolRuntime
from civitas_api.community_store import LocalCommunityStore
from civitas_api.models import AgentMessageRole
from civitas_api.police_jurisdiction import PoliceStationRegistry
from civitas_api.research import ResearchIndex


def test_registry_resolves_hsr_and_microsoft_landmark_records():
    registry = PoliceStationRegistry()

    hsr = registry.resolve("HSR Layout sector 2")
    assert hsr["status"] == "ok"
    assert hsr["best_match"]["name"] == "HSR Layout Police Station"
    assert hsr["best_match"]["address"].startswith("27th Main Road")

    microsoft = registry.resolve("near Microsoft's office")
    assert microsoft["status"] == "ok"
    assert microsoft["best_match"]["name"] == "Bellanduru Police Station"
    assert "microsoft office" in microsoft["best_match"]["match_basis"]


def test_registry_returns_ranked_candidates_for_boundary_ambiguity():
    result = PoliceStationRegistry().resolve("Microsoft office near HSR Layout")

    assert result["status"] == "ambiguous"
    assert result["needs_confirmation"] is True
    assert [candidate["name"] for candidate in result["candidates"][:2]] == [
        "Bellanduru Police Station",
        "HSR Layout Police Station",
    ]
    assert result["clarifying_question"]


def test_registry_has_actionable_fallback_when_no_record_matches():
    result = PoliceStationRegistry().resolve("an unnamed road in Bengaluru")

    assert result["status"] == "needs_clarification"
    assert result["best_match"] is None
    assert result["fallback"]["guidance"]
    assert result["fallback"]["source_url"]


@pytest.mark.asyncio
async def test_police_tool_returns_structured_station_evidence():
    index = ResearchIndex(Path(__file__).parents[1] / "data" / "corpus" / "manifest.json")
    tools = CivicToolRuntime(index, community=None)  # type: ignore[arg-type]

    result = await tools.execute(
        "resolve_police_jurisdiction",
        {"location_text": "near Microsoft's office"},
    )

    assert result["status"] == "ok"
    assert result["best_match"]["station_id"] == "bengaluru-bellanduru"
    assert result["provenance"]["dataset_id"] == "bengaluru-police-station-routing"


def test_preflight_routes_police_location_questions_to_dedicated_tool():
    agent = object.__new__(ReActAgent)
    agent.index = ResearchIndex(Path(__file__).parents[1] / "data" / "corpus" / "manifest.json")

    calls = agent._deterministic_preflight_calls(
        "My phone was stolen near Microsoft's office"
    )

    assert calls == [
        (
            "resolve_police_jurisdiction",
            {"location_text": "Microsoft's office"},
        )
    ]


def test_preflight_keeps_full_address_after_nearest_station_phrase():
    agent = object.__new__(ReActAgent)
    agent.index = ResearchIndex(Path(__file__).parents[1] / "data" / "corpus" / "manifest.json")

    address = (
        "Prestige Ferns Galaxy, Sy. No. 7/1, 7/2, 8/1A in Ambalipura, "
        "Varthur Hobli, Outer Ring Road, Bengaluru 560103"
    )
    calls = agent._deterministic_preflight_calls(
        f"what police station is nearest to {address}?"
    )

    assert calls == [("resolve_police_jurisdiction", {"location_text": address})]


def test_location_only_follow_up_reuses_prior_police_intent():
    agent = object.__new__(ReActAgent)
    agent.index = ResearchIndex(Path(__file__).parents[1] / "data" / "corpus" / "manifest.json")

    address = "Prestige Ferns Galaxy, Ambalipura, Varthur Hobli, Bengaluru 560103"
    calls = agent._deterministic_preflight_calls(
        address,
        prior_police_request="what police station is nearest to this address?",
    )

    assert calls == [("resolve_police_jurisdiction", {"location_text": address})]


def test_police_answer_renderer_does_not_delegate_station_name_to_model():
    answer = ReActAgent._render_police_jurisdiction(
        [
            {
                "type": "tool",
                "data": {
                    "tool_name": "resolve_police_jurisdiction",
                    "result": PoliceStationRegistry().resolve("HSR Layout sector 2"),
                },
            }
        ]
    )

    assert answer is not None
    assert "HSR Layout Police Station" in answer
    assert "27th Main Road" in answer


@pytest.mark.asyncio
async def test_agent_preflight_returns_station_without_model_guessing(tmp_path: Path):
    class ProviderMustNotGuess:
        name = "test-provider"

        async def stream(self, messages, tools):
            del messages, tools
            raise AssertionError("the structured police preflight should answer this turn")
            yield  # pragma: no cover

    store = LocalCommunityStore(tmp_path / "community.sqlite3")
    try:
        owner_id = "police-resident"
        thread = store.create_thread(owner_id, "Stolen phone")
        store.add_message(
            owner_id,
            thread.id,
            AgentMessageRole.USER,
            "My phone was stolen in HSR Layout. Which police station should I go to?",
        )
        index = ResearchIndex(Path(__file__).parents[1] / "data" / "corpus" / "manifest.json")
        agent = ReActAgent(index, store, ProviderMustNotGuess(), grounding_verification=False)

        events = [
            event
            async for event in agent.stream_turn(owner_id=owner_id, thread_id=thread.id)
        ]
        final = next(event for event in events if event.get("kind") == "final")
        assert "HSR Layout Police Station" in final["content"]
        assert any(
            event.get("kind") == "tool_call"
            and event.get("data", {}).get("tool_name") == "resolve_police_jurisdiction"
            for event in events
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_address_follow_up_after_unresolved_turn_returns_station(tmp_path: Path):
    class ProviderMustNotGuess:
        name = "test-provider"

        async def stream(self, messages, tools):
            del messages, tools
            raise AssertionError(
                "the follow-up should be handled by the structured police preflight"
            )
            yield  # pragma: no cover

    store = LocalCommunityStore(tmp_path / "community.sqlite3")
    try:
        owner_id = "police-follow-up-resident"
        thread = store.create_thread(owner_id, "Police station lookup")
        store.add_message(
            owner_id,
            thread.id,
            AgentMessageRole.USER,
            "what police station is nearest to this address?",
        )
        store.add_message(
            owner_id,
            thread.id,
            AgentMessageRole.ASSISTANT,
            "Please share the full address.",
        )
        store.add_message(
            owner_id,
            thread.id,
            AgentMessageRole.USER,
            "Prestige Ferns Galaxy, Ambalipura, Varthur Hobli, Bengaluru 560103",
        )
        index = ResearchIndex(Path(__file__).parents[1] / "data" / "corpus" / "manifest.json")
        agent = ReActAgent(index, store, ProviderMustNotGuess(), grounding_verification=False)

        events = [
            event
            async for event in agent.stream_turn(owner_id=owner_id, thread_id=thread.id)
        ]
        final = next(event for event in events if event.get("kind") == "final")
        assert "Bellanduru Police Station" in final["content"]
    finally:
        store.close()
