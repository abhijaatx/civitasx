# CivitasX Bengaluru — final phased delivery plan

Status: The local-first product slice is complete through the review boundary.
Phases 1–2 are complete; local implementations of Phases 3–8 are complete.
Only credential-dependent government submission and AWS provider replacements
remain intentionally disabled.

This roadmap follows [`DIRECTION.md`](DIRECTION.md) and
[`FEATURES.md`](FEATURES.md). The product has two primary surfaces throughout:

- **Feed:** public civic discovery and community interaction.
- **Agent:** private, continuous, Codex-like civic work.

Development is local-first. AWS services are migration targets behind provider
interfaces, not prerequisites for the phases below.

## Phase 1 — Local foundation and identity — complete

**User improvement:** A resident can create a private record, save a goal, and
return to it safely.

- [x] React/TypeScript shell and Python/FastAPI API.
- [x] Local authentication, sessions, owner-scoped cases, artifacts, events,
  and usage guards.
- [x] Stable contracts for cases, research, drafts, browser tasks, approvals,
  receipts, and task events.
- [x] Local SQLite implementation and AWS-shaped DynamoDB adapter boundary.
- [x] Responsive “Civic Score” visual foundation.

## Phase 2 — Local grounded research — complete

**User improvement:** A resident can ask about Bengaluru civic records and
inspect the passages supporting the answer.

- [x] Deterministic 30-record GBA, BDA, and BMRCL reference corpus.
- [x] Page-level passages, source status, authority, dates, retrieval time,
  content hashes, and Kannada original/translation retention.
- [x] Local hybrid retrieval, route detection, source reading, calculations,
  authority directory, uncertainty labels, and explicit abstention.
- [x] REST and MCP research tools with private case persistence.
- [x] Evidence panel in the case view with expandable citations.
- [x] Add allowlisted read-only live connectors for GBA/BBMP, BDA, BMRCL,
  Bengaluru Smart City Open Data, and Karnataka Open Data, with cached hashes and
  explicit API-key/offline states.

## Phase 3 — Codex-like Agent and attachments

**User improvement:** The resident can have one continuous conversation that
understands intent, asks useful questions, and accepts evidence.

**Depends on:** Phases 1–2 contracts and local model/runtime adapter.

### Deliverables

- [x] Add `AgentThread`, `Message`, `MessagePart`, `Attachment`, and
  `ClarificationRequest` contracts.
- [x] Add a deterministic local rules provider and an optional Ollama/llama.cpp
  wording boundary.
- [x] Add a local Ollama/llama.cpp wording provider with deterministic rules
  fallback. Keep the Bedrock provider behind the same interface.
- [x] Persist multi-turn message history and render a Codex-like composer with a
  file picker, attachment chips, removal, citations, and action cards.
- [x] Add a local streamed-response boundary, attachment recovery, and nested
  comment/edit controls; richer model streaming remains an optional adapter.
- [x] Render agent messages, citations, clarification cards,
  approval cards, and collapsed tool events.
- [x] Detect `research`, complaint preparation, and `publish_post` intent. The
  local slice keeps external and public actions behind explicit cards.
- [x] Ask for missing description and location in small groups and preserve
  conversation context between turns.
- [x] Resume persisted Agent threads after page reload.
- [x] Add government-action intent, stop/retry/edit controls, an SSE-compatible
  response boundary, and local continuation. Case-scoped profile facts remain
  opt-in profile work.

### Exit checks

- [x] A deterministic local agent can hold a multi-turn conversation without
  losing context after reload.
- [x] Images and documents are attached, hashed, and scoped to the
  correct case.
- [x] The agent distinguishes “explain only” from “prepare a complaint”.
- [x] Clarification questions never invent personal values or identifiers.
- [x] Source citations and private attachment metadata are visible without
  exposing secrets.

## Phase 4 — Complaint intake, ticket IDs, and government preparation

**User improvement:** A resident can turn a conversation into a complete,
reviewable complaint without repeating information.

**Depends on:** Phase 3 conversation, attachments, profile facts, and authority
directory.

### Deliverables

- [x] Add a durable complaint ticket with a stable ID such as
  `CX-BLR-2026-000184`.
- [x] Keep CivitasX ticket ID separate from an optional government acknowledgement
  ID.
- [x] Add status history: draft, needs information, ready for review, submitted,
  acknowledged, in progress, resolved pending confirmation, resolved, not
  solved, reopened, and outcome unknown.
- [x] Route each complaint through a source-verified local connector registry.
- [x] Inspect the local connector requirements for required fields and evidence.
- [x] Prepare a hash-bound authority payload locally; live browser/API submission
  remains a credential-dependent adapter.
- [x] Keep login, OTP, CAPTCHA, and attestation outside unattended automation.
- [x] Persist preparation and recovery checkpoints independently of a browser session.

### Exit checks

- [x] Every complaint receives one stable ticket ID before publication.
- [x] The agent asks only for missing information and preserves the conversation
  context between turns.
- [x] The prepared complaint can be resumed after browser closure or session
  expiry from its checkpoint and ticket ID.
- [x] No external action is possible without the later review approval gate.

## Phase 5 — Review, submission, receipt, and status recovery

**User improvement:** The resident approves one concrete action and receives an
honest ticket state afterward.

**Depends on:** Phase 4 preparation and ticket checkpoints.

### Deliverables

