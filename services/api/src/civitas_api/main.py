"""FastAPI application for the CivitasX service."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

import httpx
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .agent import AgentReply, ProviderFailure, ReActAgent, build_provider
from .auth import AuthService, bearer_token, current_user, get_auth_service
from .community_store import LocalCommunityStore, utc_now
from .config import get_settings
from .connectors import get_connector, list_connectors
from .dynamo_community_store import DynamoCommunityStore
from .errors import ConflictError, NotFoundError, RateLimitError
from .live_connectors import LiveConnectorRegistry
from .mcp_server import mcp, reset_mcp_user, set_mcp_user
from .models import (
    AgentMessageRole,
    AgentThreadDetail,
    AgentThreadStatus,
    AppCapabilities,
    AppConfig,
    AppLimits,
    ApplyWorkspaceChangeRequest,
    ApprovePreparationRequest,
    Artifact,
    Attachment,
    AttachmentListResponse,
    AuthConfig,
    AuthorityListResponse,
    AuthResponse,
    CaseListResponse,
    CheckpointListResponse,
    CivicPost,
    CognitoExchangeRequest,
    Comment,
    CommentListResponse,
    CompareSourcesRequest,
    ConnectorListResponse,
    ConnectorProfile,
    CreateAgentThreadRequest,
    CreateArtifactRequest,
    CreateCaseRequest,
    CreateCommentRequest,
    CreatePublicPostRequest,
    CreateReportRequest,
    CreateTicketRequest,
    DocumentComparison,
    FeedQuery,
    FeedResponse,
    FollowRequest,
    FollowResponse,
    FollowSubjectListResponse,
    FollowSubjectRequest,
    LiveEndpointListResponse,
    LiveRefreshResult,
    LoginRequest,
    ModerationActionListResponse,
    ModerationActionRequest,
    MuteRequest,
    MuteResponse,
    NotificationListResponse,
    NotificationReadRequest,
    PasswordRecoveryRequest,
    PreparationApproval,
    PrepareTicketRequest,
    PublicPostAttachment,
    PublicPostAttachmentListResponse,
    RecordOutcomeRequest,
    RegisterRequest,
    ReportListResponse,
    ResearchAnswer,
    ResearchHistoryResponse,
    ResearchQueryRequest,
    SaveRequest,
    SaveResponse,
    SendAgentMessageRequest,
    ShareSnapshot,
    SourceDocumentDetail,
    TicketDetail,
    TicketPreparation,
    TicketStatus,
    UpdateAgentThreadRequest,
    UpdateCaseRequest,
    UpdateTicketStatusRequest,
    UsageSummary,
    User,
    VoteRequest,
    VoteResponse,
)
from .research import ResearchIndex
from .store import DynamoStore, SQLiteStore

# Build the transport once; its session manager is started from FastAPI's
# lifespan below so Streamable HTTP requests work when mounted under /mcp.
mcp_http_app = mcp.streamable_http_app()
mcp_session_manager = mcp.session_manager


@lru_cache(maxsize=1)
def get_store() -> SQLiteStore:
    settings = get_settings()
    if settings.storage == "dynamodb":
        # Keep this branch explicit so a production deployment never silently
        # writes to local disk.
        return DynamoStore(  # type: ignore[return-value]
            settings.dynamodb_table,
            settings.dynamodb_region,
            budget_limit_usd=settings.global_budget_usd,
            browser_concurrency=settings.browser_concurrency,
        )
    return SQLiteStore(settings.db_path)


@lru_cache(maxsize=1)
def get_community_store() -> LocalCommunityStore | DynamoCommunityStore:
    settings = get_settings()
    if settings.storage == "dynamodb":
        return DynamoCommunityStore(
            settings.dynamodb_table,
            settings.dynamodb_region,
            attachments_bucket=settings.artifacts_bucket,
        )
    return LocalCommunityStore(settings.db_path, settings.db_path.parent / "attachments")


@lru_cache(maxsize=1)
def get_agent() -> ReActAgent:
    settings = get_settings()
    return ReActAgent(
        get_research_index(),
        get_community_store(),
        build_provider(settings),
        max_iterations=settings.agent_max_iterations,
        context_max_chars=settings.agent_context_max_chars,
        context_keep_messages=settings.agent_context_keep_messages,
        grounding_verification=settings.agent_grounding_verification,
        live_registry=get_live_registry(),
    )


@lru_cache(maxsize=1)
def get_research_index() -> ResearchIndex:
    settings = get_settings()
    return ResearchIndex(
        settings.corpus_manifest_path,
        stale_after_days=settings.research_stale_after_days,
        live_cache_path=settings.live_cache,
        live_cache_bucket=settings.live_cache_bucket or settings.corpus_bucket,
        live_cache_prefix=settings.live_cache_prefix,
    )


@lru_cache(maxsize=1)
def get_live_registry() -> LiveConnectorRegistry:
    settings = get_settings()
    return LiveConnectorRegistry(
        timeout_seconds=settings.live_fetch_timeout_seconds,
        status_path=settings.live_status,
        status_bucket=settings.live_status_bucket or settings.corpus_bucket,
        status_prefix=settings.live_status_prefix,
        credentials={
            "CIVITAS_DATA_GOV_API_KEY": settings.data_gov_api_key,
            "CIVITAS_APISETU_CLIENT_ID": settings.apisetu_client_id,
            "CIVITAS_DIGILOCKER_CLIENT_ID": settings.digilocker_client_id,
            "CIVITAS_CPGRAMS_CLIENT_ID": settings.cpgrams_client_id,
            "CIVITAS_PARIVAHAN_CLIENT_ID": settings.parivahan_client_id,
            "CIVITAS_GSTN_CLIENT_ID": settings.gstn_client_id,
            "CIVITAS_ABDM_CLIENT_ID": settings.abdm_client_id,
            "CIVITAS_ABHA_CLIENT_ID": settings.abha_client_id,
        },
    )


class MCPAuthMiddleware(BaseHTTPMiddleware):
    """Pass the authenticated identity into FastMCP tool execution.

    FastMCP owns the JSON-RPC transport, so regular FastAPI dependencies cannot
    be attached to individual MCP tools. A context variable bridges the outer
    HTTP authentication boundary to those tool functions without trusting an
    owner ID supplied in tool arguments.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if not request.url.path.startswith("/mcp"):
            return await call_next(request)
        # Browser preflight requests intentionally do not carry bearer
        # credentials. CORSMiddleware is installed outside this middleware,
        # but keep this guard as a second line of defence for mounted apps.
        if request.method == "OPTIONS":
            return await call_next(request)
        token = bearer_token(request.headers.get("authorization"))
        try:
            user = get_auth_service().user_from_token(token)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        context_token = set_mcp_user(user)
        try:
            return await call_next(request)
        finally:
            reset_mcp_user(context_token)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Instantiate the configured resources once during startup. Cases, agent
    # conversations, tickets, and the feed now select the same storage family.
    async with mcp_session_manager.run():
        get_store()
        get_research_index()
        get_community_store()
        get_agent()
        yield
    store = get_store()
    store.close()
    get_store.cache_clear()
    get_research_index.cache_clear()
    get_live_registry.cache_clear()
    community_store = get_community_store()
    community_store.close()
    get_community_store.cache_clear()
    getattr(get_agent, "cache_clear", lambda: None)()
    # FastMCP intentionally treats a manager as single-use. Reset this private
    # test/development flag so TestClient and hot-reload can restart cleanly;
    # a production process still runs only one lifespan.
    mcp_session_manager._has_started = False  # type: ignore[attr-defined]


