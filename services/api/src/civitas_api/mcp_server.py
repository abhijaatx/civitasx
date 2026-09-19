"""CivitasX MCP tools exposed over stateless Streamable HTTP."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import get_settings
from .models import User

_mcp_user: ContextVar[User | None] = ContextVar("civitas_mcp_user", default=None)


def set_mcp_user(user: User | None):
    return _mcp_user.set(user)


def reset_mcp_user(token: Any) -> None:
    _mcp_user.reset(token)


def get_mcp_user() -> User:
    user = _mcp_user.get()
    if user is None:
        raise PermissionError("An authenticated user is required for this MCP tool")
    return user


def _store():
    from .main import get_store

    return get_store()


mcp = FastMCP(
    name="CivitasX",
    instructions=(
        "CivitasX tools answer Bengaluru civic questions from indexed public records "
        "and create or inspect a resident's private case. Use only the authenticated "
        "user's cases; do not request or invent owner IDs. Every material claim "
        "must resolve to a returned source passage."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
)


@mcp.tool(
    name="get_civitas_status",
    title="Get CivitasX status",
    description="Return the current phase, enabled capabilities, and safe operating limits.",
)
def get_civitas_status() -> dict[str, Any]:
    settings = get_settings()
    usage = _store().get_global_usage()
    from .main import get_research_index

    return {
        "service": "CivitasX",
        "phase": 8,
        "capabilities": {
            "research": True,
            "submission": False,
            "feed": True,
            "agent": True,
            "tickets": True,
            "attachments": True,
            "moderation": True,
            "comparisons": True,
            "preparation": True,
            "live_sources": True,
        },
        "limits": {
            "global_budget_usd": settings.global_budget_usd,
            "browser_concurrency": settings.browser_concurrency,
            "max_browser_actions_per_attempt": settings.max_browser_actions_per_attempt,
            "max_browser_seconds_per_attempt": settings.max_browser_seconds_per_case,
        },
        "usage": usage,
        "research_indexed_documents": get_research_index().document_count,
    }


@mcp.tool(
    name="list_my_cases",
    title="List my saved cases",
    description="List saved CivitasX cases belonging to the authenticated user.",
)
def list_my_cases(limit: int = 50) -> dict[str, Any]:
    user = get_mcp_user()
    cases = _store().list_cases(user.id, limit=limit)
    return {"items": [case.model_dump(mode="json") for case in cases]}


@mcp.tool(
    name="create_case",
    title="Save a civic request",
    description="Create a private saved case for the authenticated user from a goal.",
)
def create_case(goal: str, title: str | None = None) -> dict[str, Any]:
    user = get_mcp_user()
    case = _store().create_case(user.id, goal=goal, title=title)
    return case.model_dump(mode="json")


@mcp.tool(
    name="get_my_case",
    title="Get one saved case",
    description="Read one private saved case by ID for the authenticated user.",
)
def get_my_case(case_id: str) -> dict[str, Any]:
    user = get_mcp_user()
    case = _store().get_case(user.id, case_id)
    return case.model_dump(mode="json")


@mcp.tool(
    name="search_civic_records",
    title="Search Bengaluru civic records",
    description=(
        "Answer a Bengaluru civic question from indexed public records. "
        "The response includes supported facts, uncertainties, authority matches, "
        "and page-addressable source passages."
    ),
)
def search_civic_records(question: str, max_sources: int = 6) -> dict[str, Any]:
    get_mcp_user()
    from .main import get_research_index

    result = get_research_index().answer(question, max_sources=max_sources)
    return result.model_dump(mode="json")


@mcp.tool(
    name="get_authority_directory",
    title="Find the responsible civic authority",
    description="List source-verified Bengaluru authority responsibilities and contact routes.",
)
def get_authority_directory(query: str | None = None) -> dict[str, Any]:
    get_mcp_user()
    from .main import get_research_index

    return {
        "items": [
            record.model_dump(mode="json")
            for record in get_research_index().list_authorities(query)
        ]
    }


@mcp.tool(
    name="get_civic_source",
    title="Read a civic source",
    description="Read document metadata and page-level passages for a returned source ID.",
)
def get_civic_source(source_id: str) -> dict[str, Any]:
    get_mcp_user()
    from .main import get_research_index

    index = get_research_index()
    document = index.get_document(source_id)
    if document is None:
        return {"source_id": source_id, "found": False, "message": "Source not found"}
    pages = []
    for page in index.pages:
        if page.source_id != source_id:
            continue
        translated = bool(page.original_text and page.translation)
        pages.append(
            {
                "source_id": source_id,
                "page": page.page,
                "passage": page.translation if translated else page.text,
                "original_passage": page.original_text if translated else None,
                "translated_passage": page.translation if translated else None,
                "translation_language": "kn" if translated else None,
            }
        )
    return {"found": True, "document": document.model_dump(mode="json"), "pages": pages}


@mcp.tool(
    name="compare_civic_sources",
    title="Compare two civic source versions",
    description="Return source-linked page changes between two indexed documents.",
)
def compare_civic_sources(source_id: str, baseline_source_id: str) -> dict[str, Any]:
    get_mcp_user()
    from .main import get_research_index

    try:
        return get_research_index().compare_documents(source_id, baseline_source_id).model_dump(
            mode="json"
        )
    except KeyError:
        return {
            "found": False,
            "source_id": source_id,
            "baseline_source_id": baseline_source_id,
            "message": "One or both source documents were not found",
        }


@mcp.tool(
    name="get_civic_feed",
    title="Read the civic feed",
    description="Read public redacted civic posts with explainable local ranking signals.",
)
def get_civic_feed(
    sort: str = "recommended",
    locality: str | None = None,
    topic: str | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    user = get_mcp_user()
    from .main import _decorate_post, get_community_store

    posts = get_community_store().list_feed(
        user.id, sort=sort, locality=locality, topic=topic, limit=limit
    )
    return {"items": [_decorate_post(post, user.id).model_dump(mode="json") for post in posts]}


@mcp.tool(
    name="get_my_ticket",
    title="Read my civic ticket",
    description="Read one private CivitasX ticket and its status history.",
)
def get_my_ticket(ticket_id: str) -> dict[str, Any]:
    user = get_mcp_user()
    from .main import get_community_store

    return get_community_store().get_ticket(user.id, ticket_id).model_dump(mode="json")


@mcp.tool(
    name="list_live_sources",
    title="List live government sources",
    description="List allowlisted read-only government endpoints and their refresh status.",
)
def list_live_sources() -> dict[str, Any]:
    get_mcp_user()
    from .main import get_live_registry, get_research_index

    return {
        "items": [
            profile.model_dump(mode="json")
            for profile in get_live_registry().list(get_research_index())
        ]
    }


@mcp.tool(
    name="query_india_open_data",
    title="Query India Open Government Data",
    description=(
        "Query one allowlisted data.gov.in public resource. Requires the operator's "
        "official API key; no write operation is exposed."
    ),
)
def query_india_open_data(
    resource_id: str,
    query: str | None = None,
    filters: dict[str, str] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    get_mcp_user()
    from .main import get_live_registry

    try:
        data = get_live_registry().query_data_gov(
            resource_id,
            query=query,
            filters=filters,
            limit=limit,
            offset=offset,
        )
    except (PermissionError, ValueError, RuntimeError) as exc:
        return {
            "resource_id": resource_id,
            "status": "unavailable",
            "message": str(exc),
        }
    return {"resource_id": resource_id, "status": "ok", "data": data}


@mcp.tool(
    name="research_my_case",
    title="Research my saved case",
    description="Run a grounded civic search for a private case and save the answer to its record.",
)
def research_my_case(
    case_id: str, question: str | None = None, max_sources: int = 6
) -> dict[str, Any]:
    user = get_mcp_user()
    store = _store()
    case = store.get_case(user.id, case_id)
    from .main import get_research_index

    result = get_research_index().answer(question or case.goal, max_sources=max_sources)
    store.create_artifact(
        user.id,
        case_id,
        f"Research answer · {result.route}",
        result.model_dump_json(),
        "note",
    )
    store.add_event(
        user.id,
        case_id,
        "research.completed",
        (
            f"Evidence checked: {result.coverage.hits_returned} source"
            f"{'' if result.coverage.hits_returned == 1 else 's'}"
        ),
    )
    return result.model_dump(mode="json")
