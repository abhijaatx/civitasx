# CivitasX AWS foundation

`template.yaml` describes the future AWS boundary for the local-first CivitasX
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
- An AWS Budget at the planned $80 ceiling, with an optional SNS email notification.

The application still enforces its own admission budget and browser concurrency.
AWS Budgets alerts are an additional signal, not a hard spending cap. The
template's Bedrock permission is a deployment boundary; the default Phase 2
retrieval path remains local and extractive. It does not establish that a model
is enabled or that promotional credits cover it.

## Validate and deploy

From the repository root:

```bash
aws cloudformation validate-template --template-body file://infra/template.yaml
sam validate --template-file infra/template.yaml --lint
sam build --template-file infra/template.yaml
sam deploy --guided --template-file .aws-sam/build/template.yaml
```

`sam build` needs the Python dependencies in `services/api/requirements.txt`.
The local host does not require Docker for unit tests; a CI runner with SAM
and Docker should perform the final packaging build.

Before deployment, change `CognitoDomainPrefix`, set `FrontendOrigin` to the
actual HTTPS origin, and provide `AlertEmail` if the team wants AWS Budget
notifications. Confirm the email subscription and the AWS promotional-credit
eligible-services list in the account first.

After SAM reports the `ApiUrl` output, set that exact URL as the `CIVITAS_API_URL`
environment variable in Amplify. The checked-in `amplify.yml` exports it during
the Vite build; the frontend does not silently target a browser-local API in a
hosted build. SAM and Amplify remain separate deployments so a frontend build
cannot unexpectedly replace the API or its data stores.

Do not make AWS deployment a prerequisite for feature development. First pass
the local adapter contract tests, then deploy one provider at a time and run
the same API, MCP, citation, ticket, feed, and privacy checks against the AWS
adapters.
