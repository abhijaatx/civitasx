# CivitasX AWS foundation

`template.yaml` describes the AWS boundary for the local-first CivitasX
product. It is intentionally separate from the local development loop; the
local app runs with SQLite/filesystem/local model adapters and does not need to
provision these resources.

The future Phase 2 AWS boundary includes:

- Cognito email accounts and a public OAuth client using Authorization Code + PKCE.
- One on-demand DynamoDB table for owner-scoped cases, agent threads, tickets,
  community records, artifacts, events, and usage.
- A private, encrypted S3 bucket for artifacts and short-lived browser material.
- A separate versioned private S3 bucket for source-document originals and refreshed corpus bundles.
- Refreshed live-source documents and connector status are stored as separate objects under
  `runtime/live-sources/` and `runtime/live-status/`; local `/tmp` files are only a fallback.
- A Lambda Web Adapter FastAPI function URL with a least-privilege runtime role.
- A private S3 frontend origin behind an HTTPS CloudFront distribution.
- An optional AWS Budget alert with an SNS email notification.

The application still enforces its own admission budget and browser concurrency.
AWS Budgets alerts are an additional signal, not a hard spending cap. The
template's Bedrock permission is a deployment boundary; the default Phase 2
retrieval path remains local and extractive. It does not establish that a model
is enabled or that promotional credits cover it.

## Validate and deploy

From the repository root:

```bash
sam validate --template-file infra/template.yaml --lint
scripts/aws/deploy.sh
```

The deployment script builds Linux ARM64 dependencies with `uv`, packages them
with SAM, deploys the API/data/auth/frontend stack, publishes `apps/web/dist`
to the private S3 origin, invalidates CloudFront, and runs `/api/health`.
It does not require Docker.

Set `CIVITAS_ALERT_EMAIL` if the team wants AWS Budget notifications. The
production pilot keeps government browser submission disabled and caps Lambda
URL attachment requests at 5 MB; move uploads directly to S3 before raising
that limit.

Do not make AWS deployment a prerequisite for feature development. First pass
the local adapter contract tests, then deploy one provider at a time and run
the same API, MCP, citation, ticket, feed, and privacy checks against the AWS
adapters.