app = FastAPI(
    title="CivitasX API",
    version=get_settings().api_version,
    description=("Local-first Bengaluru civic Feed, Agent, ticket, and grounded research service."),
    lifespan=lifespan,
)
settings = get_settings()
app.add_middleware(MCPAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.mount("/mcp", mcp_http_app)


@app.exception_handler(ConflictError)
async def conflict_handler(_: Request, exc: ConflictError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})


@app.exception_handler(NotFoundError)
async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})


@app.exception_handler(RateLimitError)
async def rate_limit_handler(_: Request, exc: RateLimitError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": str(exc), "code": "usage_limit"},
        headers={"Retry-After": "60"},
    )


@app.get("/api/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    paid_enabled = settings.agent_provider == "bedrock" or (
        settings.agent_provider == "groq" and bool(settings.groq_api_key)
    )
    return {
        "status": "ok",
        "service": settings.app_name,
        "phase": 8,
        "auth_mode": settings.auth_mode,
        "storage": settings.storage,
        "paid_operations_enabled": paid_enabled,
        "research_indexed_documents": get_research_index().document_count,
        "feed_enabled": True,
        "agent_provider": settings.agent_provider,
    }


@app.get("/api/config", response_model=AppConfig)
def public_config() -> AppConfig:
    settings = get_settings()
    return AppConfig(
        auth=AuthConfig(
            mode=settings.auth_mode,
            local_recovery_enabled=bool(
                settings.auth_mode == "local" and settings.local_recovery_code
            ),
            cognito_domain=settings.cognito_domain,
            client_id=settings.cognito_client_id,
            region=settings.cognito_region,
            user_pool_id=settings.cognito_user_pool_id,
        ),
        capabilities=AppCapabilities(
            phase=8,
            research=True,
            submission=False,
            feed=True,
            agent=True,
            tickets=True,
            attachments=True,
            live_sources=True,
        ),
        limits=AppLimits(
            global_budget_usd=settings.global_budget_usd,
            browser_concurrency=settings.browser_concurrency,
            max_browser_actions_per_attempt=settings.max_browser_actions_per_attempt,
            max_browser_seconds_per_attempt=settings.max_browser_seconds_per_case,
        ),
    )


@app.post(
    "/api/auth/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    request: RegisterRequest, auth: AuthService = Depends(get_auth_service)
) -> AuthResponse:
    return auth.register(request)


@app.post("/api/auth/login", response_model=AuthResponse)
def login(request: LoginRequest, auth: AuthService = Depends(get_auth_service)) -> AuthResponse:
    return auth.login(request)


@app.post("/api/auth/recover", response_model=AuthResponse)
def recover_password(
    request: PasswordRecoveryRequest, auth: AuthService = Depends(get_auth_service)
) -> AuthResponse:
    return auth.recover_password(request)


@app.post("/api/auth/cognito/exchange")
def exchange_cognito_code(request: CognitoExchangeRequest) -> dict[str, str]:
    settings = get_settings()
    if (
        settings.auth_mode != "cognito"
        or not settings.cognito_domain
        or not settings.cognito_client_id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cognito auth is not configured"
        )
    token_url = f"{settings.cognito_domain.rstrip('/')}/oauth2/token"
    try:
        response = httpx.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "client_id": settings.cognito_client_id,
                "code": request.code,
                "redirect_uri": request.redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=502, detail="The identity provider could not be reached"
        ) from error
    if not response.is_success:
        raise HTTPException(status_code=401, detail="The identity provider rejected this sign-in")
    payload = response.json()
    access_token = payload.get("access_token") or payload.get("id_token")
    if not isinstance(access_token, str) or not access_token:
        raise HTTPException(
            status_code=502, detail="The identity provider returned no usable token"
        )
    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    token: str | None = Depends(bearer_token), auth: AuthService = Depends(get_auth_service)
) -> Response:
    auth.logout(token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/me", response_model=User)
def me(user: User = Depends(current_user)) -> User:
    return user


@app.get("/api/cases", response_model=CaseListResponse)
def list_cases(user: User = Depends(current_user)) -> CaseListResponse:
    return CaseListResponse(items=get_store().list_cases(user.id))


@app.post("/api/cases", response_model=dict, status_code=status.HTTP_201_CREATED)
def create_case(request: CreateCaseRequest, user: User = Depends(current_user)) -> dict[str, Any]:
    return (
        get_store()
        .create_case(user.id, goal=request.goal, title=request.title)
        .model_dump(mode="json")
    )


@app.get("/api/cases/{case_id}")
def get_case(case_id: str, user: User = Depends(current_user)) -> dict[str, Any]:
    return get_store().get_case(user.id, case_id).model_dump(mode="json")


@app.patch("/api/cases/{case_id}")
def update_case(
    case_id: str, request: UpdateCaseRequest, user: User = Depends(current_user)
) -> dict[str, Any]:
    return (
        get_store()
        .update_case(
            user.id,
            case_id,
            version=request.version,
            goal=request.goal,
            title=request.title,
            notes=request.notes,
        )
        .model_dump(mode="json")
    )


@app.get("/api/cases/{case_id}/artifacts", response_model=dict)
def list_artifacts(case_id: str, user: User = Depends(current_user)) -> dict[str, Any]:
    items = get_store().list_artifacts(user.id, case_id)
    return {"items": [item.model_dump(mode="json") for item in items]}


@app.post(
    "/api/cases/{case_id}/artifacts",
    response_model=Artifact,
    status_code=status.HTTP_201_CREATED,
)
def create_artifact(
    case_id: str, request: CreateArtifactRequest, user: User = Depends(current_user)
) -> Artifact:
    return get_store().create_artifact(
        user.id, case_id, request.name, request.content, request.kind
    )


@app.get("/api/cases/{case_id}/artifacts/{artifact_id}", response_model=Artifact)
def get_artifact(case_id: str, artifact_id: str, user: User = Depends(current_user)) -> Artifact:
    return get_store().get_artifact(user.id, case_id, artifact_id)


@app.get("/api/cases/{case_id}/events", response_model=dict)
def list_events(case_id: str, user: User = Depends(current_user)) -> dict[str, Any]:
    items = get_store().list_events(user.id, case_id)
    return {"items": [event.model_dump(mode="json") for event in items]}


@app.get("/api/cases/{case_id}/usage", response_model=UsageSummary)
def case_usage(case_id: str, user: User = Depends(current_user)) -> UsageSummary:
    return get_store().get_usage(user.id, case_id)


@app.get("/api/usage/global")
def global_usage(user: User = Depends(current_user)) -> dict[str, float | int]:
    # Keep the endpoint authenticated even though the counters contain no
    # personal data; it is operational information, not a public budget API.
    _ = user
    return get_store().get_global_usage()


# ---------------------------------------------------------------------------
# Local-first Agent, ticket, and community feed endpoints.


def _authority_name(authority_id: str | None) -> str | None:
    if not authority_id:
        return None
    record = get_research_index().authority_records.get(authority_id)
    return record.name if record else None


def require_moderator(user: User = Depends(current_user)) -> User:
    configured = {
        email.strip().casefold()
        for email in get_settings().moderator_emails.split(",")
        if email.strip()
    }
    if user.email.casefold() not in configured:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Moderator access required"
        )
    return user


