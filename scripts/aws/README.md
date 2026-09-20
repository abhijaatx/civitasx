# CivitasX AWS checks

## Deploy the AWS pilot

The repeatable deployment entrypoint builds Linux ARM64 dependencies, deploys
the SAM stack, configures the CloudFront origin and Cognito callback, publishes
the Vite SPA, and runs an API health check:

```bash
scripts/aws/deploy.sh
```

Optional variables include `CIVITAS_ALERT_EMAIL`,
`CIVITAS_COGNITO_DOMAIN_PREFIX`, `CIVITAS_BEDROCK_MODEL`, and
`CIVITAS_FRONTEND_ORIGIN`. Keep `CIVITAS_IPGRS_SUBMISSION_ENABLED=false` for
the Lambda deployment until the browser connector is migrated to AgentCore
Browser.

Run the safe control-plane preflight first:

```bash
python3 scripts/aws/preflight.py --json
```

It reads the active region, checks the current principal without printing
credentials, and probes Bedrock, AgentCore Browser, and S3 Vectors control
planes. A visible model catalog is not invocation permission, and neither is
evidence that the account’s promotional credits cover the service.

The paid checks are opt-in and intentionally tiny:

```bash
CIVITAS_ALLOW_PAID_SMOKE=1 python3 scripts/aws/model_smoke.py --allow-paid
CIVITAS_ALLOW_PAID_SMOKE=1 python3 scripts/aws/browser_smoke.py --allow-paid
# Optional: print a one-minute Live View URL while the session is active.
CIVITAS_ALLOW_PAID_SMOKE=1 python3 scripts/aws/browser_smoke.py --allow-paid --print-live-view
```

Before either command, confirm the AWS credit program’s eligible services and
reserve the estimated cost against the application’s usage guard. The browser
smoke test visits only `example.com`, closes the session in all code paths, and
does not persist a Live View URL. The optional URL expires after one minute.
