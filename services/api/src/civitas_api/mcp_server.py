"""CivitasX MCP tools exposed over stateless Streamable HTTP."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .community_store import redact_public_text
from .config import get_settings
from .models import (
    AgentRunStatus,
    SendAgentMessageRequest,
    User,
)
from .policy import PolicyEngine

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


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _transport_security() -> TransportSecuritySettings:
    settings = get_settings()
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_csv(settings.mcp_allowed_hosts),
        allowed_origins=_csv(settings.mcp_allowed_origins),
    )


mcp = FastMCP(
    name="CivitasX",
    instructions=(
        "CivitasX tools answer Bengaluru civic questions from indexed public records "
        "and create or inspect a resident's private case. Use only the authenticated "
        "user's cases; do not request or invent owner IDs. Every material claim "
        "must resolve to a returned source passage. External submissions and public "
        "posts require an exact, hash-bound approval; a draft or preparation is not "
        "a submission. Long actions return a durable run ID."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=_transport_security(),
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
            "submission": bool(
                settings.demo_submission_enabled
                or settings.ipgrs_submission_enabled
                or (settings.official_api_url and settings.official_api_token)
            ),
            "feed": True,
            "agent": True,
            "tickets": True,
            "attachments": True,
            "moderation": True,
            "comparisons": True,
            "preparation": True,
            "mcp_agent_actions": True,
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
    name="get_resident_profile",
    title="Get my remembered profile",
    description="Read the authenticated resident's confirmed reusable form details.",
)
def get_resident_profile() -> dict[str, Any]:
    user = get_mcp_user()
    return _community().get_profile(user.id).model_dump(mode="json")


@mcp.tool(
    name="remember_profile_value",
    title="Remember a profile value",
    description=(
        "Save a resident-provided form value for future tasks. Use only after the "
        "resident confirms the value and explicitly asks to remember it."
    ),
)
def remember_profile_value(
    key: str,
    value: str,
    source: str = "user",
    confirmed: bool = True,
    remember: bool = True,
) -> dict[str, Any]:
    user = get_mcp_user()
    return _community().upsert_profile_value(
        user.id,
        key=key,
        value=value,
        source=source,
        confirmed=confirmed,
        remember=remember,
    ).model_dump(mode="json")


@mcp.tool(
    name="forget_profile_value",
    title="Forget a profile value",
    description="Delete one remembered resident profile value.",
)
def forget_profile_value(key: str) -> dict[str, Any]:
    user = get_mcp_user()
    _community().delete_profile_value(user.id, key)
    return {"deleted": True, "key": key}


def _community():
    from .main import get_community_store

    return get_community_store()


@mcp.tool(
    name="list_agent_threads",
    title="List agent conversations",
    description="List the authenticated resident's persistent CivitasX agent conversations.",
)
def list_agent_threads(limit: int = 30) -> dict[str, Any]:
    user = get_mcp_user()
    return {
        "items": [
            item.model_dump(mode="json")
            for item in _community().list_threads(user.id, limit)
        ]
    }


@mcp.tool(
    name="create_agent_thread",
    title="Start an agent conversation",
    description="Create a private persistent CivitasX conversation for the authenticated resident.",
)
def create_agent_thread(
    goal: str,
    title: str | None = None,
    case_id: str | None = None,
) -> dict[str, Any]:
    user = get_mcp_user()
    from .main import get_store

    if case_id:
        get_store().get_case(user.id, case_id)
    else:
        case_id = get_store().create_case(user.id, goal=goal, title=title).id
    thread = _community().create_thread(user.id, title or goal[:160], case_id)
    return _community().get_thread_detail(user.id, thread.id).model_dump(mode="json")


@mcp.tool(
    name="get_agent_thread",
    title="Read an agent conversation",
    description="Read one private agent conversation, including tool and review parts.",
)
def get_agent_thread(thread_id: str) -> dict[str, Any]:
    user = get_mcp_user()
    return _community().get_thread_detail(user.id, thread_id).model_dump(mode="json")


@mcp.tool(
    name="send_agent_message",
    title="Talk to the CivitasX agent",
    description=(
        "Send a natural-language message to a persistent CivitasX conversation. "
        "The same conversation is visible in the web app."
    ),
)
async def send_agent_message(
    thread_id: str,
    content: str,
    attachment_ids: list[str] | None = None,
    client_message_id: str | None = None,
) -> dict[str, Any]:
    user = get_mcp_user()
    from .main import execute_agent_turn

    detail = await execute_agent_turn(
        user.id,
        thread_id,
        SendAgentMessageRequest(
            content=content,
            attachment_ids=attachment_ids or [],
            client_message_id=client_message_id,
        ),
    )
    return detail.model_dump(mode="json")


@mcp.tool(
    name="get_submission_review",
    title="Inspect filing review",
    description="Read the latest exact complaint payload and its approval state.",
)
def get_submission_review(ticket_id: str) -> dict[str, Any]:
    user = get_mcp_user()
    from .main import _ticket_connector_context

    detail, profile = _ticket_connector_context(user.id, ticket_id)
    preparation = _community().latest_preparation(user.id, detail.ticket.id)
    return {
        "ticket": detail.model_dump(mode="json"),
        "connector": profile.model_dump(mode="json"),
        "preparation": (
            preparation.model_copy(
                update={"submission_enabled": profile.submission_enabled}
            ).model_dump(mode="json")
            if preparation
            else None
        ),
        "approval_required": True,
    }


@mcp.tool(
    name="prepare_submission",
    title="Prepare an official filing",
    description=(
        "Build a hash-bound, private complaint payload. This fills and validates the "
        "local preparation; it does not submit anything."
    ),
)
def prepare_submission(
    ticket_id: str,
    fields: dict[str, str] | None = None,
    attachment_ids: list[str] | None = None,
) -> dict[str, Any]:
    user = get_mcp_user()
    from .ipgrs_connector import validate_ipgrs_fields
    from .main import _ticket_connector_context

    detail, profile = _ticket_connector_context(user.id, ticket_id)
    if profile.provider == "browser":
        errors = validate_ipgrs_fields(
            {
                "description": detail.ticket.description,
                "locality": detail.ticket.locality or "",
                **(fields or {}),
            }
        )
        if errors:
            raise ValueError("; ".join(errors))
    preparation = _community().prepare_ticket(
        user.id,
        detail.ticket.id,
        authority_name=profile.name,
        contact_route=profile.contact_route,
        intake_url=str(profile.intake_url),
        required_fields=[field.key for field in profile.requirements if field.required],
        fields=fields or {},
        attachment_ids=attachment_ids or [],
    )
    return {
        "preparation": preparation.model_copy(
            update={
                "authority_name": profile.name,
                "contact_route": profile.contact_route,
                "intake_url": profile.intake_url,
                "submission_enabled": profile.submission_enabled,
            }
        ).model_dump(mode="json"),
        "approval_required": True,
    }


@mcp.tool(
    name="approve_submission",
    title="Approve an exact filing",
    description="Approve the current hash-bound preparation for the authenticated resident.",
)
def approve_submission(ticket_id: str, content_hash: str) -> dict[str, Any]:
    user = get_mcp_user()
    from .main import _ticket_connector_context

    detail, profile = _ticket_connector_context(user.id, ticket_id)
    preparation = _community().latest_preparation(user.id, detail.ticket.id)
    if preparation is None:
        raise ValueError("Prepare the ticket before approving it")
    approval = _community().approve_preparation(
        user.id, preparation.id, content_hash
    )
    return approval.model_copy(
        update={"submission_enabled": profile.submission_enabled}
    ).model_dump(mode="json")


@mcp.tool(
    name="submit_approved_request",
    title="Submit an approved filing",
    description=(
        "Start a filing for an exact approved preparation. With the reviewed Karnataka "
        "iPGRS connector enabled this opens the official portal, fills approved fields, "
        "and pauses for resident classification, OTP, and CAPTCHA. It never bypasses "
        "those resident-controlled steps. A configured certified official API uses an "
        "idempotency key and requires a verifiable authority reference. In local tests, "
        "simulation=true uses only the synthetic connector."
    ),
)
async def submit_approved_request(
    ticket_id: str,
    content_hash: str,
    simulation: bool = False,
) -> dict[str, Any]:
    user = get_mcp_user()
    from .main import _ticket_connector_context
    from .models import TicketStatus

    detail, profile = _ticket_connector_context(user.id, ticket_id)
    preparation = _community().latest_preparation(user.id, detail.ticket.id)
    if preparation is None or preparation.status != "approved":
        raise ValueError("An approved preparation is required")
    if preparation.content_hash != content_hash:
        raise ValueError("The content hash does not match the approved preparation")
    if preparation.approval_expires_at is not None:
        from .community_store import utc_now

        if preparation.approval_expires_at <= utc_now():
            raise ValueError("The review approval has expired; prepare and approve again")
    settings = get_settings()
    from .main import _existing_submission_run

    connector_enabled = bool(
        simulation and settings.demo_submission_enabled
    ) or bool(
        not simulation
        and settings.ipgrs_submission_enabled
        and profile.authority_id == "gba"
        and profile.submission_enabled
    ) or bool(
        not simulation and profile.provider == "official_api" and profile.submission_enabled
    )
    decision = PolicyEngine(settings.opa_url).decide(
        "start_submission",
        {
            "approval": preparation.status == "approved",
            "connector_enabled": connector_enabled,
            # Calling this authenticated, write-scoped MCP tool is the
            # resident/client confirmation for starting this exact run.
            "resident_confirmation": True,
        },
    )
    if not decision.allow:
        raise ValueError(decision.reason)
    existing = _existing_submission_run(
        _community(), user.id, detail.ticket.id, preparation.content_hash
    )
    if existing is not None:
        return existing.model_dump(mode="json")
    if not simulation and profile.provider == "official_api" and profile.submission_enabled:
        from .official_api_connector import execute_official_api_submission

        run = _community().create_run(
            user.id,
            kind="submission",
            message="Submitting through the certified official API",
            ticket_id=detail.ticket.id,
            connector_id=f"official-api:{profile.authority_id}",
            status=AgentRunStatus.RUNNING,
            receipt={"content_hash": preparation.content_hash, "stage": "queued"},
        )
        return (
            await execute_official_api_submission(
                owner_id=user.id,
                run=run,
                detail=detail,
                preparation=preparation,
                community=_community(),
                settings=settings,
            )
        ).model_dump(mode="json")
    run = _community().create_run(
        user.id,
        kind="submission",
        message="Submission queued",
        ticket_id=detail.ticket.id,
        connector_id=(
            "karnataka-ipgrs-grievances"
            if (
                settings.ipgrs_submission_enabled
                and profile.authority_id == "gba"
                and not simulation
            )
            else profile.authority_id
        ),
        status=AgentRunStatus.RUNNING,
        receipt={"content_hash": preparation.content_hash, "stage": "queued"},
    )
    if (
        not simulation
        and settings.ipgrs_submission_enabled
        and profile.authority_id == "gba"
    ):
        from .ipgrs_connector import ipgrs_browser_manager

        return (
            await ipgrs_browser_manager.start(
                owner_id=user.id,
                run_id=run.id,
                ticket_id=detail.ticket.id,
                preparation=preparation,
                community=_community(),
                settings=settings,
            )
        ).model_dump(mode="json")
    if not simulation or not settings.demo_submission_enabled:
        return _community().update_run(
            user.id,
            run.id,
            status=AgentRunStatus.FAILED,
            message=(
                "No verified live submission connector is enabled. Complete the filing "
                "through the supervised portal flow after credentials and approval are configured."
            ),
        ).model_dump(mode="json")
    reference = f"DEMO-{detail.ticket.civitas_ticket_id}-{run.id[:8].upper()}"
    acknowledgement = "Synthetic connector accepted the approved payload for testing."
    receipt_decision = PolicyEngine(settings.opa_url).decide(
        "record_receipt",
        {
            "submission_started": True,
            "receipt_verified": bool(reference),
            "content_hash_match": True,
        },
    )
    if not receipt_decision.allow:
        return _community().update_run(
            user.id,
            run.id,
            status=AgentRunStatus.FAILED,
            message=receipt_decision.reason,
            receipt={
                "content_hash": content_hash,
                "outcome": "outcome_unknown",
                "policy": receipt_decision.as_dict(),
            },
        ).model_dump(mode="json")
    _community().record_outcome(
        user.id,
        detail.ticket.id,
        status=TicketStatus.SUBMITTED,
        external_reference_id=reference,
        acknowledgement=acknowledgement,
        tracking_url=f"https://civitas.local/demo/track/{reference}",
        submitted_content_hash=content_hash,
        note="Demo connector submission",
    )
    return _community().update_run(
        user.id,
        run.id,
        status=AgentRunStatus.SUBMITTED,
        message="Synthetic connector accepted the approved payload",
        external_reference_id=reference,
        receipt={
            "reference_id": reference,
            "acknowledgement": acknowledgement,
            "tracking_url": f"https://civitas.local/demo/track/{reference}",
            "simulation": True,
            "content_hash": content_hash,
        },
    ).model_dump(mode="json")


@mcp.tool(
    name="prepare_public_post",
    title="Prepare a public issue preview",
    description="Create a redacted public-post preview and hash before publication approval.",
)
def prepare_public_post(
    ticket_id: str,
    title: str,
    body: str,
    locality: str | None = None,
    visibility: str = "locality",
    display_name: str | None = None,
    attachment_ids: list[str] | None = None,
) -> dict[str, Any]:
    user = get_mcp_user()
    import hashlib

    ticket = _community().get_ticket(user.id, ticket_id).ticket
    if visibility not in {"nearby", "locality", "citywide"}:
        raise ValueError("Invalid public visibility")
    redacted_title = redact_public_text(title.strip())
    redacted_body = redact_public_text(body.strip())
    content_hash = hashlib.sha256(f"{title.strip()}\n{body.strip()}".encode()).hexdigest()
    return {
        "ticket_id": ticket.id,
        "civitas_ticket_id": ticket.civitas_ticket_id,
        "title": redacted_title,
        "body": redacted_body,
        "locality": locality.strip() if locality else ticket.locality,
        "visibility": visibility,
        "display_name": display_name or user.name,
        "attachment_ids": sorted(set(attachment_ids or [])),
        "redaction_content_hash": content_hash,
        "approval_required": True,
    }


@mcp.tool(
    name="publish_approved_post",
    title="Publish an approved civic post",
    description="Publish exactly the previously reviewed redacted issue preview.",
)
def publish_approved_post(
    ticket_id: str,
    title: str,
    body: str,
    redaction_content_hash: str,
    locality: str | None = None,
    visibility: str = "locality",
    display_name: str | None = None,
    attachment_ids: list[str] | None = None,
    redaction_approved: bool = False,
) -> dict[str, Any]:
    user = get_mcp_user()
    import hashlib

    if not redaction_approved:
        raise ValueError("Review and approve the redacted preview before publishing")
    expected = hashlib.sha256(f"{title.strip()}\n{body.strip()}".encode()).hexdigest()
    if expected != redaction_content_hash:
        raise ValueError("The public preview changed after approval")
    post = _community().publish_ticket(
        user.id,
        ticket_id,
        title=title,
        body=body,
        locality=locality,
        visibility=visibility,
        author_name=display_name or user.name,
        attachment_ids=attachment_ids or [],
    )
    return post.model_dump(mode="json")


@mcp.tool(
    name="get_run_status",
    title="Get a durable action run",
    description="Read the current status, checkpoint message, and receipt for an action run.",
)
def get_run_status(run_id: str) -> dict[str, Any]:
    user = get_mcp_user()
    return _community().get_run(user.id, run_id).model_dump(mode="json")


@mcp.tool(
    name="cancel_run",
    title="Cancel an action run",
    description="Cancel a queued or waiting CivitasX action run owned by the resident.",
)
async def cancel_run(run_id: str) -> dict[str, Any]:
    user = get_mcp_user()
    run = _community().get_run(user.id, run_id)
    if run.status in {AgentRunStatus.SUBMITTED, AgentRunStatus.CANCELLED}:
        return run.model_dump(mode="json")
    if run.connector_id == "karnataka-ipgrs-grievances":
        from .ipgrs_connector import ipgrs_browser_manager

        await ipgrs_browser_manager.cancel(owner_id=user.id, run_id=run_id)
    return _community().update_run(
        user.id,
        run_id,
        status=AgentRunStatus.CANCELLED,
        message="Run cancelled by the resident",
    ).model_dump(mode="json")


@mcp.tool(
    name="resume_run",
    title="Resume a waiting action run",
    description=(
        "Resume a supervised portal run. send_otp requests the official OTP, "
        "verification_code verifies it in memory, and submit=true requests the final "
        "portal submission after the resident has completed classification and CAPTCHA "
        "and explicitly attested to that final review."
    ),
)
async def resume_run(
    run_id: str,
    verification_code: str | None = None,
    send_otp: bool = False,
    submit: bool = False,
    resident_attestation: bool = False,
) -> dict[str, Any]:
    user = get_mcp_user()
    run = _community().get_run(user.id, run_id)
    if run.connector_id == "karnataka-ipgrs-grievances":
        from .ipgrs_connector import ipgrs_browser_manager

        return (
            await ipgrs_browser_manager.resume(
                owner_id=user.id,
                run_id=run_id,
                verification_code=verification_code,
                send_otp=send_otp,
                submit=submit,
                resident_attestation=resident_attestation,
                community=_community(),
            )
        ).model_dump(mode="json")
    if run.status == AgentRunStatus.WAITING_FOR_USER:
        return _community().update_run(
            user.id,
            run_id,
            status=AgentRunStatus.RUNNING,
            message="Run resumed; awaiting the connector worker",
        ).model_dump(mode="json")
    return run.model_dump(mode="json")


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
