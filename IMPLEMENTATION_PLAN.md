# CivitasX local-first implementation plan

Status: planning complete; the local Feed + Agent + ticket + review + community
vertical slice is implemented and verified. Remaining items are production
provider replacement, live government connectors, and evaluation hardening.

This plan turns [`DIRECTION.md`](DIRECTION.md) into tasks that can be built and
tested without AWS or paid APIs. Each local provider has the same boundary as
its future AWS provider. The first complete slice is intentionally narrow
enough to demo end to end:

```text
sign in → Feed → Agent conversation → attachment → complaint ticket
        → private/public choice → redacted feed post → vote/comment/status
```

## Build order

### A. Contracts and local adapters

- [x] Finalize Feed and Agent as the only primary surfaces.
- [x] Define the ticket as the shared private/public record.
- [x] Define local-to-AWS provider mapping.
- [x] Record open-source reuse and license rules.
- [x] Add agent thread, message, attachment, ticket, post, comment, vote, and
  notification contracts.
- [x] Add SQLite and DynamoDB/S3 implementations behind the same API/MCP
  community contract.
- [x] Add explicit community storage seams, additive SQLite migrations, and
  monotonic cloud ticket allocation.

### B. Agent vertical slice

- [x] Add persistent multi-turn agent threads.
- [x] Add a provider-backed ReAct loop with JSON-schema civic tools, bounded
  iterations, and multi-turn tool results.
- [x] Let the model decide whether to research, resolve a location, draft a
  complaint, or compare documents instead of routing through keyword matching.
- [x] Stream model deltas and real tool calls/results over SSE.
- [x] Add attachment upload, hash, metadata, and case/thread scoping.
- [x] Render persistent messages, citations, clarification states, and action
  cards in the React client.
- [x] Keep local connector requirements and authority routes inspectable from
  the API without enabling submission.
- [x] Add an event-driven SSE response boundary for Ollama and Groq-compatible
  providers; provider failure is surfaced without inventing civic facts.

### C. Complaint and ticket slice

- [x] Generate stable `CX-BLR-YYYY-NNNNNN` IDs.
- [x] Store government acknowledgement IDs, receipt text, hashes, and tracking links separately.
- [x] Add status history and resolution states.
- [x] Add private ticket creation from an Agent thread.
- [x] Add explicit `Keep private` and `Post to civic feed` actions.
- [x] Add redaction preview before public publication.
- [x] Add a private evidence-brief export for offline review.

### D. Feed and community slice

- [x] Make Feed the signed-in landing surface.
- [x] Add recent, nearby, following, and popular views.
- [x] Add locality/topic/authority/status filters.
- [x] Add public ticket posts with IDs, statuses, and coarse locations.
- [x] Add one-account-one-vote upvotes/downvotes.
- [x] Add comments and reply-ready comment records.
- [x] Add post follows and a local activity inbox.
- [x] Add reports, moderation state, basic abuse rate limits, moderation actions,
  nested comments, saves, mutes, shares, and follows.
- [x] Add “Ask Agent about this” with public context only.

### E. Government connector boundary

- [x] Define an authority connector registry with capabilities and freshness.
- [x] Keep local fixture connectors for GBA, BDA, and BMRCL.
- [x] Add a connector interface for official APIs, document repositories, and
  legacy browser workflows.
- [x] Return source status, last refresh, route, and unsupported-operation
  reasons in the Agent context.
- [x] Add a hash-bound preparation and approval boundary with submission disabled.
- [x] Add read-only live endpoint refreshes with an allowlist, cached source
  hashes, and explicit API-key/offline states.
- [ ] Replace local connectors one at a time with AWS workers after contract
  tests pass.

### F. Quality and migration

- [x] Add local end-to-end tests for research, complaint, feed, privacy,
  comments, voting, status, and recovery.
- [x] Add private/public and cross-owner attachment boundary tests.
- [x] Add `THIRD_PARTY_NOTICES.md` and the open-source reuse register; generate
  the dependency SBOM before release.
- [x] Run the local product with no AWS credentials and no paid API keys.
- [ ] Deploy a shadow AWS environment only after local tests are green.

## Reuse decisions

- Use LibreChat/OpenHands patterns for chat composition, streaming events,
  attachments, and resumable threads.
- Use LangGraph-compatible state transitions for the Agent, while keeping the
  civic tool boundary application-owned.
- Evaluate browser-use and BrowserGym for browser execution and testing.
- Use llama.cpp or Ollama for local model inference.
- Keep the social feed thin and product-specific rather than importing a full
  Reddit clone. Lemmy and Discourse are behavior references because their
  copyleft licenses require a separate architectural decision.

## Definition of “ready for visual review”

The first implementation pass is ready for product review when a tester can:

1. Sign in and land on Feed.
2. Open Agent and have a multi-turn conversation.
3. Attach a photo or PDF and see its preview.
4. Ask for a complaint and answer missing-information prompts.
5. Create a ticket with a stable ID.
6. Keep it private or preview a redacted public post.
7. See the post in Nearby or Recent.
8. Upvote, downvote, comment, and change the ticket status.
9. Open the post in Agent without exposing private data.
10. Reload and resume the ticket and conversation.
