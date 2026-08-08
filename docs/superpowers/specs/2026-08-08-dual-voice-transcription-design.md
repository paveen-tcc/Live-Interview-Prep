# Dual Voice Transcription Design

**Status:** Approved architecture; pending written-spec review  
**Date:** 2026-08-08  
**Scope:** Voice candidate-turn capture, transcription, persistence, recovery, and evaluation readiness

## Problem

Voice interviews currently persist a candidate turn only when Azure Realtime emits `conversation.item.input_audio_transcription.completed`. The configured `gpt-4o-mini-transcribe` value referred to a model that was not deployed in the Azure resource, so Azure emitted no candidate transcripts. Assistant turns continued to persist, and evaluation later rejected the assistant-only transcript with `transcript_not_ready`.

The fix must make candidate-turn capture reliable without putting transcription latency in the interviewer response path. Candidate audio must not be retained permanently. A transient provider or network failure must not silently lose an answer or create duplicate turns.

## Goals

- Keep the existing `gpt-realtime-2.1` speech-to-speech interviewer responsive.
- Use the deployed `gpt-realtime-whisper` model for low-latency live candidate transcripts.
- Use the deployed `gpt-4o-transcribe` model for a more accurate final transcript of each candidate utterance.
- Persist exactly one ordered candidate turn per Realtime item ID.
- Allow the final model to upgrade the live transcript without creating a duplicate turn.
- Continue the interview when either transcription path succeeds.
- Pause and offer Reconnect only when both paths fail for the same candidate utterance.
- Keep raw candidate audio only in volatile memory and discard it after finalization or terminal failure.
- Prevent evaluation from starting while candidate-turn finalization work is still active in the browser.

## Non-goals

- Replacing the `gpt-realtime-2.1` interviewer.
- Persisting or replaying raw audio.
- Speaker diarization; the microphone stream is already known to be the candidate.
- Building a general multi-provider transcription router in this change.
- Benchmarking preview models such as MAI-Transcribe-1 or Azure Speech Post-stream Refinement in the production interview path.

## Architecture

The voice path has two independent transcription lanes fed from the same browser microphone stream:

1. **Conversation and live transcript lane:** WebRTC sends microphone audio directly to the existing Azure Realtime session. Its input transcription deployment is `gpt-realtime-whisper`, configured with a language hint and a low delay. The Realtime model continues generating interviewer audio without waiting for the second lane.
2. **Final transcript lane:** The browser records only the current candidate utterance in memory. On the matching `input_audio_buffer.speech_stopped` event, it uploads that short audio segment to an authenticated API endpoint. The API forwards the in-memory file to the `gpt-4o-transcribe` deployment and atomically inserts or upgrades the candidate turn.

The live transcript is a complete fallback, not merely decorative text. It is persisted as soon as Azure Realtime completes it. A later final-transcription response replaces the text for the same `client_turn_id` while the interview is active. The Realtime item ID is the idempotency key for both lanes.

## Configuration

The same Azure Foundry resource and server credential are used for all deployed models:

```env
AZURE_OPENAI_ENDPOINT=https://your-resource.services.ai.azure.com
AZURE_OPENAI_API_KEY=<rotated-key>
AZURE_OPENAI_API_VERSION=2025-04-01-preview
AZURE_OPENAI_REALTIME_DEPLOYMENT=gpt-realtime-2.1
AZURE_OPENAI_TEXT_DEPLOYMENT=gpt-5.6-luna
AZURE_OPENAI_REALTIME_VOICE=alloy
AZURE_OPENAI_REALTIME_TRANSCRIPTION_MODEL=gpt-realtime-whisper
AZURE_OPENAI_FINAL_TRANSCRIPTION_DEPLOYMENT=gpt-4o-transcribe
AZURE_OPENAI_TRANSCRIPTION_LANGUAGE=en
AZURE_OPENAI_TRANSCRIPTION_DELAY=low
```

`AZURE_OPENAI_REALTIME_TRANSCRIPTION_MODEL` and `AZURE_OPENAI_FINAL_TRANSCRIPTION_DEPLOYMENT` are Azure deployment names. Startup capability checks expose only whether live and final transcription are configured; they never expose deployment names, endpoints, or credentials to the browser.

The final-transcription HTTP client has a bounded request timeout and two automatic retry attempts for transient timeouts, connection errors, HTTP 408, HTTP 429, and HTTP 5xx responses. Authentication, validation, and missing-deployment failures are not retried indefinitely.

