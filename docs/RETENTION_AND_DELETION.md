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
| Raw candidate audio | Not retained by this application | Held only in browser and API memory during transcription; never written to disk |
| Content-free usage events | 90 days | `USAGE_EVENT_RETENTION_DAYS` |
| Deletion receipts | PII-free terminal receipt only | SHA-256 namespaced identifiers; no interview content |

Run the retention worker as a scheduled one-shot command:

```bash
python scripts/run_retention.py
```

It deletes expired sessions transactionally, clears expired delivery metrics, removes old usage events, and prints only aggregate counts. Schedule it at least daily in an external scheduler when a shared environment is introduced.

## Raw audio lifecycle

Voice answers are transcribed twice: live by the Realtime session during the
interview, and again after the answer ends by the final transcription
deployment, whose text replaces the live text when it arrives.

- The browser buffers each utterance in a `MediaRecorder` chunk list held in
  JavaScript memory. Nothing is written to `localStorage`, `sessionStorage`,
  IndexedDB, the filesystem, or a download.
- The browser releases its reference to an utterance as soon as that turn
  reaches a terminal state: final transcript stored, live transcript accepted as
  fallback, or the page unloads. At most two utterances are held concurrently;
  the recorder refuses a third rather than growing without bound.
- After a double failure the browser keeps exactly one utterance so Reconnect
  can retry it. That retained object is released on the next successful attempt
  and on page unload.
- The API parses the multipart upload as a bounded in-memory stream. It does not
  use temporary-file spooling, so raw audio never reaches API disk. Audio above
  `AZURE_OPENAI_FINAL_TRANSCRIPTION_MAX_BYTES` is rejected before buffering
  completes.
- The API forwards the bytes to Azure and discards them when the response
  returns or the request fails. Only the returned text, its source, model name,
  and finalization timestamp persist.
- Azure's own retention for the configured deployments is governed by that
  Azure resource, not by this application. Confirm it independently before an
  external pilot.

This application does not claim transcription is infallible, and it does not
claim the API server never sees audio bytes — it does see them, transiently, in
memory.

## User controls

- A session can be permanently deleted from the workspace after an explicit confirmation. Its transcript, evaluation, delivery metrics, résumé-derived profile, upload metadata, job target, and scorecard are deleted when not shared by another session.
- Delivery metrics can be disabled without changing the evidence report, or deleted independently.
- Account deletion requires typing `DELETE MY ACCOUNT`. Repeated deletion calls are terminal and idempotent. A PII-free receipt prevents the same authenticated principal from being silently recreated.
- Deleting data does not call an AI provider and never writes document or transcript contents to operational telemetry.

## Verification

Integration tests cover cascading session deletion, repeated deletion, complete account deletion, account recreation prevention, stale-draft cleanup, delivery-metric expiry, usage-event expiry, and migration rollback.
