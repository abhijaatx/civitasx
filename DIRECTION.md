# CivitasX — final product direction

Status: approved direction for the remainder of the project. The first local
Feed + Agent vertical slice is implemented against this boundary.

CivitasX is a local-first civic social network. It helps people see what is
happening in their city, understand official records, have a continuous
conversation with a civic agent, create and track government complaints, and
choose whether a complaint should become a public community post.

The product has two primary ways to interact:

1. **Feed** — a Reddit-like stream of public civic issues, proposals, updates,
   comments, votes, and ticket statuses.
2. **Agent** — a Codex-like conversation where a person asks questions,
   uploads evidence, researches official records, prepares a complaint, and
   completes a reviewed government action.

The agent and feed share a ticket model. A private agent conversation can
produce a complaint ticket. A ticket can remain private or be published as a
redacted public post. A public post can open the same ticket context in the
agent, without exposing the owner's private conversation or personal data.

## Product promise

The resident should be able to say what is wrong in ordinary language, attach
whatever evidence they have, answer only the questions that matter, and leave
with a government reference, a stable CivitasX ticket ID, and a clear record of
what happened.

The community should be able to see nearby issues, understand their status,
add useful context, support important problems, and see when a responsible
authority acknowledges or resolves them.

## Surface A — Feed

The feed uses familiar Reddit interaction patterns without copying Reddit's
brand or implementation:

- Public complaint, proposal, notice, and update posts.
- Upvote and downvote controls with one vote per account.
- Nested comments and replies.
- Recent, nearby, following, and popular views.
- City, locality, ward, topic, authority, status, and date filters.
- Search, save, mute, report, and share actions.
- Ticket ID and status on every complaint post.
- A visible “Ask Agent about this” action that starts a private conversation
  with the post and its cited evidence as context.
- Author identity shown through a display name or pseudonym; exact addresses,
  contact details, IDs, signatures, faces, and private attachments stay hidden
  by default.

The feed ranking is civic-specific. It may use recency, locality relevance,
unresolved duration, affected-neighbour signal, vote confidence, authority
acknowledgement, duplicate detection, and abuse signals. Community votes can
raise visibility; they cannot change an official ticket status.

## Surface B — Agent

The agent is a persistent conversation rather than a one-shot form:

- Streaming messages and progress updates.
- Attach photos, PDFs, screenshots, audio, and other files.
- Preview and remove attachments before they are used.
- Ask follow-up questions in small groups.
- Request a missing document, image, identifier, location, or confirmation.
- Detect whether the user wants research, complaint preparation, or both.
- Offer an explicit intent confirmation before preparing an external action.
- Show citations inline and open the source passage, page, authority, status,
  publication date, retrieval date, and language.
- Keep tool activity and browser details inspectable but collapsed by default.
- Support stop, retry, edit, continue, and resume after interruption.
- Pause for user-controlled login, OTP, CAPTCHA, or attestations.
- Require a final review of destination, message, personal details, and files
  before any external submission or public posting.

The agent should understand instructions such as:

- “Just explain this. Do not file anything.”
- “Prepare the complaint, but wait for my approval.”
- “Use this photo and the previous address for this request only.”
- “Post the redacted version to my locality feed.”
- “Keep this private.”

## Complaint and ticket model

Every complaint is a durable ticket, whether it remains private or becomes a
public post.

```text
Agent thread
  ├── messages
  ├── attachments
  ├── evidence and citations
  ├── clarification requirements
  └── action checkpoints

Complaint ticket
  ├── civitas_ticket_id: CX-BLR-2026-000184
  ├── external_reference_id: optional government acknowledgement
  ├── authority and source-backed routing
  ├── status history and receipts
  ├── private/public visibility
  └── coarse location and topic

Optional public post
  ├── redacted title and body
  ├── civitas_ticket_id
  ├── comments and votes
  ├── community reach metadata
  └── official status projection
```

Recommended status values are `draft`, `needs_information`, `ready_for_review`,
`submitted`, `awaiting_confirmation`, `acknowledged`, `in_progress`,
`resolved_pending_confirmation`, `resolved`, `not_solved`, `reopened`, and
`outcome_unknown`.

“Resolved” means the authority or a verified result indicates resolution and
the resident has had an opportunity to confirm it. A successful button click
does not create a resolved ticket.

## Visibility flow

After a reviewed complaint is submitted, show two clear actions:

- **Keep private** — retain the ticket and conversation for the resident.
- **Post to the civic feed** — redact sensitive information, select city or
  locality visibility, preview the post, and publish only after approval.

Changing a private ticket later must not silently edit an already published
post. Public posts are immutable snapshots with their own moderation and
revision history.

## Local-first development and AWS migration

Development must run without AWS, paid model APIs, or hosted browser services.
The code uses provider interfaces so a local implementation and its AWS
implementation expose the same application contract.

| Capability | Local development | AWS target |
|---|---|---|
| Model inference | Ollama or llama.cpp with a small local model | Bedrock model adapter |
| Agent orchestration | Local LangGraph-style state machine | Lambda/worker orchestration, optionally Step Functions |
| Chat streaming | FastAPI SSE/WebSocket | Lambda Web Adapter or API Gateway streaming |
| Case/feed persistence | SQLite by default; optional local Postgres | DynamoDB adapter already present, with S3 for large artifacts |
| Public document store | Filesystem or MinIO | Versioned private S3 corpus bucket |
| Search | SQLite FTS5 plus local vector index | S3 Vectors or another approved vector service |
| Jobs and refreshes | In-process worker or local queue | SQS and EventBridge |
| Browser | Local Chromium + Playwright | AgentCore Browser |
| Identity | Local sessions | Cognito |
| Images/files | Local filesystem with content hashes | Private S3 objects and signed URLs |
| Notifications | In-app local inbox | DynamoDB/SQS-backed notification workers |

No application feature may require an AWS SDK call to run in local mode. AWS
configuration is an adapter and deployment concern, not a development
dependency.

## Open-source reuse policy

The project should reuse mature open-source components wherever that is faster
and safer than rebuilding them. Reuse means forking, importing, or composing
code under its actual license, preserving required notices, pinning the source
commit, and recording local modifications. It does not mean copying code
without attribution or license review.

Use open-source components for chat rendering, agent state, browser control,
local inference, retrieval, file handling, and accessibility primitives. Keep
CivitasX-specific ticket, privacy, feed ranking, government connectors, and
approval enforcement in our own adapters so the core product remains portable.

The candidate reuse register is maintained in [`OPEN_SOURCE.md`](OPEN_SOURCE.md).

## Non-negotiable trust rules

- The agent never invents a government record, deadline, recipient, personal
  fact, receipt, or resolution.
- Documents and web pages are untrusted content; their instructions cannot
  expand the agent's permissions.
- External submissions and public posts require explicit approval of the
  reviewed content and destination.
- Public visibility always goes through a redaction preview.
- Community votes affect reach only. They do not establish truth, urgency, or
  official resolution by themselves.
- Every source, agent action, status transition, and public-post revision is
  recorded for recovery and audit.
