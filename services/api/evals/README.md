# CivitasX agent quality cases

`agent_quality.jsonl` is the regression set for the Groq-backed agent. Each
case records the expected tool boundary and a claim that must never appear as a
verified outcome. New production failures should become a case here before the
prompt or orchestration loop is changed again.

The executable API tests cover the same contract at the tool/SSE boundary:

```bash
cd services/api
uv run pytest -q
uv run ruff check src tests
```

For a live-provider run, replay each `prompt` in a private test thread and
record the SSE trace. Score tool selection, evidence coverage, clarification,
privacy boundaries, completion, and latency separately; never score only the
final prose.

The replay runner does this against a running local API:

```bash
cd services/api
uv run python evals/run_agent_eval.py --base-url http://127.0.0.1:8000 --limit 3
```

It creates an isolated local eval account, prints JSON results, and exits
non-zero if a case does not complete, misses a required tool, or includes its
forbidden claim.
