# CivitasX civic-agent architecture

CivitasX uses a capability-driven workflow rather than a prompt branch for
every resident issue:

```text
message → capability graph → location/evidence resolution → policy decision
                                      ↓
                         answer / clarify / draft / approval
```

## Open-source boundaries

- LangGraph owns the typed state graph when the `architecture` extra is
  installed. The deterministic capability registry remains the offline
  fallback.
- Haystack BM25 is an optional recall signal over the versioned local evidence
  corpus. The extractive evidence contract remains application-owned.
- PostgreSQL/PostGIS is the production boundary for jurisdiction polygons and
  civic assets. Local JSON registries keep development and tests offline.
- OPA/Rego owns policy decisions when `CIVITAS_OPA_URL` is configured. The
  local fallback is deny-by-default and exposes the engine used in every
  decision.
- Temporal is the intended durable-action boundary for OTP waits, retries,
  external submissions, and receipt polling. The local run store still records
  the approved content hash and outcome checkpoint; no submission is considered
  complete without a verified receipt.
- OpenTelemetry wraps workflow planning and civic-tool execution. Set
  `CIVITAS_TELEMETRY_ENABLED=true` and optionally
  `OTEL_EXPORTER_OTLP_ENDPOINT` to export traces.

## Reliability contract

The architecture distinguishes four outcomes:

1. `reliable_now`: deterministic or indexed evidence is sufficient.
2. `needs_clarification`: a required slot, usually an exact location, is missing.
3. `safe_route_or_draft`: a live civic connector is still needed, but the agent
   can safely route or prepare a private draft without claiming completion.
4. `approval_required` or `live_connector_required`: an external action or
   current source must be enabled and verified.

Run the 100-case resident evaluation with:

```bash
cd services/api
uv run --locked --extra architecture python evals/run_resident_arch_eval.py
```

The result is a structural reliability score, not a claim that a government
agency accepted a complaint.
