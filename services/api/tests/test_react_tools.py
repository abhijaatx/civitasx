from pathlib import Path

import pytest

from civitas_api.agent import ProviderResult, ReActAgent
from civitas_api.civic_tools import CIVIC_TOOL_DEFINITIONS, CivicToolRuntime, resolve_location
from civitas_api.community_store import LocalCommunityStore
from civitas_api.models import AgentMessageRole
from civitas_api.research import ResearchIndex


def test_civic_tools_expose_json_schemas_and_broad_locality_resolution():
    names = {
        item["function"]["name"]
        for item in CIVIC_TOOL_DEFINITIONS
    }
    assert names == {
        "search_official_records",
        "list_official_documents",
        "get_official_document",
        "resolve_police_jurisdiction",
        "resolve_ward_and_authority",
        "list_current_wards",
        "get_current_officials",
        "draft_complaint_ticket",
        "compare_official_documents",
        "inspect_private_attachments",
        "list_live_official_sources",
        "refresh_live_official_source",
    }
    hsr = resolve_location("HSR Layout sector 2")
    bellandur = resolve_location("Bellandur near the lake")
    electronic_city = resolve_location("Electronic City phase 1")
    assert hsr["canonical_locality"] == "HSR Layout"
    assert bellandur["canonical_locality"] == "Bellandur"
    assert electronic_city["canonical_locality"] == "Electronic City"
    assert hsr["authority_id"] == "gba"
    assert hsr["needs_confirmation"] is True


def test_react_agent_has_no_rules_based_dispatcher():
    assert not hasattr(ReActAgent, "detect_intent")
    assert not hasattr(ReActAgent, "extract_locality")


@pytest.mark.asyncio
async def test_document_tools_open_latest_records_and_do_not_guess_current_wards():
    index = ResearchIndex(Path(__file__).parents[1] / "data" / "corpus" / "manifest.json")
    tools = CivicToolRuntime(index, community=None)  # type: ignore[arg-type]

    listing = await tools.execute(
        "list_official_documents",
        {"query": None, "authority_id": None, "sort_by": "published_at", "limit": 3},
    )
    assert listing["documents"]
    assert listing["documents"][0]["date_basis"] == "published_at"
    assert "retrieved_at only records" not in listing["documents"][0]["date_note"]

    source_id = listing["documents"][0]["source_id"]
    opened = await tools.execute(
        "get_official_document",
        {"source_id": source_id, "page": None, "page_limit": 1},
    )
    assert opened["document"]["document"]["source_id"] == source_id
    assert opened["document"]["pages"]

    wards = await tools.execute("list_current_wards", {"query": "current wards"})
    officials = await tools.execute("get_current_officials", {"query": "current mayor"})
    assert wards["status"] == "unavailable"
    assert wards["official_url"] == "https://www.bbmp.gov.in/KnowYourNewCorporation/"
    assert officials["official_url"] == "https://gba.karnataka.gov.in/"


def test_preflight_routes_document_followups_and_current_official_questions():
    agent = object.__new__(ReActAgent)
    agent.index = ResearchIndex(Path(__file__).parents[1] / "data" / "corpus" / "manifest.json")

    recent = agent._deterministic_preflight_calls("What are the recent documents updated?")
    assert recent[0][0] == "list_official_documents"

    current_official = agent._deterministic_preflight_calls("Who is the current mayor?")
    assert current_official == [("get_current_officials", {"query": "Who is the current mayor?"})]

    follow_up = agent._deterministic_preflight_calls(
        "Show me the document",
        source_context={"source_id": "bmrcl-phase2a-dpr", "page": 44},
    )
    assert follow_up == [
        (
            "get_official_document",
            {"source_id": "bmrcl-phase2a-dpr", "page": None, "page_limit": 8},
        )
    ]


@pytest.mark.asyncio
async def test_tool_capable_model_owns_semantic_tool_selection(tmp_path: Path):
    class SemanticToolProvider:
        name = "semantic-tool-provider"
        supports_tool_calls = True

        def __init__(self):
            self.saw_tool_messages: list[bool] = []

        async def stream(self, messages, tools):
            del tools
            has_tool_evidence = any(item.get("role") == "tool" for item in messages)
            self.saw_tool_messages.append(has_tool_evidence)
            if not has_tool_evidence:
                yield {
                    "kind": "complete",
                    "result": ProviderResult(
                        "",
                        [
                            {
                                "id": "research-1",
                                "name": "search_official_records",
                                "arguments": {
                                    "query": (
                                        "How does the official plan describe safer walking "
                                        "routes near Indiranagar?"
                                    ),
                                    "authority_id": None,
                                },
                            }
                        ],
                    ),
                }
                return
            yield {
                "kind": "complete",
                "result": ProviderResult(
                    "I found the official passages and will cite them in the answer.",
                    [],
                ),
            }

    store = LocalCommunityStore(tmp_path / "community.sqlite3")
    try:
        owner_id = "semantic-routing-resident"
        thread = store.create_thread(owner_id, "Safer walking routes")
        store.add_message(
            owner_id,
            thread.id,
            AgentMessageRole.USER,
            "How does the official plan describe safer walking routes near Indiranagar?",
        )
        provider = SemanticToolProvider()
        index = ResearchIndex(Path(__file__).parents[1] / "data" / "corpus" / "manifest.json")
        agent = ReActAgent(index, store, provider, grounding_verification=False)

        events = [
            event
            async for event in agent.stream_turn(owner_id=owner_id, thread_id=thread.id)
        ]

        assert provider.saw_tool_messages == [False, True]
        assert any(
            event.get("kind") == "tool_call"
            and event.get("data", {}).get("tool_name") == "search_official_records"
            for event in events
        )
        final = next(event for event in events if event.get("kind") == "final")
        assert "official passages" in final["content"]
    finally:
        store.close()
