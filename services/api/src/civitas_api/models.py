"""Shared API and MCP data contracts.

These contracts cover the local Feed + Agent slice and the later government
action boundary. Grounded research answers remain case artifacts while browser,
review, and submission shapes stay stable for the next phases.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationInfo,
    field_validator,
    model_validator,
)


def _clean_text(value: str) -> str:
    return " ".join(value.strip().split())


def _required_text(value: str, *, minimum: int, label: str) -> str:
    cleaned = _clean_text(value)
    if len(cleaned) < minimum:
        raise ValueError(f"{label} must contain at least {minimum} characters")
    return cleaned


class User(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    email: str
    created_at: datetime | None = None


class ProfileValue(BaseModel):
    """A resident value that may be reused after explicit confirmation."""

    key: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=4000)
    source: Literal["user", "conversation", "document"] = "user"
    confirmed: bool = False
    remember: bool = True
    confirmed_at: datetime | None = None
    updated_at: datetime


class ResidentProfile(BaseModel):
    """Owner-scoped reusable details for conversational form filling."""

    items: list[ProfileValue] = Field(default_factory=list)
    updated_at: datetime | None = None


class UpsertProfileValueRequest(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=4000)
    source: Literal["user", "conversation", "document"] = "user"
    confirmed: bool = True
    remember: bool = True

    @field_validator("key", "value")
    @classmethod
    def clean_profile_text(cls, value: str) -> str:
        return _required_text(value, minimum=1, label="Profile value")


class DeleteProfileValueRequest(BaseModel):
    key: str = Field(min_length=1, max_length=80)


class AgentRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    READY_FOR_REVIEW = "ready_for_review"
    SUBMITTED = "submitted"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentRun(BaseModel):
    id: str
    owner_id: str
    thread_id: str | None = None
    ticket_id: str | None = None
    kind: Literal["conversation", "submission", "publication"]
    status: AgentRunStatus
    message: str
    connector_id: str | None = None
    external_reference_id: str | None = None
    receipt: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ResumeRunRequest(BaseModel):
    """User-controlled continuation for a browser run.

    The verification code is accepted only in memory and is never persisted in
    the run receipt or event log.
    """

    verification_code: str | None = Field(default=None, min_length=1, max_length=32)
    send_otp: bool = False
    submit: bool = False
    # The final button is a deliberate resident attestation that the visible
    # portal classification and any CAPTCHA/consent step were reviewed.  The
    # API never tries to infer or bypass those controls.
    resident_attestation: bool = False


class AuthResponse(BaseModel):
    user: User
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=256)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return _required_text(value, minimum=1, label="Name")

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("Enter a valid email address")
        return value


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        return value.strip().lower()


class PasswordRecoveryRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    recovery_code: str = Field(min_length=8, max_length=256)
    password: str = Field(min_length=12, max_length=256)

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        return value.strip().lower()


class CognitoExchangeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=4096)
    redirect_uri: str = Field(min_length=1, max_length=2048)


class CaseStatus(StrEnum):
    SAVED = "saved"


class CivicCase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    goal: str
    notes: str = ""
    status: CaseStatus = CaseStatus.SAVED
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class CaseListResponse(BaseModel):
    items: list[CivicCase]


class CreateCaseRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=10000)
    title: str | None = Field(default=None, max_length=160)

    @field_validator("goal", "title")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = _clean_text(value)
        return value or None


class UpdateCaseRequest(BaseModel):
    goal: str | None = Field(default=None, min_length=1, max_length=10000)
    title: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=20000)
    version: int = Field(ge=1)

    @field_validator("goal", "title", "notes")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return value.strip()


class SourceEvidence(BaseModel):
    source_id: str
    title: str
    authority: str
    status: Literal["draft", "proposed", "adopted", "historical", "unknown"] = "unknown"
    page: int | None = Field(default=None, ge=1)
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    url: HttpUrl | None = None
    passage: str = ""
    original_passage: str | None = None
    translation_language: str | None = None
    content_hash: str | None = None
    source_kind: Literal["official", "official_reference", "uploaded", "fixture"] = (
        "official_reference"
    )
    extraction_method: str | None = None
    extraction_status: Literal["complete", "partial", "unreadable", "unknown"] = "complete"


class SourceDocument(BaseModel):
    """A versioned public document retained in the civic evidence corpus."""

    source_id: str
    title: str
    authority: str
    authority_id: str
    jurisdiction: str = "Bengaluru"
    status: Literal["draft", "proposed", "adopted", "historical", "unknown"] = "unknown"
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    url: HttpUrl | None = None
    content_hash: str
    language: Literal["en", "kn", "mixed", "unknown"] = "unknown"
    page_count: int = Field(default=0, ge=0)
    source_kind: Literal["official", "official_reference", "uploaded", "fixture"] = (
        "official_reference"
    )
    extraction_method: str = "unknown"
    extraction_status: Literal["complete", "partial", "unreadable", "unknown"] = "unknown"
    stale_after_days: int = Field(default=180, ge=1)


class AuthorityRecord(BaseModel):
    """A source-verified directory entry used for routing civic requests."""

    authority_id: str
    name: str
    short_name: str
    responsibilities: list[str] = Field(default_factory=list)
    contact_route: str
    url: HttpUrl
    verified_at: datetime
    source_ids: list[str] = Field(default_factory=list)
    status: Literal["current", "historical", "ambiguous", "unknown"] = "current"


class SearchHit(BaseModel):
    """A ranked, page-addressable retrieval result."""

    source_id: str
    title: str
    authority: str
    authority_id: str
    status: Literal["draft", "proposed", "adopted", "historical", "unknown"]
    page: int = Field(ge=1)
    score: float = Field(ge=0)
    keyword_score: float = Field(default=0, ge=0)
    semantic_score: float = Field(default=0, ge=0)
    passage: str
    original_passage: str | None = None
    translated_passage: str | None = None
    translation_language: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    url: HttpUrl | None = None
    content_hash: str | None = None
    matched_terms: list[str] = Field(default_factory=list)
    source_kind: Literal["official", "official_reference", "uploaded", "fixture"] = (
        "official_reference"
    )
    extraction_method: str | None = None
    extraction_status: Literal["complete", "partial", "unreadable", "unknown"] = "complete"

    def as_evidence(self) -> SourceEvidence:
        return SourceEvidence(
            source_id=self.source_id,
            title=self.title,
            authority=self.authority,
            status=self.status,
            page=self.page,
            published_at=self.published_at,
            retrieved_at=self.retrieved_at,
            url=self.url,
            passage=self.passage,
            original_passage=self.original_passage,
            translation_language=self.translation_language,
            content_hash=self.content_hash,
            source_kind=self.source_kind,
            extraction_method=self.extraction_method,
            extraction_status=self.extraction_status,
        )


class SourcePage(BaseModel):
    source_id: str
    page: int = Field(ge=1)
    passage: str
    original_passage: str | None = None
    translated_passage: str | None = None
    translation_language: str | None = None


class SourceDocumentDetail(BaseModel):
    document: SourceDocument
    pages: list[SourcePage] = Field(default_factory=list)


class ResearchCoverage(BaseModel):
    documents_considered: int = Field(default=0, ge=0)
    pages_considered: int = Field(default=0, ge=0)
    hits_returned: int = Field(default=0, ge=0)
    retrieval_mode: Literal["hybrid_offline", "hybrid_bedrock", "keyword"] = "hybrid_offline"
    source_quality: Literal["official", "official_reference", "mixed", "none"] = "none"
    stale_source_ids: list[str] = Field(default_factory=list)
    unreadable_source_ids: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class ResearchFact(BaseModel):
    fact_id: str
    claim: str
    supporting_source_ids: list[str] = Field(default_factory=list)
    supporting_pages: list[int] = Field(default_factory=list)
    claim_status: Literal["supported", "conflicting", "insufficient"] = "supported"


class ResearchCalculation(BaseModel):
    expression: str
    result: str
    source_ids: list[str] = Field(default_factory=list)


class ResearchQueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=10000)
    case_id: str | None = None
    max_sources: int = Field(default=6, ge=1, le=12)
    date_from: datetime | None = None
    date_to: datetime | None = None

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        value = " ".join(value.strip().split())
        if len(value) < 3:
            raise ValueError("Ask a question with at least three characters")
        return value


class ResearchAnswer(BaseModel):
    query: str
    route: Literal["transport", "budget", "planning", "civic_service", "general"]
    explanation: str
    facts: list[ResearchFact] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    sources: list[SourceEvidence] = Field(default_factory=list)
    authority_matches: list[AuthorityRecord] = Field(default_factory=list)
    coverage: ResearchCoverage
    calculations: list[ResearchCalculation] = Field(default_factory=list)
    checked_at: datetime
    generated_by: Literal["extractive", "bedrock-grounded"] = "extractive"


class ResearchHistoryResponse(BaseModel):
    items: list[ResearchAnswer] = Field(default_factory=list)


class AuthorityListResponse(BaseModel):
    items: list[AuthorityRecord] = Field(default_factory=list)


class Draft(BaseModel):
    id: str
    case_id: str
    body: str
    stance: str | None = None
    evidence: list[SourceEvidence] = []
    version: int = 1


class BrowserTaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    READY_FOR_REVIEW = "ready_for_review"
    SUBMITTED = "submitted"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BrowserTask(BaseModel):
    id: str
    case_id: str
    url: HttpUrl
    instruction: str
    status: BrowserTaskStatus = BrowserTaskStatus.QUEUED
    actions: int = 0
    browser_seconds: int = 0
    checkpoint_id: str | None = None


class Approval(BaseModel):
    id: str
    case_id: str
    destination: str
    content_hash: str
    approved_at: datetime
    expires_at: datetime | None = None
    valid: bool = True


class Receipt(BaseModel):
    id: str
    case_id: str
    destination: str
    submitted_at: datetime
    acknowledgement: str | None = None
    tracking_url: HttpUrl | None = None
    submitted_content_hash: str


class TaskEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    type: str
    message: str
    created_at: datetime


class UsageItem(BaseModel):
    id: str
    kind: Literal["model", "embedding", "translation", "browser", "storage", "other"]
    estimated_cost_usd: float = Field(ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    browser_seconds: int = Field(default=0, ge=0)
    created_at: datetime


class UsageSummary(BaseModel):
    case_id: str
    estimated_cost_usd: float = Field(ge=0)
    reserved_cost_usd: float = Field(ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    browser_seconds: int = Field(default=0, ge=0)
    items: list[UsageItem] = []


class Artifact(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    name: str
    content: str
    kind: Literal["note", "attachment"]
    created_at: datetime


class CreateArtifactRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    content: str = Field(max_length=200000)
    kind: Literal["note", "attachment"] = "note"

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return _required_text(value, minimum=1, label="Artifact name")


class AppCapabilities(BaseModel):
    phase: int = 8
    research: bool = True
    submission: bool = False
    feed: bool = True
    agent: bool = True
    tickets: bool = True
    attachments: bool = True
    live_sources: bool = True


class AppLimits(BaseModel):
    global_budget_usd: float
    browser_concurrency: int
    max_browser_actions_per_attempt: int
    max_browser_seconds_per_attempt: int


class AuthConfig(BaseModel):
    mode: Literal["local", "cognito"]
    local_recovery_enabled: bool = False
    cognito_domain: str | None = None
    client_id: str | None = None
    region: str | None = None
    user_pool_id: str | None = None


class AppConfig(BaseModel):
    auth: AuthConfig
    capabilities: AppCapabilities
    limits: AppLimits


# ---------------------------------------------------------------------------
# Final-direction local vertical slice: Agent, tickets, and civic feed.


class AgentThreadStatus(StrEnum):
    ACTIVE = "active"
    WAITING_FOR_USER = "waiting_for_user"
    COMPLETE = "complete"
    ARCHIVED = "archived"


class AgentMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class MessagePart(BaseModel):
    type: Literal[
        "text",
        "attachment",
        "location",
        "citation",
        "action",
        "tool",
        "command",
        "status",
    ]
    text: str | None = None
    attachment_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class AgentMessage(BaseModel):
    id: str
    thread_id: str
    role: AgentMessageRole
    content: str
    parts: list[MessagePart] = Field(default_factory=list)
    created_at: datetime


class AgentThread(BaseModel):
    id: str
    case_id: str | None = None
    title: str
    status: AgentThreadStatus = AgentThreadStatus.ACTIVE
    created_at: datetime
    updated_at: datetime
    message_count: int = Field(default=0, ge=0)
    ticket_id: str | None = None


class CreateAgentThreadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    goal: str | None = Field(default=None, max_length=10000)
    case_id: str | None = None

    @field_validator("title", "goal")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = " ".join(value.strip().split())
        return value or None


class UpdateAgentThreadRequest(BaseModel):
    """Small, explicit mutations used by the conversation list."""

    title: str | None = Field(default=None, min_length=1, max_length=160)
    status: Literal["active", "waiting_for_user", "complete", "archived"] | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _required_text(value, minimum=1, label="Conversation title")


class AgentLocation(BaseModel):
    """A consented map/device location attached to one agent turn."""

    label: str | None = Field(default=None, max_length=240)
    address: str | None = Field(default=None, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0, le=100000)
    source: Literal["browser", "map_pin", "user"] = "user"

    @model_validator(mode="after")
    def validate_location_reference(self) -> Self:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Latitude and longitude must be supplied together")
        if not any((self.label, self.address, self.latitude is not None)):
            raise ValueError("A location label, address, or coordinate is required")
        return self


class SendAgentMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=8)
    location: AgentLocation | None = None
    response_language: Literal["auto", "en", "kn", "hi"] = "auto"
    client_message_id: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be empty")
        return value


class ApplyWorkspaceChangeRequest(BaseModel):
    """Explicit approval for a previously proposed local Codex change."""

    proposal_id: str = Field(min_length=8, max_length=128)


class ClarificationRequest(BaseModel):
    """Structured missing-information prompt surfaced by the Agent."""

    missing: list[str] = Field(default_factory=list, min_length=1, max_length=12)
    prompt: str = Field(min_length=1, max_length=2000)


class AgentThreadDetail(BaseModel):
    thread: AgentThread
    messages: list[AgentMessage] = Field(default_factory=list)


class Attachment(BaseModel):
    id: str
    thread_id: str | None = None
    case_id: str | None = None
    owner_id: str
    filename: str
    content_type: str
    size_bytes: int = Field(ge=0)
    sha256: str
    storage_key: str
    visibility: Literal["private", "public_redacted"] = "private"
    created_at: datetime


class AttachmentListResponse(BaseModel):
    items: list[Attachment] = Field(default_factory=list)


class TicketStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_INFORMATION = "needs_information"
    READY_FOR_REVIEW = "ready_for_review"
    SUBMITTED = "submitted"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    ACKNOWLEDGED = "acknowledged"
    IN_PROGRESS = "in_progress"
    RESOLVED_PENDING_CONFIRMATION = "resolved_pending_confirmation"
    RESOLVED = "resolved"
    NOT_SOLVED = "not_solved"
    REOPENED = "reopened"
    OUTCOME_UNKNOWN = "outcome_unknown"


class TicketVisibility(StrEnum):
    PRIVATE = "private"
    NEARBY = "nearby"
    LOCALITY = "locality"
    CITYWIDE = "citywide"


class ComplaintTicket(BaseModel):
    id: str
    civitas_ticket_id: str
    thread_id: str | None = None
    case_id: str | None = None
    owner_id: str
    title: str
    description: str
    authority_id: str | None = None
    locality: str | None = None
    status: TicketStatus = TicketStatus.DRAFT
    visibility: TicketVisibility = TicketVisibility.PRIVATE
    external_reference_id: str | None = None
    acknowledgement: str | None = None
    tracking_url: HttpUrl | None = None
    submitted_content_hash: str | None = None
    last_checkpoint_id: str | None = None
    public_post_id: str | None = None
    created_at: datetime
    updated_at: datetime


class CreateTicketRequest(BaseModel):
    thread_id: str | None = None
    case_id: str | None = None
    title: str = Field(min_length=3, max_length=180)
    description: str = Field(min_length=10, max_length=20000)
    authority_id: str | None = Field(default=None, max_length=80)
    locality: str | None = Field(default=None, max_length=160)
    visibility: TicketVisibility = TicketVisibility.PRIVATE

    @field_validator("title", "description", "locality", "authority_id")
    @classmethod
    def clean_ticket_text(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return value
        minimum = {"title": 3, "description": 10}.get(info.field_name, 1)
        label = info.field_name.replace("_", " ").title()
        return _required_text(value, minimum=minimum, label=label)


class UpdateTicketStatusRequest(BaseModel):
    status: TicketStatus
    note: str | None = Field(default=None, max_length=2000)


class TicketStatusEvent(BaseModel):
    id: str
    ticket_id: str
    status: TicketStatus
    note: str | None = None
    created_at: datetime


class TicketDetail(BaseModel):
    ticket: ComplaintTicket
    history: list[TicketStatusEvent] = Field(default_factory=list)


class CreatePublicPostRequest(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    body: str = Field(min_length=10, max_length=20000)
    locality: str | None = Field(default=None, max_length=160)
    visibility: Literal["nearby", "locality", "citywide"] = "locality"
    attachment_ids: list[str] = Field(default_factory=list, max_length=8)
    redaction_approved: bool = False
    redaction_content_hash: str | None = Field(default=None, min_length=32, max_length=128)
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("title", "body", "locality", "display_name")
    @classmethod
    def clean_post_text(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return value
        minimum = {"title": 3, "body": 10}.get(info.field_name, 1)
        label = info.field_name.replace("_", " ").title()
        return _required_text(value, minimum=minimum, label=label)


class CivicPost(BaseModel):
    id: str
    ticket_id: str
    civitas_ticket_id: str
    author_id: str | None = None
    author_name: str
    title: str
    body: str
    locality: str | None = None
    visibility: Literal["nearby", "locality", "citywide"]
    status: TicketStatus
    authority_id: str | None = None
    authority_name: str | None = None
    vote_score: int = 0
    upvotes: int = 0
    downvotes: int = 0
    comment_count: int = 0
    evidence_count: int = 0
    is_demo: bool = False
    created_at: datetime
    updated_at: datetime
    user_vote: int = 0
    is_following: bool = False
    is_owner: bool = False
    is_saved: bool = False
    is_muted: bool = False
    is_locked: bool = False
    moderation_state: Literal["visible", "hidden", "locked"] = "visible"
    ranking_score: float | None = None
    ranking_reasons: list[str] = Field(default_factory=list)


class PublicPostAttachment(BaseModel):
    id: str
    post_id: str
    filename: str
    content_type: str
    size_bytes: int = Field(ge=0)
    url: str


class PublicPostAttachmentListResponse(BaseModel):
    items: list[PublicPostAttachment] = Field(default_factory=list)


class FeedResponse(BaseModel):
    items: list[CivicPost] = Field(default_factory=list)
    next_cursor: str | None = None


class FeedQuery(BaseModel):
    sort: Literal["recent", "popular", "nearby", "following", "recommended"] = "recommended"
    locality: str | None = None
    visibility: Literal["nearby", "locality", "citywide"] | None = None
    status: TicketStatus | None = None
    authority_id: str | None = None
    topic: str | None = None
    limit: int = Field(default=30, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=256)


class Comment(BaseModel):
    id: str
    post_id: str
    author_id: str | None = None
    author_name: str
    body: str
    parent_id: str | None = None
    created_at: datetime
    updated_at: datetime
    is_demo: bool = False
    is_owner: bool = False
    moderation_state: Literal["visible", "hidden"] = "visible"
    is_deleted: bool = False


class CreateCommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    parent_id: str | None = None

    @field_validator("body")
    @classmethod
    def clean_comment(cls, value: str) -> str:
        return _required_text(value, minimum=1, label="Comment")


class CommentListResponse(BaseModel):
    items: list[Comment] = Field(default_factory=list)


class VoteResponse(BaseModel):
    post_id: str
    value: Literal[-1, 0, 1]
    upvotes: int = Field(ge=0)
    downvotes: int = Field(ge=0)
    vote_score: int


class VoteRequest(BaseModel):
    value: Literal[-1, 0, 1]


class FollowRequest(BaseModel):
    following: bool = True


class FollowResponse(BaseModel):
    post_id: str
    following: bool


class SaveRequest(BaseModel):
    saved: bool = True


class SaveResponse(BaseModel):
    post_id: str
    saved: bool


class MuteRequest(BaseModel):
    muted: bool = True


class MuteResponse(BaseModel):
    post_id: str
    muted: bool


class FollowSubjectRequest(BaseModel):
    subject_type: Literal["topic", "authority", "locality"]
    value: str = Field(min_length=1, max_length=160)
    following: bool = True

    @field_validator("value")
    @classmethod
    def clean_subject_value(cls, value: str) -> str:
        return _required_text(value, minimum=1, label="Follow value")


class FollowSubject(BaseModel):
    subject_type: Literal["topic", "authority", "locality"]
    value: str
    following: bool = True
    created_at: datetime


class FollowSubjectListResponse(BaseModel):
    items: list[FollowSubject] = Field(default_factory=list)


class ShareSnapshot(BaseModel):
    token: str
    post_id: str
    url: str
    expires_at: datetime
    revoked: bool = False


class CreateReportRequest(BaseModel):
    reason: Literal[
        "personal_information",
        "harassment",
        "spam",
        "threat",
        "misinformation",
        "duplicate",
        "other",
    ]
    details: str | None = Field(default=None, max_length=2000)

    @field_validator("details")
    @classmethod
    def clean_report_details(cls, value: str | None) -> str | None:
        return " ".join(value.strip().split()) if value else None


class Report(BaseModel):
    id: str
    target_type: Literal["post", "comment"]
    target_id: str
    reporter_id: str
    reason: str
    details: str | None = None
    status: Literal["open", "reviewing", "resolved", "dismissed"] = "open"
    created_at: datetime
    updated_at: datetime


class ModerationActionRequest(BaseModel):
    action: Literal["hide", "restore", "lock", "unlock", "resolve_report", "dismiss_report"]
    note: str | None = Field(default=None, max_length=2000)


class ModerationAction(BaseModel):
    id: str
    moderator_id: str
    target_type: Literal["post", "comment", "report"]
    target_id: str
    action: str
    note: str | None = None
    created_at: datetime


class ReportListResponse(BaseModel):
    items: list[Report] = Field(default_factory=list)


class ModerationActionListResponse(BaseModel):
    items: list[ModerationAction] = Field(default_factory=list)


class PrepareTicketRequest(BaseModel):
    fields: dict[str, str] = Field(default_factory=dict, max_length=30)
    attachment_ids: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("fields")
    @classmethod
    def clean_preparation_fields(cls, value: dict[str, str]) -> dict[str, str]:
        return {
            str(key).strip(): str(item).strip() for key, item in value.items() if str(item).strip()
        }


class TicketPreparation(BaseModel):
    id: str
    ticket_id: str
    civitas_ticket_id: str
    authority_id: str | None = None
    authority_name: str | None = None
    contact_route: str | None = None
    intake_url: HttpUrl | None = None
    destination: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)
    required_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    attachment_ids: list[str] = Field(default_factory=list)
    content_hash: str
    status: Literal["needs_information", "ready_for_review", "approved", "blocked"]
    submission_enabled: bool = False
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None = None
    approval_expires_at: datetime | None = None


class PreparationApproval(BaseModel):
    preparation_id: str
    ticket_id: str
    destination: str
    content_hash: str
    approved_at: datetime
    expires_at: datetime
    valid: bool = True
    submission_enabled: bool = False


class ApprovePreparationRequest(BaseModel):
    content_hash: str = Field(min_length=32, max_length=128)


class Checkpoint(BaseModel):
    id: str
    ticket_id: str
    preparation_id: str | None = None
    phase: Literal[
        "prepared",
        "reviewed",
        "awaiting_user",
        "submission_started",
        "outcome_unknown",
        "complete",
    ]
    summary: str
    created_at: datetime


class RecordOutcomeRequest(BaseModel):
    status: Literal[
        "submitted",
        "awaiting_confirmation",
        "acknowledged",
        "in_progress",
        "resolved_pending_confirmation",
        "resolved",
        "not_solved",
        "reopened",
        "outcome_unknown",
    ]
    external_reference_id: str | None = Field(default=None, max_length=160)
    acknowledgement: str | None = Field(default=None, max_length=4000)
    tracking_url: HttpUrl | None = None
    submitted_content_hash: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=2000)


class CheckpointListResponse(BaseModel):
    items: list[Checkpoint] = Field(default_factory=list)


class CompareSourcesRequest(BaseModel):
    source_id: str = Field(min_length=1, max_length=160)
    baseline_source_id: str = Field(min_length=1, max_length=160)


class DocumentChange(BaseModel):
    page: int = Field(ge=1)
    change_type: Literal["added", "removed", "changed"]
    before: str = ""
    after: str = ""
    source_id: str
    baseline_source_id: str


class DocumentComparison(BaseModel):
    source_id: str
    baseline_source_id: str
    current_title: str
    baseline_title: str
    authority: str
    status: Literal["draft", "proposed", "adopted", "historical", "unknown"]
    changes: list[DocumentChange] = Field(default_factory=list)
    summary: str
    uncertainties: list[str] = Field(default_factory=list)
    checked_at: datetime


class CreateNotificationRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=1000)
    post_id: str | None = None
    ticket_id: str | None = None


class NotificationReadRequest(BaseModel):
    read: bool = True


class Notification(BaseModel):
    id: str
    owner_id: str
    kind: str
    message: str
    post_id: str | None = None
    ticket_id: str | None = None
    read: bool = False
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[Notification] = Field(default_factory=list)


class ConnectorField(BaseModel):
    key: str
    label: str
    required: bool = False
    input_type: Literal["text", "textarea", "file", "location", "identifier"] = "text"
    description: str = ""


class ConnectorProfile(BaseModel):
    authority_id: str
    name: str
    contact_route: str
    intake_url: HttpUrl
    provider: Literal["local_fixture", "official_api", "browser"] = "local_fixture"
    capabilities: list[Literal["lookup", "prepare", "submit"]] = Field(default_factory=list)
    submission_enabled: bool = False
    verified_at: datetime
    requirements: list[ConnectorField] = Field(default_factory=list)


class ConnectorListResponse(BaseModel):
    items: list[ConnectorProfile] = Field(default_factory=list)


class LiveEndpointProfile(BaseModel):
    endpoint_id: str
    authority_id: str
    authority_name: str
    title: str
    url: HttpUrl
    source_kind: Literal["official", "official_reference"] = "official"
    transport: Literal["html", "pdf", "json", "csv", "api"] = "html"
    status: Literal[
        "ready",
        "requires_api_key",
        "approval_required",
        "consent_required",
        "browser_only",
        "offline",
        "disabled",
        "unknown",
    ] = "unknown"
    priority: Literal["P0", "P1", "P2"] = "P2"
    access_mode: Literal[
        "public_read",
        "api_key",
        "approval_required",
        "consent_required",
        "browser_only",
        "documentation",
    ] = "public_read"
    capabilities: list[str] = Field(default_factory=list)
    docs_url: str | None = None
    refreshable: bool = True
    consent_scope: str | None = None
    requires_env: str | None = None
    last_checked_at: datetime | None = None
    last_error: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    source_extraction_status: Literal["complete", "partial", "unreadable", "unknown"] = "unknown"


class LiveEndpointListResponse(BaseModel):
    items: list[LiveEndpointProfile] = Field(default_factory=list)


class LiveRefreshResult(BaseModel):
    endpoint: LiveEndpointProfile
    fetched: bool = False
    source_id: str | None = None
    bytes_fetched: int = Field(default=0, ge=0)
    pages_indexed: int = Field(default=0, ge=0)
    message: str
