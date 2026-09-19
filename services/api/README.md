# CivitasX API — local Feed + Agent slice

The local service provides authentication, owner-scoped case storage, grounded
civic research, a verified authority directory, persistent Agent threads,
attachments, complaint tickets, a redacted civic Feed, comments, votes,
follows, and an activity inbox. Browser preparation and external form
submission remain behind the next connector phase.

## Run locally

```bash
cd services/api
uv sync
CIVITAS_SQLITE_PATH=../.civitas/dev.sqlite3 uv run uvicorn civitas_api.main:app --reload --port 8000
```

The default mode is local authentication and SQLite. The browser UI expects
`http://localhost:8000` unless `VITE_API_URL` is set.

Useful configuration:

| Variable | Default | Purpose |
|---|---|---|
| `CIVITAS_AUTH_MODE` | `local` | `local` for development; `cognito` for production |
| `CIVITAS_LOCAL_RECOVERY_CODE` | unset | Optional secret that enables local-pilot password recovery; never commit it |
| `CIVITAS_STORAGE` | `sqlite` | `sqlite` locally; `dynamodb` is an explicit cloud adapter boundary |
| `CIVITAS_SQLITE_PATH` | `.civitas/civitas.sqlite3` | Local database path |
| `CIVITAS_FRONTEND_ORIGIN` | `http://localhost:5173` | CORS origin |
| `CIVITAS_GLOBAL_BUDGET_USD` | `80` | Admission ceiling for paid operations |
| `CIVITAS_BROWSER_CONCURRENCY` | `2` | Reserved browser slots |
| `CIVITAS_COGNITO_REGION` | unset | Cognito issuer region in production |
| `CIVITAS_COGNITO_USER_POOL_ID` | unset | Cognito user pool in production |
| `CIVITAS_COGNITO_CLIENT_ID` | unset | Cognito app client in production |
| `CIVITAS_ARTIFACTS_BUCKET` | unset | Private S3 bucket reserved for cloud artifact offload |
| `CIVITAS_CORPUS_PATH` | `data/corpus/manifest.json` | Versioned public-source manifest |
| `CIVITAS_CORPUS_BUCKET` | unset | Cloud bucket reserved for original source bundles |
| `CIVITAS_TRANSLATION_CACHE_PATH` | `.civitas/translation-cache.json` | Content-addressed translation cache |
| `CIVITAS_RESEARCH_STALE_AFTER_DAYS` | `180` | Age after which a source is flagged for refresh |

Production startup rejects local authentication. Local register/login routes
return 404 when Cognito mode is selected. Cognito bearer tokens are validated
against the pool issuer and JWKS, including issuer, expiry, token use, and
client ID checks.

## API and MCP

- `GET /api/health` and `GET /api/config` are public and expose only safe phase and limit metadata.
- `/api/auth/register`, `/api/auth/login`, `/api/auth/recover`, `/api/auth/logout`, and `/api/me` manage identity. Local recovery requires `CIVITAS_LOCAL_RECOVERY_CODE`; Cognito deployments use the identity provider's recovery flow.
- `/api/cases` and nested artifacts, events, and usage endpoints require a Bearer token.
- `/api/agent/threads` persists Codex-like conversations. Send messages to
  receive deterministic local research, clarification, or complaint actions.
  Uploads are stored under the local attachment root and are owner/thread
  scoped.
- `/api/tickets` creates stable CivitasX complaint IDs, records status history,
  exposes a redaction-gated `/publish` endpoint for public snapshots, and
  provides a private `/export` evidence brief for offline sharing.
- `/api/feed` supports recent, nearby, following, popular, and recommended
  views plus locality, topic, status, and authority filters. Ranking evaluates
  the full candidate set before returning an opaque `next_cursor`; posts
  support one-account votes, follows, comments, and status projections.
- `/api/notifications` returns local activity for follows, votes, comments, and
  ticket status changes.
- `POST /api/research` runs a citation-first search. Pass `case_id` to save the answer as a private case artifact; `GET /api/cases/{id}/research` restores it after a reload.
- `GET /api/authorities` lists the source-verified GBA, BDA, and BMRCL directory. `GET /api/sources/{source_id}` returns page-level passages and retained Kannada originals.
- `GET /api/connectors` and `GET /api/connectors/{authority_id}` expose the
  local fixture requirements and contact route for each authority. They report
  `submission_enabled: false` until an official API or reviewed browser
  connector is added.
- `POST /api/cases/{id}` is intentionally not used; updates use `PATCH` with an optimistic `version`.
- `/mcp` is stateless Streamable HTTP. Protected tools include `list_my_cases`, `create_case`, `get_my_case`, `search_civic_records`, `research_my_case`, `get_authority_directory`, and `get_civic_source`; `get_civitas_status` exposes safe service metadata.
- Tool calls derive ownership from the authenticated bearer token. No tool accepts an owner ID.

The generated OpenAPI document is available at `/openapi.json` after startup.
The contract summary is in [`contracts/README.md`](contracts/README.md).

## Corpus ingestion

The checked-in `data/corpus/manifest.json` contains 30 deterministic
official-reference snapshots for the prototype. Ingest a downloaded source
only after confirming its authority, URL, publication date, and status:

```bash
uv run python ../../scripts/ingest_corpus.py ./downloaded-record.pdf \
  --source-id bda-example-2025 \
  --title "Example BDA record" \
  --authority "Bangalore Development Authority" \
  --authority-id bda \
  --url https://bdabangalore.org/records/example \
  --status adopted \
  --published-at 2025-05-01T00:00:00+00:00
```

The ingestor stores the original bytes, page-level extraction, a raw-file hash,
and a page-content hash. PDF extraction uses `pypdf`; an OCR callable and a
translation provider can be supplied by a refresh worker when a scan or
Kannada-only page needs them. Missing OCR or translation is retained as an
explicit partial state.

For the optional AgentCore Browser smoke test, install its extra and the
Playwright browser once:

```bash
uv sync --extra aws-smoke
uv run playwright install chromium
```

## Tests and checks

```bash
uv run pytest -q
uv run ruff check src tests
```

The tests cover fresh-login persistence, cross-user denial, optimistic
concurrency, transactional usage reservation/settlement, corpus integrity,
citation-first answers, missing evidence, Kannada retention, and research
artifact ownership. AWS and browser smoke checks live under the repository
`scripts/aws/` directory and are read-only or dry-run by default.