## Browser Audio Capture

Voice preflight continues to request one microphone `MediaStream`. The same stream is attached to WebRTC and to a focused in-memory utterance recorder. No second microphone permission prompt is allowed.

The recorder uses `MediaRecorder` with the first supported provider-compatible MIME type in this order:

1. `audio/webm;codecs=opus`
2. `audio/mp4`
3. `audio/ogg;codecs=opus`

Recording begins with the voice connection and emits short chunks. The recorder retains a small rolling pre-buffer so an Azure `speech_started` event cannot cut off the beginning of a word. When `speech_started` arrives, the client associates buffered and subsequent chunks with that Realtime `item_id`. When `speech_stopped` arrives, it closes the segment after a short tail, builds one in-memory `Blob`, and starts final transcription.

Only one candidate utterance is finalized at a time, but the next utterance may be captured while the previous HTTP request is completing. The queue is bounded. Exceeding the bound pauses the microphone and surfaces Reconnect instead of discarding audio silently.

If the browser has no supported recording MIME type, voice preflight fails with a clear browser-compatibility message before the interview starts. Text-input mode remains available and does not initialize the recorder.

## Turn State and Persistence

`InterviewTurn` gains nullable transcription metadata:

- `transcription_source`: `typed`, `realtime_live`, `final_model`, `assistant`, or `legacy`.
- `transcription_model`: the server-known deployment name for provider-generated candidate transcripts; never supplied by the browser.
- `transcription_finalized_at`: set when a candidate turn is upgraded by the final model or explicitly accepted as the live fallback.

Existing rows migrate to `legacy`. Text-input and assistant turns keep their current acknowledgement behavior.

The existing batch-turn endpoint continues to accept live Realtime candidate transcripts. It may insert the turn once and may acknowledge an identical retry, but it may not replace transcript text.

A new multipart endpoint performs final transcription:

```text
POST /api/interviews/{interview_id}/turns/{client_turn_id}:transcribe
```

Request fields are the audio file plus optional observed speech timestamps. The server:

1. Verifies authentication, interview ownership, voice input mode, active status, MIME allowlist, nonempty content, and maximum byte size.
2. Returns the already-finalized turn without calling Azure when the same `client_turn_id` was previously finalized.
3. Calls the configured Azure `gpt-4o-transcribe` deployment using the language hint and a short, server-built vocabulary prompt derived from the frozen role title and résumé-backed technical terms.
4. Inserts the candidate turn if the live lane has not arrived, or replaces the matching live transcript if it has.
5. Marks the turn acknowledged with `transcription_source=final_model` and commits once.
6. Returns the updated interview runtime.

The endpoint never permits final transcription to modify assistant, typed, post-interview, or differently owned turns. Provider response bodies and transcript/audio content are excluded from operational logs.

## Client Turn Coordination

The client keeps a small per-item state object containing the speech timing, live transcript status, in-memory audio blob, final request status, and whether a persisted turn is final.

Event ordering is intentionally unconstrained:

- If the live transcript arrives first, it is saved immediately and later upgraded in place.
- If the final transcript arrives first, the server inserts the final turn; a later live save becomes an idempotent no-op and cannot downgrade it.
- Repeated Realtime events, HTTP retries, reconnects, and Strict Mode effects reuse the same Realtime item ID and cannot create another turn.
- Once a turn is finalized, its audio blob and recorder chunks are released immediately.

The candidate’s audio is not needed by the interviewer response path. Final transcription therefore runs asynchronously and never delays `response.create` or remote audio playback.

## Failure and Recovery

Failure is evaluated per candidate utterance:

- **Live succeeds, final succeeds:** persist the final-model text.
- **Live succeeds, final fails after retries:** explicitly finalize the live text as the fallback, release audio, show a nonblocking status, and continue.
- **Live fails, final succeeds:** persist the final-model text, release audio, and continue.
- **Both fail:** disable the microphone track, close the Realtime transport, retain the unsent audio blob only in browser memory, move the UI to reconnecting, and show why transcription failed plus a Reconnect action.

Reconnect obtains a fresh short-lived Realtime secret, reconnects the same interview, and retries the retained final-transcription request before accepting new microphone input. A successful retry releases the blob and resumes. If the browser reloads, volatile audio is lost; any already-persisted live transcript remains available as the recoverable fallback.

