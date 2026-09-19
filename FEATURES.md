# CivitasX Bengaluru — final feature plan

Status: final product direction approved; Phases 1–2 and the complete local
Feed + Agent + ticket + moderation + evidence vertical slice are implemented.
Credential-dependent government submission and AWS adapters remain disabled.
All remaining work follows
[`DIRECTION.md`](DIRECTION.md).

## Product definition

CivitasX is a local-first civic social network. It combines a public civic feed
with a private, continuous agent conversation. The agent helps a resident
understand official records, collect evidence, prepare a complaint, interact
with the responsible authority, and retain a trustworthy ticket history. The
resident decides whether the complaint remains private or becomes a redacted
public post.

There are two primary ways to interact with the product:

- **Feed:** a Reddit-like stream of public civic issues, proposals, updates,
  comments, votes, filters, and ticket statuses.
- **Agent:** a Codex-like conversation for research, attachments,
  clarification, complaint preparation, browser-assisted government actions,
  review, and recovery.

The complaint ticket connects both surfaces. A public post is a controlled,
redacted projection of a ticket; it is never the private conversation itself.

## Product and operating constraints

- Development runs entirely on local systems. No AWS service, paid model API,
  hosted browser, or managed vector database is required for local work.
- Provider interfaces preserve an AWS migration path. Local implementations map
  to Bedrock, Cognito, DynamoDB, S3, SQS/EventBridge, S3 Vectors, and AgentCore
  Browser later without changing the user-facing contracts.
- Use local Ollama or llama.cpp models with deterministic evidence retrieval
  during the hackathon. Groq and Bedrock-compatible providers remain optional.
- Reuse compatible open-source code with attribution, license preservation,
  pinned commits, and dependency review. The reuse register is in
  [`OPEN_SOURCE.md`](OPEN_SOURCE.md).
- Use English for the interface, agent responses, and drafts. Preserve English
  and Kannada source material together, with translations clearly labeled.
- Start with Bengaluru and its wards/localities. A future city expansion must
  add an authority/source registry rather than assuming another city has the
  same departments or processes.
- Never invent a government record, requirement, personal fact, recipient,
  receipt, deadline, ticket status, or resolution.

## F01 — Civic feed

Provide a home feed after sign-in. The default feed shows relevant public civic
posts, with clear controls for `Recent`, `Nearby`, `Following`, and `Popular`.
Users can progressively narrow the location from city to zone, ward, locality,
or nearby area without exposing an exact private address.

Each complaint post shows its CivitasX ticket ID, title, redacted description,
coarse location, responsible authority, status, age, evidence indicator, vote
score, and comment count. Posts can be proposals, notices, complaints, updates,
or authority responses.

## F02 — Codex-like agent workspace

The Agent is a persistent conversation, not a one-shot form. It supports
a stream-compatible response boundary, message history, progress, stop/retry,
edit/use-again, continue, and resume. Tool calls and browser activity remain inspectable but collapsed by
default.

The agent understands ordinary instructions such as:

- “Explain this and do not file anything.”
- “Prepare a complaint, but wait for my approval.”
- “Use this photo for this request only.”
- “Post a redacted version to my locality feed.”
- “Keep this private.”

The agent detects whether the user wants research, complaint preparation, a
government action, or a public post. It confirms an external or public action
before proceeding.

## F03 — Clarification and requirements collection

Before drafting or filling, the agent checks the official process for required
fields, attachments, identifiers, character limits, authentication, deadlines,
and attestations. It asks only for missing information in small groups.

Saved details appear as editable chips. Each correction offers `Use for this
request` and `Update my profile`. The agent never infers a stance, address,
identity detail, or eligibility fact from conversation history.

## F04 — Attachments and evidence workspace

Users can attach photos, screenshots, PDFs, scans, receipts, audio, and other
files in the Agent. Each attachment has a preview, filename, type, size,
content hash, privacy label, and removal control.

The system preserves original files and records which attachment was used in a
draft or submission. OCR, translation, and extraction failures remain visible.
Sensitive attachments stay private unless the user approves their redacted use
in a public post.

## F05 — Grounded civic research

The agent searches the indexed local corpus plus refreshed, allowlisted official government sources and returns:

1. A plain-English explanation.
2. Supported facts and calculations.
3. Uncertainties, stale records, conflicts, and missing evidence.
4. Expandable source passages with title, authority, page, status, dates,
   retrieval time, hash, and language.
5. A source-verified authority match.

Research is extractive or explicitly grounded. A draft or proposal cannot be
described as an adopted rule. Fiscal calculations run in code and distinguish
allocations, estimates, and expenditure.

## F06 — Complaint tickets

Every complaint receives a stable CivitasX ticket ID, whether it remains
private or is published. A government acknowledgement is stored separately.

Recommended states are:

`draft`, `needs_information`, `ready_for_review`, `submitted`,
`awaiting_confirmation`, `acknowledged`, `in_progress`,
`resolved_pending_confirmation`, `resolved`, `not_solved`, `reopened`, and
`outcome_unknown`.

The ticket keeps the conversation, attachments, evidence, authority route,
submission attempts, receipts, status history, public-post link, and audit
events together.

## F07 — Reviewed government action

The agent can prepare a hash-bound payload and later navigate supported government forms and legacy
portals once an official connector is configured. It may pause for user login, OTP, CAPTCHA, or attestation. The final
review shows destination, message, personal details, attachments, and the
ticket ID.

