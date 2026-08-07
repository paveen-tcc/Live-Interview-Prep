# Retention and deletion policy

Status: implemented for local private-alpha testing; policy approval is required before external pilots.

## Defaults

| Data | Default | Control |
| --- | ---: | --- |
| Raw résumé bytes | Not retained | Discarded immediately after validated extraction |
| Normalized résumé text and profile | 30 days with its session | Session/account deletion or retention job |
| Transcript and evidence report | 30 days after completion | `TRANSCRIPT_RETENTION_DAYS` |
| Unfinished draft setup | 30 days after creation | `DRAFT_RETENTION_DAYS` |
| Optional speaking-delivery metrics | 30 days after completion | `DELIVERY_METRICS_RETENTION_DAYS`; independently disable/delete in the report |
| Raw audio | Not retained by this application | Browser connects directly to Azure Realtime |
| Content-free usage events | 90 days | `USAGE_EVENT_RETENTION_DAYS` |
| Deletion receipts | PII-free terminal receipt only | SHA-256 namespaced identifiers; no interview content |

Run the retention worker as a scheduled one-shot command:

```bash
python scripts/run_retention.py
```

It deletes expired sessions transactionally, clears expired delivery metrics, removes old usage events, and prints only aggregate counts. Schedule it at least daily in an external scheduler when a shared environment is introduced.

## User controls

- A session can be permanently deleted from the workspace after an explicit confirmation. Its transcript, evaluation, delivery metrics, résumé-derived profile, upload metadata, job target, and scorecard are deleted when not shared by another session.
- Delivery metrics can be disabled without changing the evidence report, or deleted independently.
- Account deletion requires typing `DELETE MY ACCOUNT`. Repeated deletion calls are terminal and idempotent. A PII-free receipt prevents the same authenticated principal from being silently recreated.
- Deleting data does not call an AI provider and never writes document or transcript contents to operational telemetry.

## Verification

Integration tests cover cascading session deletion, repeated deletion, complete account deletion, account recreation prevention, stale-draft cleanup, delivery-metric expiry, usage-event expiry, and migration rollback.