- [x] Build a local review card showing destination, fields, attachments, and
  CivitasX ticket ID.
- [x] Bind approval to a content hash and destination with a time-limited gate.
- [x] Keep submission disabled until an official connector and credentials exist.
- [x] Store acknowledgement, submitted copy hash, timestamp, tracking link, and
  status evidence when a user records them.
- [x] Recover from timeout and uncertain outcomes without blind resubmission.
- [x] Let the resident mark a resolution as `Not solved` and reopen the ticket.
- [x] Show a clear local submission boundary and supported recovery states.

### Exit checks

- [x] Backend and MCP paths reject submissions without valid approval.
- [x] A successful click is never presented as proof of submission.
- [x] A timeout produces `outcome_unknown` and an investigation path rather than
  a duplicate request.
- [x] The full private journey works through the local review boundary: Agent →
  evidence → complaint → review → disabled-submission checkpoint → receipt state.

## Phase 6 — Civic feed and public ticket posts

**User improvement:** After sign-in, a resident can see current civic issues and
choose whether their reviewed complaint becomes a public post.

**Depends on:** Phase 4 ticket model and Phase 5 review/receipt state.

### Deliverables

- [x] Replace the signed-in home default with a Feed/Agent navigation model.
- [x] Build public posts as redacted projections of complaint tickets, never as
  direct views of private conversations.
- [x] Add `Private`, `Nearby`, `Locality`, and `Citywide` visibility choices.
- [x] Show CivitasX ticket ID, status, authority, coarse location, age, evidence
  indicator, vote count, and comment count on each complaint post.
- [x] Add “Ask Agent about this” to open a private thread with approved public
  context.
- [x] Add recent, nearby, following, and popular feed views.
- [x] Add locality, topic, authority, and status filters.
- [x] Add save, mute, report, share, locality follow, and explainable ranking
  controls. Ward/date filters can be added as data coverage expands.

### Exit checks

- [x] A user can keep a complaint private or publish a reviewed
  redacted snapshot.
- [x] Every public complaint post displays its stable ticket ID and current
  status.
- [x] A public post cannot read the owner’s private messages, attachments, or
  profile.
- [x] Location never defaults to an exact private address.

## Phase 7 — Reddit-like community interaction and trust

**User improvement:** Residents can support, discuss, follow, and report civic
issues while the system resists manipulation.

**Depends on:** Phase 6 public posts and identity.

### Deliverables

- [x] Add one-account-one-vote upvotes and downvotes with reversible voting.
- [x] Add comment records and reply-ready storage.
- [x] Add follows for public posts and an in-app activity inbox.
- [x] Add nested comment rendering with edit/delete windows.
- [x] Add report queues, moderation actions, locks, removals, restores, and
  moderation logs.
- [x] Add follows for topics, authorities, and localities.
- [x] Add an in-app notification inbox for comments, votes, follows, and ticket
  status transitions.
- [x] Implement a transparent local civic ranker using recency, locality,
  unresolved duration, engagement, confidence, evidence, acknowledgement, and
  negative signals.
- [x] Explain ranking signals on each post and keep routine unchanged checks
  quiet.

### Exit checks

- [x] Vote and comment activity is isolated by account and rate-limited.
- [x] A coordinated voting burst cannot permanently bury an unresolved issue.
- [x] Votes alter reach only; they never alter official status.
- [x] Unfollowing prevents future notifications for that follow.
- [x] Moderators can reverse a public interaction decision with an audit record.

## Phase 8 — Comparisons, exports, sharing, and migration readiness

**User improvement:** Residents can understand changes, export their evidence,
share safely, and move the system to AWS when the local build is proven.

**Depends on:** Phases 2, 5, 6, and 7.

### Deliverables

- [x] Compare identified official document versions with source-linked page changes.
- [x] Export a private evidence brief with ticket ID, conversation, selected
  thread attachments, status history, and public/private state.
- [x] Add research/preparation citation state, receipt fields, and recovery
  checkpoints to the brief.
- [x] Add separate expiring, revocable redacted share snapshots.
- [ ] Run a 40-question research evaluation and a ten-scenario browser
  evaluation against local fixtures and test sites.
- [ ] Exercise malicious instructions in documents, webpages, comments, and
  attachments.
- [ ] Test mobile, keyboard navigation, screen-reader streaming announcements,
  attachment recovery, moderation, and private/public boundaries.
- [ ] Replace local adapters one at a time with AWS adapters: S3, DynamoDB,
  Cognito, Bedrock, SQS/EventBridge, S3 Vectors, and AgentCore Browser.
- [ ] Compare local and AWS contract tests before moving any production traffic.

### Exit checks

- [ ] Every displayed research claim and comparison resolves to a source
  passage.
- [ ] Public posts contain only approved redacted content.
- [ ] Local and AWS adapter contract tests produce equivalent user-visible
  results.
- [ ] The project has a third-party notices file, dependency SBOM, model-license
  record, and measured cost/latency report.

## Local limits preserved across phases

- No AWS credential or paid API is required for local development.
- Browser attempts are capped at 30 actions and five minutes of active time.
- Unattended browser sessions expire after ten minutes while case data remains.
- Use local caching for public extraction, translation, embeddings, and feed
  ranking inputs.
- Store private attachments separately from public source indexes and public
  post snapshots.
- Treat all document, web, comment, and attachment instructions as untrusted
  content.