def _decorate_post(post: CivicPost, viewer_id: str) -> CivicPost:
    return post.model_copy(
        update={
            "authority_name": _authority_name(post.authority_id),
            # Do not expose the internal owner UUID on public responses.
            "is_owner": post.author_id == viewer_id,
            "author_id": None,
        }
    )


@app.get("/api/feed", response_model=FeedResponse)
def feed(query: FeedQuery = Depends(), user: User = Depends(current_user)) -> FeedResponse:
    if query.sort == "nearby" and not query.locality:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Select a locality before opening the Nearby feed",
        )
    try:
        posts, next_cursor = get_community_store().list_feed_page(
            user.id,
            sort=query.sort,
            locality=query.locality,
            visibility=query.visibility,
            status=query.status,
            authority_id=query.authority_id,
            topic=query.topic,
            limit=query.limit,
            cursor=query.cursor,
            enforce_visibility=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FeedResponse(
        items=[_decorate_post(post, user.id) for post in posts],
        next_cursor=next_cursor,
    )


@app.get("/api/feed/{post_id}", response_model=CivicPost)
def feed_post(
    post_id: str,
    locality: str | None = Query(default=None, max_length=160),
    user: User = Depends(current_user),
) -> CivicPost:
    return _decorate_post(
        get_community_store().get_post(
            post_id, viewer_id=user.id, locality=locality, enforce_visibility=True
        ),
        user.id,
    )


@app.get("/api/feed/{post_id}/evidence", response_model=dict)
def feed_post_evidence(
    post_id: str,
    locality: str | None = Query(default=None, max_length=160),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    # Visibility is checked before reading the source-linked private thread.
    get_community_store().get_post(
        post_id, viewer_id=user.id, locality=locality, enforce_visibility=True
    )
    return {
        "items": [
            item.model_dump(mode="json")
            for item in get_community_store().list_post_evidence(post_id)
        ]
    }


@app.get(
    "/api/feed/{post_id}/attachments",
    response_model=PublicPostAttachmentListResponse,
)
def feed_post_attachments(
    post_id: str,
    locality: str | None = Query(default=None, max_length=160),
    user: User = Depends(current_user),
) -> PublicPostAttachmentListResponse:
    attachments = get_community_store().list_post_attachments(
        post_id,
        viewer_id=user.id,
        locality=locality,
        enforce_visibility=True,
    )
    return PublicPostAttachmentListResponse(
        items=[
            PublicPostAttachment(
                id=attachment.id,
                post_id=post_id,
                filename=attachment.filename,
                content_type=attachment.content_type,
                size_bytes=attachment.size_bytes,
                url=f"/api/feed/{post_id}/attachments/{attachment.id}",
            )
            for attachment in attachments
        ]
    )


@app.get("/api/feed/{post_id}/attachments/{attachment_id}")
def feed_post_attachment(
    post_id: str,
    attachment_id: str,
    locality: str | None = Query(default=None, max_length=160),
    user: User = Depends(current_user),
) -> FileResponse:
    attachments = get_community_store().list_post_attachments(
        post_id,
        viewer_id=user.id,
        locality=locality,
        enforce_visibility=True,
    )
    attachment = next((item for item in attachments if item.id == attachment_id), None)
    if attachment is None:
        raise NotFoundError("Public attachment not found")
    return FileResponse(
        get_community_store().attachment_path(attachment),
        media_type=attachment.content_type,
        filename=attachment.filename,
    )


@app.get("/api/feed/{post_id}/comments", response_model=CommentListResponse)
def feed_comments(post_id: str, user: User = Depends(current_user)) -> CommentListResponse:
    return CommentListResponse(items=get_community_store().list_comments(post_id, user.id))


@app.post(
    "/api/feed/{post_id}/comments", response_model=Comment, status_code=status.HTTP_201_CREATED
)
def add_feed_comment(
    post_id: str,
    request: CreateCommentRequest,
    user: User = Depends(current_user),
) -> Comment:
    return get_community_store().create_comment(
        user.id, post_id, user.name, request.body, request.parent_id
    )


def _require_owned_comment_on_post(post_id: str, comment_id: str, user: User) -> None:
    comments = get_community_store().list_comments(post_id, user.id)
    if not any(comment.id == comment_id and comment.is_owner for comment in comments):
        raise NotFoundError("Comment not found")


@app.patch("/api/feed/{post_id}/comments/{comment_id}", response_model=Comment)
def edit_feed_comment(
    post_id: str,
    comment_id: str,
    request: CreateCommentRequest,
    user: User = Depends(current_user),
) -> Comment:
    _require_owned_comment_on_post(post_id, comment_id, user)
    return get_community_store().edit_comment(user.id, comment_id, request.body)


@app.delete("/api/feed/{post_id}/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_feed_comment(
    post_id: str, comment_id: str, user: User = Depends(current_user)
) -> Response:
    _require_owned_comment_on_post(post_id, comment_id, user)
    get_community_store().delete_comment(user.id, comment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/api/feed/{post_id}/comments/{comment_id}/report",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
def report_feed_comment(
    post_id: str,
    comment_id: str,
    request: CreateReportRequest,
    user: User = Depends(current_user),
) -> dict[str, Any]:
    _ = get_community_store().get_post(post_id, viewer_id=user.id)
    if not any(
        comment.id == comment_id
        for comment in get_community_store().list_comments(post_id, user.id)
    ):
        # A report may target another resident's comment, but the comment must
        # belong to this post. The store-level lookup below enforces that scope.
        raise NotFoundError("Comment not found")
    return (
        get_community_store()
        .create_report(user.id, "comment", comment_id, request.reason, request.details)
        .model_dump(mode="json")
    )


@app.post("/api/feed/{post_id}/vote", response_model=VoteResponse)
def vote_feed_post(
    post_id: str, request: VoteRequest, user: User = Depends(current_user)
) -> VoteResponse:
    return get_community_store().vote_post(user.id, post_id, request.value)


@app.post("/api/feed/{post_id}/follow", response_model=FollowResponse)
def follow_feed_post(
    post_id: str, request: FollowRequest, user: User = Depends(current_user)
) -> FollowResponse:
    return get_community_store().follow_post(user.id, post_id, request.following)


@app.post("/api/feed/{post_id}/save", response_model=SaveResponse)
def save_feed_post(
    post_id: str, request: SaveRequest, user: User = Depends(current_user)
) -> SaveResponse:
    result = get_community_store().save_post(user.id, post_id, request.saved)
    return SaveResponse(**result)


@app.post("/api/feed/{post_id}/mute", response_model=MuteResponse)
def mute_feed_post(
    post_id: str, request: MuteRequest, user: User = Depends(current_user)
) -> MuteResponse:
    result = get_community_store().mute_post(user.id, post_id, request.muted)
    return MuteResponse(**result)


@app.post("/api/feed/{post_id}/share", response_model=ShareSnapshot)
def share_feed_post(post_id: str, user: User = Depends(current_user)) -> ShareSnapshot:
    return get_community_store().create_share_snapshot(user.id, post_id)


@app.post("/api/feed/{post_id}/report", response_model=dict, status_code=status.HTTP_201_CREATED)
def report_feed_post(
    post_id: str, request: CreateReportRequest, user: User = Depends(current_user)
) -> dict[str, Any]:
    return (
        get_community_store()
        .create_report(user.id, "post", post_id, request.reason, request.details)
        .model_dump(mode="json")
    )


@app.get("/api/subjects/follows", response_model=FollowSubjectListResponse)
def list_feed_subject_follows(user: User = Depends(current_user)) -> FollowSubjectListResponse:
    return FollowSubjectListResponse(items=get_community_store().list_subject_follows(user.id))


@app.post("/api/subjects/follows", response_model=dict)
def follow_feed_subject(
    request: FollowSubjectRequest, user: User = Depends(current_user)
) -> dict[str, Any]:
    return (
        get_community_store()
        .follow_subject(user.id, request.subject_type, request.value, request.following)
        .model_dump(mode="json")
    )


@app.get("/api/notifications", response_model=NotificationListResponse)
def notifications(user: User = Depends(current_user)) -> NotificationListResponse:
    return NotificationListResponse(items=get_community_store().list_notifications(user.id))


@app.patch("/api/notifications/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
def mark_notification(
    notification_id: str,
    request: NotificationReadRequest,
    user: User = Depends(current_user),
) -> Response:
    get_community_store().mark_notification(user.id, notification_id, request.read)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_notifications(user: User = Depends(current_user)) -> Response:
    get_community_store().mark_all_notifications(user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.delete("/api/shares/{token}", response_model=ShareSnapshot)
def revoke_share(token: str, user: User = Depends(current_user)) -> ShareSnapshot:
    return get_community_store().revoke_share_snapshot(user.id, token)


@app.get("/share/{token}")
def public_share(token: str) -> JSONResponse:
    """Return only the redacted, expiring public snapshot."""

    return JSONResponse(
        content=jsonable_encoder(get_community_store().get_share_snapshot(token)),
        headers={"Cache-Control": "no-store, private"},
    )


@app.get("/api/moderation/reports", response_model=ReportListResponse)
def moderation_reports(
    report_status: str | None = Query(default="open", alias="status", max_length=30),
    _: User = Depends(require_moderator),
) -> ReportListResponse:
    return ReportListResponse(items=get_community_store().list_reports(report_status))


@app.get("/api/moderation/actions", response_model=ModerationActionListResponse)
def moderation_actions(
    _: User = Depends(require_moderator),
) -> ModerationActionListResponse:
    return ModerationActionListResponse(items=get_community_store().list_moderation_actions())


@app.post("/api/moderation/{target_type}/{target_id}", response_model=dict)
def moderation_action(
    target_type: str,
    target_id: str,
    request: ModerationActionRequest,
    moderator: User = Depends(require_moderator),
) -> dict[str, Any]:
    try:
        return get_community_store().moderate(
            moderator.id, target_type, target_id, request.action, request.note
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/agent/threads", response_model=dict)
def agent_threads(user: User = Depends(current_user)) -> dict[str, Any]:
    return {
        "items": [
            thread.model_dump(mode="json") for thread in get_community_store().list_threads(user.id)
        ]
    }


@app.post(
    "/api/agent/threads", response_model=AgentThreadDetail, status_code=status.HTTP_201_CREATED
)
def create_agent_thread(
    request: CreateAgentThreadRequest, user: User = Depends(current_user)
) -> AgentThreadDetail:
    case_id = request.case_id
    goal = request.goal or request.title or "Explore a Bengaluru civic issue"
    if case_id:
        get_store().get_case(user.id, case_id)
    else:
        case_id = get_store().create_case(user.id, goal=goal, title=request.title).id
    title = request.title or goal[:160]
    thread = get_community_store().create_thread(user.id, title, case_id)
    return get_community_store().get_thread_detail(user.id, thread.id)


@app.get("/api/agent/threads/{thread_id}", response_model=AgentThreadDetail)
def get_agent_thread(thread_id: str, user: User = Depends(current_user)) -> AgentThreadDetail:
    return get_community_store().get_thread_detail(user.id, thread_id)


@app.patch("/api/agent/threads/{thread_id}", response_model=AgentThreadDetail)
def update_agent_thread(
    thread_id: str,
    request: UpdateAgentThreadRequest,
    user: User = Depends(current_user),
) -> AgentThreadDetail:
    status_value = None
    if request.status is not None:
        status_value = AgentThreadStatus(request.status)
    get_community_store().update_thread(
        user.id, thread_id, title=request.title, status=status_value
    )
    return get_community_store().get_thread_detail(user.id, thread_id)


def _begin_agent_turn(owner_id: str, thread_id: str, request: SendAgentMessageRequest) -> bool:
    community = get_community_store()
    thread = community.get_thread(owner_id, thread_id)
    if request.client_message_id:
        existing = community.get_thread_detail(owner_id, thread_id)
        for index, message in enumerate(existing.messages):
            if message.role != AgentMessageRole.USER:
                continue
            for part in message.parts:
                if (part.data or {}).get("client_message_id") == request.client_message_id:
                    # A browser can disconnect after the user message is saved but before
                    # the streamed assistant reply is persisted. Allow that exact turn to
                    # resume; only dedupe once an assistant reply exists after it.
                    if any(
                        later.role == AgentMessageRole.ASSISTANT
                        for later in existing.messages[index + 1 :]
                    ):
                        return False
                    return True
    if thread.message_count == 0 and thread.title.lower().startswith(
        "explore a bengaluru civic issue"
    ):
        generated_title = " ".join(request.content.split())[:70]
        if generated_title:
            community.update_thread(owner_id, thread_id, title=generated_title)
    community.validate_attachment_ids(owner_id, thread_id, request.attachment_ids)
    parts = [
        {"type": "attachment", "attachment_id": attachment_id, "text": "Attached evidence"}
        for attachment_id in request.attachment_ids
    ]
    if request.client_message_id:
        # Keep the idempotency marker inside the existing persisted part union;
        # metadata is not a user-visible message type.
        parts.append(
            {
                "type": "status",
                "text": "client message marker",
                "data": {"client_message_id": request.client_message_id},
            }
        )
    community.add_message(
        owner_id,
        thread_id,
        AgentMessageRole.USER,
        request.content,
        parts,
    )
    return True


def _reserve_agent_usage(owner_id: str, thread_id: str) -> str | None:
    settings = get_settings()
    thread = get_community_store().get_thread(owner_id, thread_id)
    if not thread.case_id:
        return None
    # Admission is deliberately conservative: the reservation is refunded on
    # local/provider failure and settled after the response is persisted.
    amount = min(0.25, max(0.01, settings.max_model_cost_per_case_usd))
    return get_store().reserve_usage(
        owner_id=owner_id,
        case_id=thread.case_id,
        kind="model",
        amount_usd=amount,
        global_budget_usd=settings.global_budget_usd,
        browser_concurrency=settings.browser_concurrency,
    )


def _persist_agent_reply(owner_id: str, thread_id: str, reply: AgentReply) -> None:
    get_community_store().add_message(
        owner_id, thread_id, AgentMessageRole.ASSISTANT, reply.content, reply.parts
    )


def _agent_reply_usage(reply: AgentReply) -> tuple[int, int]:
    """Read provider token telemetry from the persisted turn-complete part."""

    for part in reversed(reply.parts):
        if part.get("type") != "status":
            continue
        data = part.get("data") or {}
        if data.get("status") not in {"turn_complete", "budget_exhausted"}:
            continue
        try:
            return max(0, int(data.get("input_tokens", 0))), max(
                0, int(data.get("output_tokens", 0))
            )
        except (TypeError, ValueError):
            return 0, 0
    return 0, 0


@app.post("/api/agent/threads/{thread_id}/messages", response_model=AgentThreadDetail)
async def send_agent_message(
    thread_id: str,
    request: SendAgentMessageRequest,
    user: User = Depends(current_user),
) -> AgentThreadDetail:
    reservation = _reserve_agent_usage(user.id, thread_id)
    try:
        created = _begin_agent_turn(user.id, thread_id, request)
        if not created:
            if reservation:
                get_store().release_usage(owner_id=user.id, reservation_id=reservation)
            return get_community_store().get_thread_detail(user.id, thread_id)
        reply = await get_agent().run(owner_id=user.id, thread_id=thread_id)
        _persist_agent_reply(user.id, thread_id, reply)
        if reservation:
            input_tokens, output_tokens = _agent_reply_usage(reply)
            get_store().settle_usage(
                owner_id=user.id,
                reservation_id=reservation,
                actual_cost_usd=0.0,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                item_kind="model",
            )
        return get_community_store().get_thread_detail(user.id, thread_id)
    except Exception:
        if reservation:
            get_store().release_usage(owner_id=user.id, reservation_id=reservation)
        raise


@app.post("/api/agent/threads/{thread_id}/messages/stream")
async def stream_agent_message(
    thread_id: str,
    request: SendAgentMessageRequest,
    user: User = Depends(current_user),
) -> StreamingResponse:
    """Stream model tokens and real civic tool calls over the SSE contract."""

    reservation = _reserve_agent_usage(user.id, thread_id)
    try:
        created = _begin_agent_turn(user.id, thread_id, request)
    except Exception:
        if reservation:
            get_store().release_usage(owner_id=user.id, reservation_id=reservation)
        raise

    async def events():
        if not created:
            if reservation:
                get_store().release_usage(owner_id=user.id, reservation_id=reservation)
            detail = get_community_store().get_thread_detail(user.id, thread_id)
            assistant = next(
                (
                    message
                    for message in reversed(detail.messages)
                    if message.role == AgentMessageRole.ASSISTANT
                ),
                None,
            )
            if assistant:
                payload = {"message": assistant.model_dump(mode="json")}
                encoded = json.dumps(payload, ensure_ascii=False)
                yield f"event: message\ndata: {encoded}\n\n"
            yield 'event: status\ndata: {"state":"complete","label":"Existing response reused"}\n\n'
            yield "event: done\ndata: {}\n\n"
            return
        settled = False
        try:
            final: AgentReply | None = None
            async for event in get_agent().stream_turn(owner_id=user.id, thread_id=thread_id):
                kind = event.get("kind")
                if kind == "token":
                    token_payload = {"delta": event.get("text", "")}
                    encoded = json.dumps(token_payload, ensure_ascii=False)
                    yield f"event: message\ndata: {encoded}\n\n"
                elif kind in {"plan", "status", "tool_call", "tool_result"}:
                    event_data = json.dumps(event.get("data", {}), ensure_ascii=False)
                    yield f"event: {kind}\ndata: {event_data}\n\n"
                elif kind == "final":
                    final = AgentReply(
                        content=str(event.get("content", "")), parts=list(event.get("parts", []))
                    )
            if final is None:
                final = AgentReply("The agent did not complete this turn. Please retry.", [])
            _persist_agent_reply(user.id, thread_id, final)
            if reservation:
                input_tokens, output_tokens = _agent_reply_usage(final)
                get_store().settle_usage(
                    owner_id=user.id,
                    reservation_id=reservation,
                    actual_cost_usd=0.0,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    item_kind="model",
                )
                settled = True
            detail = get_community_store().get_thread_detail(user.id, thread_id)
            assistant = next(
                (
                    message
                    for message in reversed(detail.messages)
                    if message.role == AgentMessageRole.ASSISTANT
                ),
                None,
            )
            yield 'event: status\ndata: {"state":"complete","label":"Response ready"}\n\n'
            if assistant:
                payload = {"message": assistant.model_dump(mode="json")}
                yield f"event: message\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"
        finally:
            if reservation and not settled:
                get_store().release_usage(owner_id=user.id, reservation_id=reservation)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/agent/threads/{thread_id}/workspace/apply", response_model=AgentThreadDetail)
async def apply_workspace_change(
    thread_id: str,
    request: ApplyWorkspaceChangeRequest,
    user: User = Depends(current_user),
) -> AgentThreadDetail:
    """Apply only a previously proposed Codex change after explicit approval."""

    community = get_community_store()
    detail = community.get_thread_detail(user.id, thread_id)
    proposal: dict[str, Any] | None = None
    already_applied = False
    for message in detail.messages:
        for part in message.parts:
            if not isinstance(part.data, dict):
                continue
            if (
                part.type == "action"
                and part.data.get("action") == "workspace_change_approval"
                and part.data.get("proposal_id") == request.proposal_id
            ):
                proposal = dict(part.data)
            if (
                part.type == "status"
                and part.data.get("status") == "workspace_change_applied"
                and part.data.get("proposal_id") == request.proposal_id
            ):
                already_applied = True
    if proposal is None:
        raise HTTPException(status_code=404, detail="Workspace change proposal not found")
    if already_applied:
        raise HTTPException(status_code=409, detail="Workspace change was already applied")
    prompt = str(proposal.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=409, detail="Workspace change proposal is empty")
    try:
        summary = await get_agent().apply_workspace_change(prompt)
    except ProviderFailure as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    community.add_message(
        user.id,
        thread_id,
        AgentMessageRole.ASSISTANT,
        summary,
        parts=[
            {
                "type": "status",
                "text": "Approved workspace change applied",
                "data": {
                    "status": "workspace_change_applied",
                    "proposal_id": request.proposal_id,
                    "provider": "codex-cli",
                    "sandbox": "workspace-write",
                },
            }
        ],
    )
    return community.get_thread_detail(user.id, thread_id)


@app.post(
    "/api/agent/threads/{thread_id}/attachments",
    response_model=Attachment,
    status_code=status.HTTP_201_CREATED,
)
async def upload_agent_attachment(
    thread_id: str,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
) -> Attachment:
    community = get_community_store()
    community.get_thread(user.id, thread_id)
    content = await file.read()
    if len(content) > get_settings().attachment_max_bytes:
        raise HTTPException(status_code=413, detail="Attachment is too large")
    thread = community.get_thread(user.id, thread_id)
    return community.save_attachment(
        user.id,
        filename=file.filename or "upload",
        content_type=file.content_type or "application/octet-stream",
        content=content,
        thread_id=thread_id,
        case_id=thread.case_id,
    )


@app.get("/api/agent/threads/{thread_id}/attachments", response_model=AttachmentListResponse)
def agent_attachments(thread_id: str, user: User = Depends(current_user)) -> AttachmentListResponse:
    return AttachmentListResponse(items=get_community_store().list_attachments(user.id, thread_id))


@app.get("/api/agent/attachments/{attachment_id}")
def download_agent_attachment(
    attachment_id: str, user: User = Depends(current_user)
) -> FileResponse:
    community = get_community_store()
    attachment = community.get_attachment(user.id, attachment_id)
    return FileResponse(
        community.attachment_path(attachment),
        media_type=attachment.content_type,
        filename=attachment.filename,
    )


@app.delete("/api/agent/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent_attachment(attachment_id: str, user: User = Depends(current_user)) -> Response:
    get_community_store().delete_attachment(user.id, attachment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/tickets", response_model=dict)
def tickets(user: User = Depends(current_user)) -> dict[str, Any]:
    return {
        "items": [
            ticket.model_dump(mode="json") for ticket in get_community_store().list_tickets(user.id)
        ]
    }


@app.post("/api/tickets", response_model=TicketDetail, status_code=status.HTTP_201_CREATED)
def create_ticket(request: CreateTicketRequest, user: User = Depends(current_user)) -> TicketDetail:
    community = get_community_store()
    if request.thread_id:
        thread = community.get_thread(user.id, request.thread_id)
        if request.case_id and thread.case_id != request.case_id:
            raise ConflictError("Thread and case do not match")
        case_id = request.case_id or thread.case_id
    else:
        case_id = request.case_id
    if case_id:
        get_store().get_case(user.id, case_id)
    return community.create_ticket(
        user.id,
        title=request.title,
        description=request.description,
        authority_id=request.authority_id,
        locality=request.locality,
        visibility=request.visibility,
        thread_id=request.thread_id,
        case_id=case_id,
    )


@app.get("/api/tickets/{ticket_id}", response_model=TicketDetail)
def get_ticket(ticket_id: str, user: User = Depends(current_user)) -> TicketDetail:
    return get_community_store().get_ticket(user.id, ticket_id)


def _ticket_connector_context(
    user_id: str, ticket_id: str
) -> tuple[TicketDetail, ConnectorProfile]:
    community = get_community_store()
    detail = community.get_ticket(user_id, ticket_id)
    if not detail.ticket.authority_id:
        raise HTTPException(
            status_code=422, detail="Choose an authority before preparing this ticket"
        )
    profile = get_connector(get_research_index(), detail.ticket.authority_id)
    if profile is None:
        raise NotFoundError("No verified connector is available for this authority")
    return detail, profile


@app.post("/api/tickets/{ticket_id}/prepare", response_model=TicketPreparation)
def prepare_ticket(
    ticket_id: str,
    request: PrepareTicketRequest,
    user: User = Depends(current_user),
) -> TicketPreparation:
    detail, profile = _ticket_connector_context(user.id, ticket_id)
    preparation = get_community_store().prepare_ticket(
        user.id,
        detail.ticket.id,
        authority_name=profile.name,
        contact_route=profile.contact_route,
        intake_url=str(profile.intake_url),
        required_fields=[field.key for field in profile.requirements if field.required],
        fields=request.fields,
        attachment_ids=request.attachment_ids,
    )
    return preparation.model_copy(
        update={
            "authority_name": profile.name,
            "contact_route": profile.contact_route,
            "intake_url": profile.intake_url,
        }
    )


@app.get("/api/tickets/{ticket_id}/preparation", response_model=TicketPreparation)
def latest_ticket_preparation(
    ticket_id: str, user: User = Depends(current_user)
) -> TicketPreparation:
    detail, profile = _ticket_connector_context(user.id, ticket_id)
    preparation = get_community_store().latest_preparation(user.id, detail.ticket.id)
    if preparation is None:
        raise NotFoundError("Ticket has not been prepared yet")
    return preparation.model_copy(
        update={
            "authority_name": profile.name,
            "contact_route": profile.contact_route,
            "intake_url": profile.intake_url,
        }
    )


@app.post("/api/tickets/{ticket_id}/preparation/approve", response_model=PreparationApproval)
def approve_ticket_preparation(
    ticket_id: str,
    request: ApprovePreparationRequest,
    user: User = Depends(current_user),
) -> PreparationApproval:
    detail, _ = _ticket_connector_context(user.id, ticket_id)
    preparation = get_community_store().latest_preparation(user.id, detail.ticket.id)
    if preparation is None:
        raise NotFoundError("Prepare the ticket before approving it")
    return get_community_store().approve_preparation(user.id, preparation.id, request.content_hash)


@app.get("/api/tickets/{ticket_id}/checkpoints", response_model=CheckpointListResponse)
def ticket_checkpoints(
    ticket_id: str, user: User = Depends(current_user)
) -> CheckpointListResponse:
    return CheckpointListResponse(items=get_community_store().list_checkpoints(user.id, ticket_id))


@app.post("/api/tickets/{ticket_id}/outcome", response_model=TicketDetail)
def ticket_outcome(
    ticket_id: str,
    request: RecordOutcomeRequest,
    user: User = Depends(current_user),
) -> TicketDetail:
    if request.status != "outcome_unknown":
        require_moderator(user)
    if request.status == "submitted" and not request.submitted_content_hash:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Record the submitted content hash when marking a ticket submitted",
        )
    return get_community_store().record_outcome(
        user.id,
        ticket_id,
        status=TicketStatus(request.status),
        external_reference_id=request.external_reference_id,
        acknowledgement=request.acknowledgement,
        tracking_url=str(request.tracking_url) if request.tracking_url else None,
        submitted_content_hash=request.submitted_content_hash,
        note=request.note,
    )


@app.post("/api/tickets/{ticket_id}/submit")
def submit_ticket(ticket_id: str, user: User = Depends(current_user)) -> JSONResponse:
    """Explicitly keep government submission disabled in the local build."""

    detail = get_community_store().get_ticket(user.id, ticket_id)
    preparation = get_community_store().latest_preparation(user.id, detail.ticket.id)
    if preparation is None or preparation.status != "approved":
        raise HTTPException(status_code=409, detail="A valid review approval is required")
    if preparation.approval_expires_at is None or preparation.approval_expires_at <= utc_now():
        raise HTTPException(
            status_code=409, detail="The review approval has expired; prepare and approve again"
        )
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "Government submission is disabled until an official connector and "
            "credentials are configured"
        ),
    )


@app.get("/api/tickets/{ticket_id}/export", response_class=PlainTextResponse)
def export_ticket(ticket_id: str, user: User = Depends(current_user)) -> PlainTextResponse:
    """Export a private, human-readable evidence brief for offline use."""

    community = get_community_store()
    detail = community.get_ticket(user.id, ticket_id)
    lines = [
        "CIVITASX EVIDENCE BRIEF",
        "========================",
        f"Ticket: {detail.ticket.civitas_ticket_id}",
        f"Status: {detail.ticket.status.value}",
        f"Title: {detail.ticket.title}",
        f"Locality: {detail.ticket.locality or 'Not specified'}",
        f"Authority route: {detail.ticket.authority_id or 'Not specified'}",
        f"Government reference: {detail.ticket.external_reference_id or 'Not recorded'}",
        f"Tracking URL: {detail.ticket.tracking_url or 'Not recorded'}",
        "",
        "DESCRIPTION",
        detail.ticket.description,
        "",
        "STATUS HISTORY",
    ]
    lines.extend(
        f"- {event.created_at.isoformat()} · {event.status.value}"
        + (f" · {event.note}" if event.note else "")
        for event in detail.history
    )
    if detail.ticket.thread_id:
        thread = community.get_thread_detail(user.id, detail.ticket.thread_id)
        lines.extend(["", "AGENT CONVERSATION"])
        lines.extend(f"- {message.role.value}: {message.content}" for message in thread.messages)
        attachments = community.list_attachments(user.id, detail.ticket.thread_id)
        if attachments:
            lines.extend(["", "ATTACHMENTS"])
            lines.extend(
                f"- {attachment.filename} · {attachment.content_type} · "
                f"{attachment.size_bytes} bytes · sha256 {attachment.sha256}"
                for attachment in attachments
            )
    if detail.ticket.public_post_id:
        lines.extend(["", f"PUBLIC POST: {detail.ticket.public_post_id}"])
    preparation = community.latest_preparation(user.id, detail.ticket.id)
    if preparation:
        lines.extend(
            [
                "",
                "AUTHORITY PREPARATION",
                f"- Status: {preparation.status}",
                f"- Content hash: {preparation.content_hash}",
                f"- Missing fields: {', '.join(preparation.missing_fields) or 'None'}",
                f"- Submission enabled: {preparation.submission_enabled}",
            ]
        )
    checkpoints = community.list_checkpoints(user.id, detail.ticket.id)
    if checkpoints:
        lines.extend(["", "RECOVERY CHECKPOINTS"])
        lines.extend(
            f"- {checkpoint.created_at.isoformat()} · {checkpoint.phase} · {checkpoint.summary}"
            for checkpoint in checkpoints
        )
    lines.extend(
        ["", "This brief is a private export. Verify all official outcomes with the authority."]
    )
    return PlainTextResponse("\n".join(lines), media_type="text/plain; charset=utf-8")


@app.patch("/api/tickets/{ticket_id}/status", response_model=TicketDetail)
def update_ticket_status(
    ticket_id: str,
    request: UpdateTicketStatusRequest,
    user: User = Depends(current_user),
) -> TicketDetail:
    if request.status in {TicketStatus.RESOLVED, TicketStatus.SUBMITTED}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Verified authority evidence is required before marking a ticket "
                "resolved or submitted"
            ),
        )
    return get_community_store().update_ticket_status(
        user.id, ticket_id, request.status, request.note
    )


@app.post(
    "/api/tickets/{ticket_id}/publish",
    response_model=CivicPost,
    status_code=status.HTTP_201_CREATED,
)
def publish_ticket(
    ticket_id: str,
    request: CreatePublicPostRequest,
    user: User = Depends(current_user),
) -> CivicPost:
    if not request.redaction_approved:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail="Review and approve the redacted public preview before publishing",
        )
    if (
        get_settings().environment.lower() in {"production", "prod"}
        and not request.redaction_content_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail="Production publishing requires a hash-bound redaction approval",
        )
    if request.redaction_content_hash:
        expected_hash = hashlib.sha256(f"{request.title}\n{request.body}".encode()).hexdigest()
        if request.redaction_content_hash != expected_hash:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail=(
                    "The public preview changed after approval; review it again before publishing"
                ),
            )
    community = get_community_store()
    ticket = community.get_ticket(user.id, ticket_id).ticket
    post = community.publish_ticket(
        user.id,
        ticket.id,
        title=request.title,
        body=request.body,
        locality=request.locality,
        visibility=request.visibility,
        author_name=request.display_name or user.name,
        attachment_ids=request.attachment_ids,
    )
    return _decorate_post(post, user.id)


@app.post("/api/research", response_model=ResearchAnswer)
def research(
    request: ResearchQueryRequest,
    user: User = Depends(current_user),
) -> ResearchAnswer:
    """Answer a civic question from indexed public records.

    The default implementation is extractive and local. When a case ID is
    supplied, the complete answer is saved as a private artifact so the user
    can resume without repeating the query.
    """

    settings = get_settings()
    if request.case_id:
        get_store().get_case(user.id, request.case_id)
    result = get_research_index().answer(
        request.question,
        max_sources=min(request.max_sources, settings.research_max_sources),
        date_from=request.date_from,
        date_to=request.date_to,
    )
    if request.case_id:
        get_store().create_artifact(
            user.id,
            request.case_id,
            f"Research answer · {result.route}",
            result.model_dump_json(),
            "note",
        )
        get_store().add_event(
            user.id,
            request.case_id,
            "research.completed",
            (
                f"Evidence checked: {result.coverage.hits_returned} source"
                f"{'' if result.coverage.hits_returned == 1 else 's'}"
            ),
        )
    return result


@app.get("/api/cases/{case_id}/research", response_model=ResearchHistoryResponse)
def case_research(case_id: str, user: User = Depends(current_user)) -> ResearchHistoryResponse:
    """Return saved research answers for a private case, newest first."""

    artifacts = get_store().list_artifacts(user.id, case_id)
    items: list[ResearchAnswer] = []
    for artifact in artifacts:
        if not artifact.name.startswith("Research answer"):
            continue
        try:
            items.append(ResearchAnswer.model_validate_json(artifact.content))
        except ValueError:
            # A manually edited note should not make the whole case unreadable.
            continue
    return ResearchHistoryResponse(items=items)


@app.get("/api/authorities", response_model=AuthorityListResponse)
def authorities(
    query: str | None = Query(default=None, max_length=200), _: User = Depends(current_user)
) -> AuthorityListResponse:
    return AuthorityListResponse(items=get_research_index().list_authorities(query))


@app.get("/api/connectors", response_model=ConnectorListResponse)
def connectors(
    query: str | None = Query(default=None, max_length=200), _: User = Depends(current_user)
) -> ConnectorListResponse:
    return ConnectorListResponse(items=list_connectors(get_research_index(), query))


@app.get("/api/connectors/{authority_id}", response_model=ConnectorProfile)
def connector(authority_id: str, _: User = Depends(current_user)) -> ConnectorProfile:
    profile = get_connector(get_research_index(), authority_id)
    if profile is None:
        raise NotFoundError("Connector not found")
    return profile


@app.get("/api/live/connectors", response_model=LiveEndpointListResponse)
def live_connectors(user: User = Depends(current_user)) -> LiveEndpointListResponse:
    _ = user
    return LiveEndpointListResponse(items=get_live_registry().list(get_research_index()))


@app.post("/api/live/connectors/{endpoint_id}/refresh", response_model=LiveRefreshResult)
def refresh_live_connector(
    endpoint_id: str, user: User = Depends(current_user)
) -> LiveRefreshResult:
    if get_settings().environment.lower() in {"production", "prod"}:
        require_moderator(user)
    try:
        return get_live_registry().refresh(endpoint_id, get_research_index())
    except KeyError as exc:
        raise NotFoundError(str(exc)) from exc


@app.get("/api/live/open-data/search")
def search_india_open_data(
    resource_id: str = Query(..., min_length=8, max_length=128),
    q: str | None = Query(default=None, max_length=200),
    filter: list[str] = Query(default=[]),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    _: User = Depends(current_user),
) -> dict[str, Any]:
    """Query one India OGD resource without exposing the API key to clients."""

    filters: dict[str, str] = {}
    for item in filter:
        if "=" not in item:
            raise HTTPException(status_code=400, detail="Filters must use key=value format")
        key, value = item.split("=", 1)
        filters[key] = value
    try:
        data = get_live_registry().query_data_gov(
            resource_id,
            query=q,
            filters=filters,
            limit=limit,
            offset=offset,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"resource_id": resource_id, "query": q, "filters": filters, "data": data}


@app.get("/api/sources/{source_id}", response_model=SourceDocumentDetail)
def source_detail(source_id: str, _: User = Depends(current_user)) -> SourceDocumentDetail:
    document = get_research_index().get_document(source_id)
    if document is None:
        raise NotFoundError("Source not found")
    pages = []
    for page in get_research_index().pages:
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
    return SourceDocumentDetail(document=document, pages=pages)


@app.post("/api/sources/compare", response_model=DocumentComparison)
def compare_sources(
    request: CompareSourcesRequest, _: User = Depends(current_user)
) -> DocumentComparison:
    try:
        return get_research_index().compare_documents(request.source_id, request.baseline_source_id)
    except KeyError as exc:
        raise NotFoundError(str(exc)) from exc
