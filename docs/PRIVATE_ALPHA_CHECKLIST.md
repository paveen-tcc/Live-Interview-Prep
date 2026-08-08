# Private alpha release checklist

## Implemented locally

- Ownership-scoped PostgreSQL persistence and hidden SQL parameters
- Server-only permanent Azure credentials and ephemeral browser secrets
- Passwordless-email production posture; explicit local developer identity
- PDF/DOCX allowlist, MIME/signature/content/size/archive limits, parser timeout, and immediate raw-byte disposal
- Evidence citations validated against acknowledged candidate turns; unsupported evidence rejects the report
- Delivery coaching opt-in, separate from role-fit scoring, independently disable/delete
- Persistent daily session/evaluation quotas and Realtime rate limiting
- Content-free usage/cost ledger and user-visible usage page
- Idempotent session/account deletion and configurable retention job
- Error/request IDs, security headers, dependency audits, and non-root container

The upload controls follow OWASP's defense-in-depth guidance: allowlisted CV formats, untrusted MIME handling, signature validation, generated storage keys, authorization, and size/decompression limits. See <https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html>.

Operational events intentionally exclude résumé/transcript contents, credentials, and ephemeral secrets. This follows OWASP's recommendation to support security/operational investigation while excluding sensitive data. See <https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html>.

## Dual-transcription degradation test

Run this before a pilot, using non-sensitive synthetic speech only. Restart the
API after each `.env` change so the capability flags are re-read.

- [ ] **Final deployment disabled.** Clear
      `AZURE_OPENAI_FINAL_TRANSCRIPTION_DEPLOYMENT`. The voice preflight refuses
      to start and names the final transcription deployment.
- [ ] **Live deployment disabled.** Restore final, clear
      `AZURE_OPENAI_REALTIME_TRANSCRIPTION_MODEL`. The preflight refuses to start
      and names the live transcription deployment.
- [ ] **Both disabled.** Clear both. The preflight names both and
      `Start interview` stays disabled.
- [ ] **Final failing mid-interview.** With both configured, start an interview,
      then make final transcription fail (point the deployment name at a
      nonexistent deployment). The interview stays connected, shows the
      nonblocking "Using live transcript" status, and the turn keeps its live
      text with a `live_transcription_fallback` usage event.
- [ ] **Both failing mid-interview.** Also break the live lane. The affected
      answer pauses the microphone, shows the reconnect prompt, records a
      `double_transcription_failure` event, and Reconnect recovers the retained
      answer once a deployment is restored.
- [ ] **Completion is blocked while unresolved.** With an answer in the paused
      state, press Stop. The interview must refuse to complete and stay
      reconnectable rather than producing an assistant-only report.
- [ ] Confirm throughout that no audio file appears on the API host and that
      logs contain request IDs, status codes, and latency but no transcript text
      or audio bytes.

## Gates that cannot be closed by local automation

- [ ] No unresolved critical finding after an independent security/privacy review
- [ ] Current Chrome, Safari, and Edge voice matrix completed
- [ ] p95 end-of-speech to first audio ≤ 2.5 seconds on the agreed network profile
- [ ] Packet-loss/jitter scenario completed without duplicate/lost turns
- [ ] Provider token/audio usage ingested so cost per completed interview is measured, not shown as unavailable
- [ ] Failure rate measured from a real pilot cohort
- [ ] Ten to twenty consented pilots across junior, mid-level, and senior profiles
- [ ] Human reviewers rate reports useful and evidence-correct
- [ ] Monitored support address, incident owner, privacy owner, and subprocessors approved

M6 is not exited until every item above has evidence. M7 remains gated until M3 voice and M6 privacy/fairness controls are stable.