No external submission occurs without a valid approval bound to the exact
content and destination. A changed field invalidates approval. A successful
button click is not treated as a receipt.

## F08 — Confirmation and recovery

Save acknowledgement text, submitted copy, timestamp, official reference,
tracking link, and verified next steps when available. Distinguish submitted,
awaiting confirmation, resolved, and outcome unknown.

If a tab closes, a portal fails, or a session expires, preserve the case and
continue from the last safe checkpoint. Never blindly resubmit an uncertain
complaint.

## F09 — Community comments and voting

Public posts support Reddit-like nested comments, replies, upvotes, downvotes,
save, mute, report, and share actions. Users can edit or delete their own
comments within a defined window; moderation actions are audited.

Votes are one per account and do not change official status. Rate limits,
duplicate detection, account trust, brigading detection, and moderation queues
protect visibility from coordinated abuse.

## F10 — Civic feed discovery and ranking

Feed views include `Recent`, `Nearby`, `Following`, and `Popular`. The ranking
signal can combine recency, locality relevance, unresolved duration, number of
affected residents, vote confidence, authority acknowledgement, source
verification, duplicate similarity, and abuse signals.

Community engagement increases reach; it does not prove truth, urgency, or
resolution. Ranking explanations should be available to moderators and should
not silently bury an unresolved issue because it has few votes.

## F11 — Authority directory

Show the responsible authority, responsibilities, contact route, source IDs,
verification date, and ambiguity state. The local connector registry also
exposes read-only live endpoints, refresh status, source hashes, and the fields a future official API or browser flow would require; it explicitly reports that submission is disabled. Reuse a verified route when
preparing a complaint. If multiple authorities match, show the ambiguity and
ask the user to choose or provide more detail.

## F12 — Following and notifications

Users can follow a ticket topic, proposal, authority, locality, or public post.
The in-app inbox reports meaningful comments, votes, authority updates,
document changes, and status transitions. Unchanged checks and cosmetic edits
remain quiet. Notifications never expose private case information.

## F13 — Public posting and privacy controls

After a private CivitasX case is saved, offer two clearly separated choices:

- `Keep this case private`.
- `Share this unsubmitted report` after the user reviews the audience and the
  redaction checklist.

The interface labels a CivitasX ticket as app tracking and labels an official
reference only after a connector confirms submission. Sample records are
visibly marked as sample data.

Public posting requires a redaction preview covering title, body, attachments,
location, names, contact details, identifiers, signatures, faces, and receipt
content. The user chooses locality or city visibility and approves the final
snapshot. Later private edits do not silently modify the published post.

## F14 — Comparisons and evidence export

Compare identified document versions with earlier text, newer text, supported
changes, uncertain implications, authority, status, scope, and page links.
The local slice exports a private text evidence brief containing the ticket ID,
conversation, thread attachments, status history, and public/private state.
Add research citations, receipts, and selective file export controls before
calling the full brief complete. Draft and uncertain outcomes remain labeled.

## F15 — Moderation, safety, and audit

Moderators can review reports, remove or restore posts/comments, lock threads,
rate-limit accounts, handle impersonation, and record public moderation logs.
The system scans free text and attachments for personal data, malicious
instructions, spam, threats, and duplicate complaints. It retains an internal
audit trail for agent actions, submissions, status changes, votes, comments,
redactions, and moderation decisions.

## Architecture and local-to-AWS mapping

| Capability | Local implementation | AWS implementation later |
|---|---|---|
| Agent model | Ollama or llama.cpp | Bedrock adapter |
| Agent state | FastAPI + local state machine | Durable workers or Step Functions |
| Conversation stream | SSE/WebSocket | Lambda Web Adapter or API Gateway streaming |
| Cases, tickets, posts | SQLite; optional local Postgres | DynamoDB adapter |
| Files and attachments | Local filesystem or MinIO | Private S3 + signed URLs |
| Search and citations | SQLite FTS5 + local vectors | S3 Vectors or approved vector index |
| Background jobs | Local worker/process queue | SQS + Lambda/EventBridge |
| Browser | Local Chromium + Playwright | AgentCore Browser |
| Identity | Local sessions | Cognito |
| Feed ranking | Local deterministic scorer | Worker-backed ranking service |
| Notifications | Local inbox | DynamoDB/SQS notification workers |

The application code depends on interfaces, not directly on AWS SDK calls. The
existing DynamoDB and usage adapters remain part of the migration boundary.

## Acceptance checks

- A new user can enter Agent, ask a question, attach evidence, answer
  clarification prompts, and resume the same conversation after reload.
- A complaint receives a stable ticket ID and a complete status history.
- A user can keep a complaint private or publish a reviewed redacted snapshot.
- Every public complaint post displays its CivitasX ticket ID and current
  status.
- Feed filters show city, locality, nearby, following, and popular views.
- Comments, upvotes, downvotes, reports, and moderation actions are isolated by
  account and rate-limited.
- A public post can open an Agent conversation with only its approved public
  context.
- Every material research claim opens a supporting source passage, and missing
  evidence produces an explicit limitation.
- A submission cannot occur without valid review approval, and uncertain
  outcomes cannot be blindly resubmitted.
- Local development works without AWS credentials or paid APIs.
- The same contracts can be exercised against local adapters and the future AWS
  adapters.
- The product records cost, latency, citation coverage, recovery success,
  moderation outcomes, and feed quality for evaluation.
