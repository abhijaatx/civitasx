# Hermes-style Agent integration

CivitasX now uses a small, local adaptation of the interaction contracts that
make [Hermes Agent](https://github.com/NousResearch/hermes-agent) feel like a
durable coding agent:

- bounded per-turn iterations;
- structured plan, tool-start, tool-result, and completion events;
- persistent thread history with a reopenable trace;
- slash commands (`/research`, `/complaint`, `/sources`, `/memory`, `/model`,
  `/new`, `/help`);
- a live tool strip and a collapsed trace on every assistant response.

The runtime now also keeps the model-facing transcript durable across reloads:
tool calls and bounded tool results are reconstructed before the next turn,
older turns are compacted into a labelled context record, and private PDF/text
attachments can be inspected through a thread-scoped tool. Attachments on the
newest user turn are preflighted before provider selection, so the fallback
transport receives the same private, scoped evidence as Groq without
filesystem access. Each turn emits a high-level plan, runs independent
read-only tools concurrently, and performs a second evidence-grounding pass
before persisting the answer. Groq remains the primary provider with bounded
retries; the local read-only Codex CLI can be selected directly or used as the
automatic fallback, with its JSONL output consumed incrementally whenever the
CLI emits partial message events instead of waiting on a buffered process
result. Code and workspace requests are routed to the local Codex provider first
so a healthy Groq response cannot lose repository context; civic requests remain
Groq-first. Codex command/file inspection activity is surfaced as redacted live
Hermes events. Allowlisted live civic connectors
can be discovered and refreshed by the agent; submission and publication remain
outside the model tool boundary. Provider-reported input/output token counts are
carried into the turn trace and usage ledger when a transport supplies them.
Codex fallback sessions are resumable per private thread when session persistence
is enabled, so a later fallback turn can continue its local Codex context.
For code-oriented requests, that fallback may inspect repository files and run
read-only workspace commands, but it must not modify files, install software,
read environment files or credentials, inspect private attachment bytes, or
call external services. A code-change request can produce an explicit approval
card; only the user's approval action invokes a scoped `workspace-write` Codex
run, which is recorded back into the private thread. This preserves useful
Codex-style code reasoning without turning the public Agent tunnel into an
unrestricted shell.

The upstream checkout was used during extraction at commit
`01382698fc32ec7740b6a204d9b7a6abeac74d33` and then removed from the working
tree because the full 284 MB desktop/messaging runtime is not needed by the
web service. The lightweight adapted event and budget modules live in
`services/api/src/civitas_api/hermes_runtime/`, with the MIT text preserved in
`NOTICE-HERMES-MIT.txt` and the source recorded in `THIRD_PARTY_NOTICES.md`.

The civic boundary remains application-owned: model wording cannot invent an
official record, submit to a government portal, or publish to the Feed. The
local evidence gate and explicit review actions run outside the model loop.
That is the part that lets CivitasX preserve Hermes-style interaction while
remaining safe for civic work.
