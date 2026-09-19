from civitas_api.agent import ReActAgent
from civitas_api.civic_tools import CIVIC_TOOL_DEFINITIONS, resolve_location


def test_civic_tools_expose_json_schemas_and_broad_locality_resolution():
    names = {
        item["function"]["name"]
        for item in CIVIC_TOOL_DEFINITIONS
    }
    assert names == {
        "search_official_records",
        "resolve_ward_and_authority",
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
