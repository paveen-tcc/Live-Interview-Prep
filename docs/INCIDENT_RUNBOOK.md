# Private-alpha incident runbook

## Severity

- **Critical:** suspected credential/data exposure, cross-user access, deletion failure that retains data beyond policy, or unsupported evidence displayed as fact.
- **High:** widespread inability to start/complete interviews, reports repeatedly fail, or retention job fails twice.
- **Normal:** isolated recoverable provider/browser failure with no data loss.

## First response

1. Stop new pilot invitations. For a critical incident, disable the affected route or application environment rather than deleting evidence.
2. Record UTC start time, affected environment, deploy/version, request/error IDs, and aggregate counts. Never paste API keys, résumé text, transcripts, raw auth headers, or client secrets into the incident log.
3. Verify database health, migration head, provider status, recent structured errors, and quota/rate-limit changes.
4. If credentials may be exposed, rotate the Azure key and database credential, revoke old access, and confirm the browser still receives only an ephemeral Realtime secret.
5. If cross-user access is suspected, suspend the alpha until ownership tests and a scoped data review pass.
6. For deletion/retention failures, preserve the terminal receipt and retry the idempotent workflow; document completion time.

## Transcription checks

All checks below are content-free: read counts, error codes, and status codes
only. Never read, quote, or export transcript text or audio while investigating.

1. **Deployment missing.** A burst of `transcription_deployment_missing` error
   codes means the configured final deployment name does not exist on the Azure
   resource, usually after a rename or a region move. Confirm
   `AZURE_OPENAI_FINAL_TRANSCRIPTION_DEPLOYMENT` matches the actual deployment
   name; interviews keep running on live transcripts meanwhile. If the name is
   correct, confirm the request route: Azure AI Foundry resources return
   `DeploymentNotFound` for the unified `/openai/v1/audio/transcriptions`
   surface and serve only the deployment-scoped route. A 100% fallback rate with
   a valid deployment name points here, not at the deployment.
2. **Throttling.** `transcription_unavailable` with upstream 429s indicates
   provider rate limiting. Check the deployment's quota and concurrent-request
   ceiling before assuming an application fault.
3. **Timeout.** `transcription_timeout` counts a request that exceeded
   `AZURE_OPENAI_FINAL_TRANSCRIPTION_TIMEOUT_SECONDS`. A rising count with
   healthy Azure status usually means longer answers or a slower region, not a
   failure to retry — the client already retries on 408, 429, and 5xx.
4. **Fallback count.** Count `live_transcription_fallback` usage events. Each one
   is an interview turn that kept its live transcript because final
   transcription failed. A rising ratio against `final_transcription_completed`
   degrades evidence quality without any user-visible error, so treat a
   sustained rise as High even while interviews still complete.
5. **Double-failure reconnect count.** Count `double_transcription_failure`
   usage events. Each is an answer where both lanes failed and the candidate was
   asked to reconnect. This is the only transcription failure that interrupts an
   interview; more than an isolated occurrence is High.

If both transcription deployments are unreachable, disable voice interviews
rather than letting candidates record answers that cannot be transcribed.

## Recovery and communication

- Restore service only after the affected automated tests and a targeted local smoke test pass.
- Give affected pilot users a plain-language summary of what happened, what data was involved, the containment time, and any action they need to take. Obtain legal/privacy review before external notification where required.
- Complete a blameless review with root cause, detection gap, impact, remediation owner, and deadline.

## Support path

During local development, report an issue in this Codex task with the visible error ID and the action that failed. Before external pilots, configure a monitored private support address and named incident owner; do not launch pilots with those fields unassigned.
