# CivitasX contracts

The final product boundary is documented in the repository root's
[`DIRECTION.md`](../../DIRECTION.md). This file describes the currently
implemented local wire shapes for cases, research, Agent threads, attachments,
resident profiles, durable agent runs, tickets, feed posts, comments, votes,
follows, moderation, preparation checkpoints, comparisons, shares, and
notifications. Government submission remains an explicit credential-dependent
boundary.

`civitas_api.models` is the runtime source of truth for the Pydantic contracts.
The browser client mirrors the public shapes in `apps/web/src/types.ts`.
FastAPI exposes the complete generated schema at `/openapi.json` when the
service is running.

## Public request flow

1. `GET /api/config` → safe auth mode, local capabilities, and operating limits.
2. `POST /api/auth/register` or `POST /api/auth/login` → `{user, access_token, token_type}`. Local pilot recovery uses `POST /api/auth/recover` with an administrator-provided recovery code when configured.
3. Send `Authorization: Bearer <access_token>` to `/api/me`, `/api/cases`, and nested private endpoints.
4. `POST /api/cases` with `{goal, title?}` → a `CivicCase` in `saved` status.
5. `PATCH /api/cases/{id}` with `{goal?, title?, notes?, version}` → next case version or `409` on a stale version.
6. `POST /api/research` with `{question, case_id?, max_sources?}` → a citation-first `ResearchAnswer`.
7. `GET /api/cases/{id}/research` → saved `ResearchAnswer` records for that owner.
8. `GET /api/sources/{source_id}` → document metadata and page-level passages.
9. `GET /api/authorities` → source-verified authority records.
10. `GET /api/connectors` → local fixture requirements, authority routes, and
    explicit submission capability state.
11. `GET /api/agent/threads` and `POST /api/agent/threads/{id}/messages` → a
    persistent Agent conversation with clarification, citation, and action parts.
12. `POST /api/tickets` → a stable private complaint ticket; `POST
    /api/tickets/{id}/publish` requires `redaction_approved: true`.
13. `GET /api/feed` → public ticket projections with explainable `recommended`,
    recent, nearby, following, and popular views. Vote, follow, save, mute,
    comment, report, share, and activity endpoints operate on the same public
    post ID.
14. `POST /api/tickets/{id}/prepare` and `/preparation/approve` → local
    requirements check, content hash, review gate, and durable checkpoint. The
    approval reports whether the selected connector is enabled; local mode is
    preparation-only, the reviewed iPGRS connector opens a supervised browser
    run, and a configured certified official API uses an idempotency key and a
    verified authority reference.
15. `POST /api/sources/compare` → page-linked document comparison. `GET
    /share/{token}` → an expiring redacted snapshot with no private case data.
16. `GET /api/live/connectors` and `POST
    /api/live/connectors/{endpoint_id}/refresh` → allowlisted read-only fetches
    from public government portals. Responses are cached, hashed, and indexed
    as live source records; missing API keys are reported explicitly.

## Private child records

- `POST /api/cases/{id}/artifacts` with `{name, content, kind}` → `Artifact`.
- `GET /api/cases/{id}/events` → `{items: TaskEvent[]}`.
- `GET /api/cases/{id}/usage` → `UsageSummary`.

Every child lookup first verifies that the bearer-token subject owns the case.
An owner mismatch returns the same `404` shape as a missing private object.

## MCP

The stateless Streamable HTTP endpoint is `/mcp`. Every request requires the
same bearer token as the REST API. The available tools are:

- `get_civitas_status()`
- `list_my_cases(limit?)`
- `create_case(goal, title?)`
- `get_my_case(case_id)`
- `search_civic_records(question, max_sources?)`
- `research_my_case(case_id, question?, max_sources?)`
- `get_authority_directory(query?)`
- `get_civic_source(source_id)`
- `compare_civic_sources(source_id, baseline_source_id)`
- `get_civic_feed(sort?, locality?, topic?, limit?)`
- `get_my_ticket(ticket_id)`
- `get_resident_profile()`
- `remember_profile_value(key, value, source?, confirmed?, remember?)`
- `forget_profile_value(key)`
- `create_agent_thread(goal, title?, case_id?)`
- `list_agent_threads(limit?)`
- `get_agent_thread(thread_id)`
- `send_agent_message(thread_id, content, attachment_ids?, client_message_id?)`
- `prepare_submission(ticket_id, fields?, attachment_ids?)`
- `get_submission_review(ticket_id)`
- `approve_submission(ticket_id, content_hash)`
- `submit_approved_request(ticket_id, content_hash, simulation?)` → a synthetic
  receipt in demo mode or a supervised iPGRS run that pauses for classification,
  OTP, and CAPTCHA.
- `resume_run(run_id, verification_code?, send_otp?, submit?, resident_attestation?)`
  → continue the supervised run without persisting the verification code;
  final submission requires the resident attestation.
- `cancel_run(run_id)` → close the supervised browser and cancel the run.
- `prepare_public_post(ticket_id, title, body, locality?, visibility?, display_name?, attachment_ids?)`
- `publish_approved_post(ticket_id, title, body, redaction_content_hash, locality?, visibility?, display_name?, attachment_ids?, redaction_approved)`
- `get_run_status(run_id)`
- `resume_run(run_id)`
- `cancel_run(run_id)`
- `list_live_sources()`

Tool arguments never include an owner ID. The transport middleware derives the
owner from the bearer token and passes it through a request context.
