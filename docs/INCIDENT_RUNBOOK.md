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

## Recovery and communication

- Restore service only after the affected automated tests and a targeted local smoke test pass.
- Give affected pilot users a plain-language summary of what happened, what data was involved, the containment time, and any action they need to take. Obtain legal/privacy review before external notification where required.
- Complete a blameless review with root cause, detection gap, impact, remediation owner, and deadline.

## Support path

During local development, report an issue in this Codex task with the visible error ID and the action that failed. Before external pilots, configure a monitored private support address and named incident owner; do not launch pilots with those fields unassigned.
