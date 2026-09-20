# CivitasX API — local Feed + Agent slice

The local service provides authentication, owner-scoped case storage, grounded
civic research, a verified authority directory, persistent Agent threads,
attachments, complaint tickets, a redacted civic Feed, comments, votes,
follows, and an activity inbox. Government filing is available through an
opt-in supervised Karnataka iPGRS browser connector.

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
| `CIVITAS_MCP_ALLOWED_HOSTS` | `localhost:*,127.0.0.1:*` | Host allowlist for the authenticated Streamable HTTP MCP transport |
| `CIVITAS_MCP_ALLOWED_ORIGINS` | unset | Optional Origin allowlist for browser-based MCP clients |
| `CIVITAS_GLOBAL_BUDGET_USD` | `80` | Admission ceiling for paid operations |
| `CIVITAS_BROWSER_CONCURRENCY` | `2` | Reserved browser slots |
| `CIVITAS_COGNITO_REGION` | unset | Cognito issuer region in production |
| `CIVITAS_COGNITO_USER_POOL_ID` | unset | Cognito user pool in production |
| `CIVITAS_COGNITO_CLIENT_ID` | unset | Cognito app client in production |
| `CIVITAS_ARTIFACTS_BUCKET` | unset | Private S3 bucket reserved for cloud artifact offload |
| `CIVITAS_CORPUS_PATH` | `data/corpus/manifest.json` | Versioned public-source manifest |
| `CIVITAS_POLICE_REGISTRY_PATH` | `data/police/bengaluru_stations.json` | Structured police-station routing records |
| `CIVITAS_CAPABILITY_REGISTRY_PATH` | `data/capabilities/civic_capabilities.json` | Data-driven civic capability definitions |
| `CIVITAS_OPA_URL` | unset | Optional Open Policy Agent decision service |
| `CIVITAS_SPATIAL_DATABASE_URL` | unset | Optional PostgreSQL/PostGIS jurisdiction store |
| `CIVITAS_TEMPORAL_TARGET` | unset | Optional Temporal target for durable external actions |
| `CIVITAS_TELEMETRY_ENABLED` | `false` | Enable OpenTelemetry export/instrumentation |
| `CIVITAS_CORPUS_BUCKET` | unset | Cloud bucket reserved for original source bundles |
| `CIVITAS_TRANSLATION_CACHE_PATH` | `.civitas/translation-cache.json` | Content-addressed translation cache |
| `CIVITAS_RESEARCH_STALE_AFTER_DAYS` | `180` | Age after which a source is flagged for refresh |
| `CIVITAS_AGENT_TOOL_TIMEOUT_SECONDS` | `30` | Maximum runtime for one civic tool call before returning a retryable timeout |
| `CIVITAS_IPGRS_SUBMISSION_ENABLED` | `false` | Enable supervised Greater Bengaluru complaint filing |
| `CIVITAS_IPGRS_BROWSER_URL` | Karnataka iPGRS form | Official portal opened by the connector |
| `CIVITAS_IPGRS_BROWSER_HEADLESS` | `false` | Keep `false` when the resident must complete CAPTCHA in the opened browser |
| `CIVITAS_OFFICIAL_API_AUTHORITY_ID` | `gba` | Authority served by the certified API connector |
| `CIVITAS_OFFICIAL_API_URL` | unset | Authority-issued complaint submission endpoint; keep unset until approved |
| `CIVITAS_OFFICIAL_API_STATUS_URL` | unset | Optional authority-issued receipt/status endpoint |
| `CIVITAS_OFFICIAL_API_TOKEN` | unset | Scoped secret for the certified API; never commit it |
| `CIVITAS_OFFICIAL_API_TIMEOUT_SECONDS` | `30` | Timeout for one official API request |

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
- `/api/profile` stores resident-provided form values only after explicit
  confirmation and a separate remember decision. `/api/runs` exposes durable
  submission or publication runs shared by the web app and MCP clients.
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
  requirements and contact route for each authority. When
  `CIVITAS_IPGRS_SUBMISSION_ENABLED=1`, the `gba` profile reports the reviewed
  Karnataka iPGRS browser connector and its district, taluk, address, PIN, and
  OTP mobile requirements.
- `POST /api/tickets/{id}/prepare` and `/preparation/approve` create the exact
  hash-bound filing. `POST /api/tickets/{id}/submit` opens the supervised iPGRS
  form when enabled. `/api/runs/{id}/resume` can request OTP, accept a
  resident-provided code in memory, and request final submission only after the
  resident attests that classification and CAPTCHA are complete. A ticket is
  marked submitted only when the portal returns a grievance number; otherwise
  its outcome is recorded as unknown. A configured certified official API uses
  the same preparation hash, an idempotency key, and a verified reference
  response.
- `POST /api/cases/{id}` is intentionally not used; updates use `PATCH` with an optimistic `version`.
- `/mcp/` is authenticated, stateless Streamable HTTP. In addition to research,
  case, and feed tools, it exposes shared agent tools (`create_agent_thread`,
  `list_agent_threads`, `get_agent_thread`, `send_agent_message`), resident
  profile tools, hash-bound preparation and approval tools, durable run status,
  synthetic submission for local tests, the supervised iPGRS filing run, and
  redaction-gated public-post tools. The browser connector never bypasses OTP
  or CAPTCHA and never stores the OTP in a receipt.
- Tool calls derive ownership from the authenticated bearer token. No tool accepts an owner ID.

### Connecting an external MCP client

Use the deployed API URL with the `/mcp/` path and send a bearer token in the
`Authorization` header. In local development, the token comes from
`POST /api/auth/login`; in production, use the Cognito access token. Do not
place tokens in URLs or commit them to client configuration files.

```text
MCP server URL: https://api.example.com/mcp/
Authorization: Bearer <token>
```

Codex and Claude can discover the server's tools through Streamable HTTP.
Configure `CIVITAS_MCP_ALLOWED_HOSTS` with the API hostname before deployment;
keep DNS-rebinding protection enabled.

For Codex, configure a bearer token through an environment variable (do not
commit the token):

```toml
[mcp_servers.civitasx]
url = "https://api.example.com/mcp/"
bearer_token_env_var = "CIVITAS_ACCESS_TOKEN"
default_tools_approval_mode = "prompt"
tool_timeout_sec = 120
```

For Claude Code, the equivalent project `.mcp.json` shape is:

```json
{
  "mcpServers": {
    "civitasx": {
      "type": "http",
      "url": "https://api.example.com/mcp/",
      "headers": {"Authorization": "Bearer ${CIVITAS_ACCESS_TOKEN}"}
    }
  }
}
```

Copy-ready examples are in [`examples/civitasx.codex.config.toml.example`](../../examples/civitasx.codex.config.toml.example)
and [`examples/civitasx.mcp.json.example`](../../examples/civitasx.mcp.json.example).

The local development command is `codex mcp add civitasx --url
http://localhost:8000/mcp/ --bearer-token-env-var CIVITAS_ACCESS_TOKEN`.
Claude Code can use `claude mcp add --transport http civitasx
http://localhost:8000/mcp/ --header "Authorization: Bearer $CIVITAS_ACCESS_TOKEN"`.
Use a short-lived token and keep write tools in prompt/approval mode.

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
