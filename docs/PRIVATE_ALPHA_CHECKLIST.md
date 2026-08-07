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