The UI must not claim that transcription cannot fail. Failures can result from permission revocation, browser media failures, connection loss, provider throttling/outage, an invalid or unavailable deployment, unsupported audio, or an empty/no-speech segment. The product guarantee is safe detection and recovery without silent data loss, not impossibility of failure.

## Interview Completion and Evaluation

When the candidate or timer ends the interview, the client stops accepting new audio and waits for its bounded final-transcription queue. The completion screen displays “Finalizing your transcript” during this wait. Each turn resolves to either a final-model transcript or an explicitly accepted live fallback before the client calls the existing completion endpoint.

The server retains its current evaluation safety boundary: an evaluation requires at least one acknowledged candidate turn and rejects pending candidate turns. Completion and evaluation remain idempotent. A report is never generated from an assistant-only transcript.

Server-authoritative timer expiry can still end an interview while the browser is finalizing. After `ended_at`, the API may idempotently finalize only an existing candidate turn that was persisted before the timer boundary; it never inserts a new turn. Evaluation can then be retried against the same frozen transcript and scorecard. When the live lane also failed and no candidate row exists, the session remains recoverable rather than trusting a new post-boundary upload.

## Privacy and Security

- Raw audio is held only in browser memory and request memory; it is never written to SQLite, files, object storage, analytics, or application logs.
- Audio blobs are released immediately after success, accepted fallback, cancellation, or terminal failure acknowledgement.
- The browser receives only a short-lived Realtime credential. The permanent Azure key and final-transcription deployment remain server-side.
- Upload validation is fail-closed: authenticated ownership, interview state, content type, byte limit, and nonempty audio are required.
- Resume and target-role vocabulary is server-derived from the frozen setup and treated as data, not instructions.
- The privacy page and retention documentation are updated to explain transient in-memory final-transcription processing accurately.

## Observability

Content-free usage events and structured logs record:

- live transcription completed or failed;
- final transcription completed, retried, fell back, or failed;
- final-transcription latency bucket;
- reconnect caused by a double transcription failure.

Operational records contain interview IDs, request/error IDs, status categories, counts, and latency, but never audio or transcript text. The UI shows a safe error ID for support without returning Azure response bodies.

## Testing

Backend unit and integration tests cover:

- Realtime session configuration uses the deployed live model, language, and delay.
- Final-transcription configuration and capability reporting.
- Azure multipart request construction without exposing the permanent key.
- Timeout/retry classification and sanitized provider failures.
- Authentication, ownership, input mode, MIME, size, empty-file, and interview-state validation.
- Insert when final arrives first, upgrade when live arrives first, and idempotent repeat calls.
- A final result cannot overwrite assistant, typed, differently owned, or post-boundary content.
- Evaluation refuses assistant-only or pending-candidate transcripts and succeeds with final or accepted-fallback candidate turns.
- Migration upgrade and downgrade behavior.

Frontend tests cover:

- One microphone permission request and one stream shared by WebRTC and the recorder.
- Supported MIME selection and preflight rejection when no format is available.
- Both event orderings converge on one candidate turn.
- Final failure uses the live fallback without pausing.
- Live failure plus final success continues normally.
- Double failure pauses, disables microphone input, and offers Reconnect.
- Reconnect retries retained audio before resuming.
- Completion waits for the finalization queue and does not hang after fallback.
- Audio blobs and chunks are released after every terminal path.
- Text-input mode remains unchanged.

Manual verification uses a short prerecorded or spoken technical answer and confirms:

1. Candidate and assistant turns both appear in order.
2. The candidate turn is upgraded to `final_model` without duplication.
3. Ending the interview produces an evidence report.
4. Disabling the final deployment exercises the live fallback without interrupting the interview.
5. Disabling both deployments pauses the interview and Reconnect resumes the same session.
6. No raw audio files or transcript content appear in application storage or logs.

## Exit Criteria

- A normal voice interview persists every candidate answer once and produces an evidence report.
- The interviewer response path does not wait for `gpt-4o-transcribe`.
- Either transcription model can fail independently without losing the candidate turn.
- A double failure visibly pauses the interview and can recover through Reconnect.
- Evaluation cannot run against an assistant-only or unfinalized candidate transcript.
- Automated backend and frontend suites pass, including new ordering, retry, fallback, privacy, and idempotency coverage.
- Retention/privacy documentation matches the implemented in-memory audio lifecycle.
