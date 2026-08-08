# Dual Voice Transcription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reliably persist every voice candidate answer by combining low-latency `gpt-realtime-whisper` transcripts with asynchronous `gpt-4o-transcribe` finalization, without slowing the interviewer or retaining raw audio.

**Architecture:** The existing Azure Realtime WebRTC session remains the conversation path and immediately persists a live candidate transcript. A browser-only recorder segments the same microphone stream using Realtime VAD events and sends each in-memory utterance to a server-side final-transcription endpoint. Both lanes converge on the Realtime item ID; the server upgrades the turn in place, the client accepts the live transcript when only the final lane fails, and only a double failure pauses for Reconnect.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy async, Alembic, httpx, pytest, React 19, TypeScript 5.8, browser MediaRecorder/WebRTC, Vitest, Testing Library

## Global Constraints

- Preserve `gpt-realtime-2.1` as the speech-to-speech interviewer and `gpt-5.6-luna` as the report evaluator.
- Use the actual Azure deployment names `gpt-realtime-whisper` and `gpt-4o-transcribe` from environment configuration; never assume that a catalog model is deployed.
- `AZURE_OPENAI_TRANSCRIPTION_LANGUAGE` defaults to `en`; `AZURE_OPENAI_TRANSCRIPTION_DELAY` defaults to `low` and accepts only `minimal`, `low`, `medium`, `high`, or `xhigh`.
- The final-transcription call never blocks Azure Realtime response generation or audio playback.
- Raw audio exists only in browser/request memory and is never written to SQLite, disk, object storage, analytics, or logs.
- One Realtime item ID maps to one ordered candidate turn. Retries and event reordering cannot duplicate or downgrade it.
- Either transcription lane may succeed independently. Only a failure of both lanes pauses the interview and offers Reconnect.
- Evaluation requires an acknowledged candidate turn and must never run from an assistant-only transcript.
- Text-input mode and delivery-coaching scoring semantics remain unchanged.
- Do not log provider response bodies, API keys, audio bytes, résumé text, or transcript text.

---

## File Structure

- `api/config.py`: validated transcription settings and safe configured-state properties.
- `api/services/realtime.py`: Azure Realtime session payload containing live deployment, language, and delay.
- `api/services/transcription.py`: bounded, retrying Azure final-transcription client and frozen-setup vocabulary prompt.
- `api/models.py`: persisted transcript source/finalization metadata.
- `api/realtime_schemas.py`: public turn metadata returned to the browser.
- `api/routes/realtime.py`: idempotent live turn persistence, final-audio endpoint, live-fallback endpoint, and completion boundary.
- `api/schemas.py`, `api/routes/capabilities.py`: safe dual-transcription capability booleans.
- `migrations/versions/20260808_0008_dual_transcription.py`: forward and rollback schema change.
- `web/src/voiceCapture.ts`: browser-only MIME selection, rolling pre-buffer, utterance segmentation, and memory release.
- `web/src/transcription.ts`: per-item convergence, fallback, retained-audio retry, and queue-idle coordination.
- `web/src/realtime.ts`: transcription failure event contract and microphone-track enable/disable support.
- `web/src/PracticePage.tsx`: recorder/coordinator lifecycle, pause/reconnect UX, and completion wait.
- `web/src/api.ts`, `web/src/types.ts`: multipart endpoints and transcript metadata types.
- `web/src/styles.css`: compact finalizing/fallback/reconnect status styling.
- `web/src/PrivacyPage.tsx`, `docs/RETENTION_AND_DELETION.md`, `docs/BROWSER_COMPATIBILITY.md`, `README.md`, `.env.example`: accurate setup and privacy documentation.

---

### Task 1: Validate dual-transcription configuration

**Files:**
- Modify: `api/config.py`
- Modify: `api/services/realtime.py`
- Modify: `api/schemas.py`
- Modify: `api/routes/capabilities.py`
- Modify: `.env.example`
- Modify: `web/src/types.ts`
- Modify: `web/src/PracticePage.test.tsx`
- Modify: `tests/test_realtime_service.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: existing `Settings`, `create_realtime_client_secret()`, and `GET /api/capabilities`.
- Produces: `Settings.final_transcription_configured`, `Settings.live_transcription_configured`, and safe capability fields with the same names; Realtime input transcription with `{model, language, delay}`.

- [ ] **Step 1: Write failing configuration and Realtime payload tests**

Add tests that construct settings explicitly and assert the deployment names remain opaque strings rather than catalog defaults:

```python
settings = Settings(
    _env_file=None,
    azure_openai_endpoint="https://example.services.ai.azure.com",
    azure_openai_api_key="server-key",
    azure_openai_realtime_deployment="interviewer-deployment",
    azure_openai_realtime_transcription_model="live-stt-deployment",
    azure_openai_final_transcription_deployment="final-stt-deployment",
    azure_openai_transcription_language="en",
    azure_openai_transcription_delay="low",
)
assert settings.live_transcription_configured is True
assert settings.final_transcription_configured is True
```

Update the voice client-secret assertion to require:

```python
assert audio["input"]["transcription"] == {
    "model": "live-stt-deployment",
    "language": "en",
    "delay": "low",
}
```

Add a capability-route test proving that booleans are returned and deployment names, endpoint, and key are absent from the JSON body.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
./venv/bin/python -m pytest tests/test_realtime_service.py tests/test_api.py -q
```

Expected: failures for missing settings/capability fields and missing `language`/`delay` in the Realtime payload.

- [ ] **Step 3: Add the validated settings and safe capabilities**

Add these settings to `Settings`:

```python
azure_openai_final_transcription_deployment: str | None = None
azure_openai_transcription_language: str = "en"
azure_openai_transcription_delay: Literal[
    "minimal", "low", "medium", "high", "xhigh"
] = "low"
azure_openai_final_transcription_timeout_seconds: float = 30.0
azure_openai_final_transcription_max_bytes: int = 25_000_000
```

Define configured-state properties that require endpoint, nonempty server key, and the appropriate deployment name. Extend backend and TypeScript capability contracts with `live_transcription_configured` and `final_transcription_configured` booleans only, and update the existing `PracticePage` test fixture with both values set to `true`. In `create_realtime_client_secret()`, build:

```python
"transcription": {
    "model": settings.azure_openai_realtime_transcription_model,
    "language": settings.azure_openai_transcription_language,
    "delay": settings.azure_openai_transcription_delay,
}
```

Add the four transcription variables and timeout/size defaults to `.env.example`; do not modify or print `.env`.

- [ ] **Step 4: Run focused tests and formatting**

Run:

```bash
./venv/bin/python -m pytest tests/test_realtime_service.py tests/test_api.py -q
./venv/bin/python -m ruff check api/config.py api/services/realtime.py api/schemas.py api/routes/capabilities.py tests/test_realtime_service.py tests/test_api.py
npm --prefix web run typecheck
```

Expected: all commands pass.

- [ ] **Step 5: Commit configuration support**

```bash
git add .env.example api/config.py api/services/realtime.py api/schemas.py api/routes/capabilities.py web/src/types.ts web/src/PracticePage.test.tsx tests/test_realtime_service.py tests/test_api.py
git commit -m "feat(transcription): configure dual Azure models"
```

---

### Task 2: Persist transcript provenance and finalization state

**Files:**
- Create: `migrations/versions/20260808_0008_dual_transcription.py`
- Modify: `api/models.py`
- Modify: `api/realtime_schemas.py`
- Modify: `web/src/types.ts`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `InterviewTurn` and migration revision `20260807_0007`.
- Produces: `transcription_source`, `transcription_model`, and `transcription_finalized_at` on database, API, and frontend turn types.

- [ ] **Step 1: Extend migration and runtime response tests first**

Update the migration test to inspect `PRAGMA table_info(interview_turns)` after upgrading and require:

```python
assert {
    "transcription_source",
    "transcription_model",
    "transcription_finalized_at",
} <= interview_turn_columns
```

Add an API runtime assertion that a legacy-created turn serializes with `transcription_source="legacy"`, a null model, and a null finalization timestamp.

- [ ] **Step 2: Run migration/API tests and verify failure**

Run:

```bash
./venv/bin/python -m pytest tests/test_api.py::test_migrations_upgrade_and_roll_back -q
./venv/bin/python -m pytest tests/test_api.py -q
```

Expected: missing-column and missing-response-field failures.

- [ ] **Step 3: Add the backward-compatible migration and model fields**

Create revision `20260808_0008` with `down_revision="20260807_0007"`. Use `batch_alter_table("interview_turns")` to add:

```python
sa.Column(
    "transcription_source",
    sa.String(length=24),
    nullable=False,
    server_default="legacy",
)
sa.Column("transcription_model", sa.String(length=160), nullable=True)
sa.Column("transcription_finalized_at", sa.DateTime(timezone=True), nullable=True)
```

The downgrade drops the three columns in reverse order. Mirror them in `InterviewTurn`, `InterviewTurnResponse`, and the TypeScript `InterviewTurn` interface:

```typescript
transcription_source:
  | "typed"
  | "realtime_live"
  | "final_model"
  | "assistant"
  | "legacy";
transcription_model: string | null;
transcription_finalized_at: string | null;
```

- [ ] **Step 4: Run migration, API, and web type tests**

Run:

```bash
./venv/bin/python -m pytest tests/test_api.py -q
npm --prefix web run typecheck
```

Expected: both commands pass.

- [ ] **Step 5: Commit transcript metadata**

```bash
git add migrations/versions/20260808_0008_dual_transcription.py api/models.py api/realtime_schemas.py web/src/types.ts tests/test_api.py
git commit -m "feat(transcription): track transcript provenance"
```

---

### Task 3: Build the bounded Azure final-transcription service

**Files:**
- Create: `api/services/transcription.py`
- Create: `tests/test_transcription_service.py`

**Interfaces:**
- Consumes: `Settings`, `azure_resource_root()`, audio bytes/MIME/filename, and a frozen interview setup snapshot.
- Produces: `FinalTranscription(text: str, deployment: str, elapsed_ms: int, attempts: int)`, `TranscriptionServiceError(code, status_code, attempts)`, `build_transcription_prompt(snapshot)`, and `transcribe_candidate_audio()`.

- [ ] **Step 1: Write service contract, prompt, retry, and sanitization tests**

Cover all of these explicit cases with `httpx.MockTransport`:

```python
result = await transcribe_candidate_audio(
    settings=settings,
    audio=b"candidate-audio",
    media_type="audio/webm;codecs=opus",
    filename="answer.webm",
    prompt="Role: Backend Engineer. Terms: FastAPI, PostgreSQL.",
    client=client,
    sleep=no_sleep,
)
assert result.text == "I built a FastAPI service."
assert observed_form_fields["model"] == "final-stt-deployment"
assert observed_form_fields["language"] == "en"
assert observed_headers["api-key"] == "server-key"
```

Also assert that one 429 followed by 200 makes two calls; 400 makes one call; timeout followed by 200 succeeds; empty provider text fails; and neither a provider body marker nor the server key appears in `str(error)` or captured logs. Prompt tests must cap output length and include only the frozen target title and résumé-backed claim terms.

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```bash
./venv/bin/python -m pytest tests/test_transcription_service.py -q
```

Expected: import failure because `api.services.transcription` does not exist.

- [ ] **Step 3: Implement the focused service**

Define:

```python
@dataclass(frozen=True)
class FinalTranscription:
    text: str
    deployment: str
    elapsed_ms: int
    attempts: int


class TranscriptionServiceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int = 502,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.attempts = attempts


async def transcribe_candidate_audio(
    *,
    settings: Settings,
    audio: bytes,
    media_type: str,
    filename: str,
    prompt: str,
    client: httpx.AsyncClient | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> FinalTranscription:
    """Return sanitized Azure transcript output or raise a categorized error."""
```

POST multipart data to `{azure_resource_root(endpoint)}/openai/v1/audio/transcriptions` with `model`, `language`, `prompt`, and `file`. Retry at most two additional attempts with short exponential backoff only for timeouts, connection errors, HTTP 408, 429, and 5xx. Map 401/403 to `transcription_auth`, 404 to `transcription_deployment_missing`, 413 to `transcription_too_large`, and all other safe categories without provider details.

The implementation under the documented function signature performs the bounded multipart request and returns only after parsing nonempty text. `build_transcription_prompt(snapshot)` treats snapshot content as data, selects the role title plus deduplicated short technical/name tokens from source-backed claims, removes control characters, and caps the prompt at 2,000 characters.

- [ ] **Step 4: Run tests, lint, and format checks**

Run:

```bash
./venv/bin/python -m pytest tests/test_transcription_service.py -q
./venv/bin/python -m ruff format --check api/services/transcription.py tests/test_transcription_service.py
./venv/bin/python -m ruff check api/services/transcription.py tests/test_transcription_service.py
```

Expected: all commands pass.

- [ ] **Step 5: Commit the provider service**

```bash
git add api/services/transcription.py tests/test_transcription_service.py
git commit -m "feat(transcription): add final audio transcription service"
```

---

### Task 4: Add idempotent finalization and live-fallback API behavior

**Files:**
- Modify: `api/routes/realtime.py`
- Modify: `api/realtime_schemas.py`
- Modify: `api/services/evaluation_jobs.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `transcribe_candidate_audio()`, `build_transcription_prompt()`, `InterviewTurn` metadata, existing ownership/state helpers, and multipart `UploadFile`.
- Produces: `POST /api/interviews/{id}/turns/{client_turn_id}:transcribe`, `POST /api/interviews/{id}/turns/{client_turn_id}:accept-live`, final-first/live-first convergence, and pending-finalization evaluation rejection.

- [ ] **Step 1: Write failing endpoint and convergence tests**

Add API tests for:

1. Authenticated owner can upload `audio/webm` to an active voice interview and receives one `final_model` candidate turn.
2. A previously saved `realtime_live` turn is updated in place with the same database ID and sequence.
3. Repeating the final endpoint returns the finalized turn without calling the mocked provider again.
4. A late live transcript cannot replace a `final_model` turn and returns 200 rather than 409.
5. `:accept-live` marks an existing live user turn finalized and cannot operate on assistant, typed, missing, or foreign turns.
6. Text-input interviews, MIME outside `audio/webm`, `audio/mp4`, and `audio/ogg`, empty files, and payloads over the configured byte limit return 409/415/422/413 before provider invocation.
7. New post-`ended_at` turns are rejected, while an existing pre-boundary live candidate turn can be finalized idempotently.
8. Provider errors return a safe error envelope and ID with no provider body.
9. Evaluation rejects a candidate turn whose `transcription_finalized_at` is null, not merely one whose delivery status is pending.
10. Content-free transcription telemetry accepts only a fixed event enum and never accepts audio or transcript fields.

Patch the service at its route import boundary so tests never contact Azure.

- [ ] **Step 2: Run the focused API tests and verify failure**

Run:

```bash
./venv/bin/python -m pytest tests/test_api.py -q
```

Expected: endpoint 404s and missing provenance/finalization behavior.

- [ ] **Step 3: Implement source-aware batch persistence**

When inserting through `turns:batch`, set source server-side:

```python
source = (
    "assistant"
    if item.speaker == "assistant"
    else "typed"
    if interview.input_mode == "text_dev"
    else "realtime_live"
)
```

Typed and assistant turns set `transcription_finalized_at` immediately. Voice live turns remain usable fallback candidates but have a null finalization time until final model success or explicit acceptance. If an existing candidate turn is already `final_model`, a differing late live transcript is a no-op; other turn-ID/content conflicts remain 409.

Set `transcription_model=settings.azure_openai_realtime_transcription_model` only for `realtime_live` candidate turns. Typed and assistant turns use null model metadata; the final endpoint supplies the final deployment name server-side.

- [ ] **Step 4: Implement multipart finalization and live acceptance**

Add route handlers with the exact paths in the interface block. Normalize the content type before comparing it, read no more than `max_bytes + 1`, and close the upload in `finally`. The final route locks the interview and existing turn, returns an already finalized result before calling the provider, calls the service outside content logging, then locks again and atomically inserts/upgrades the turn.

On success set:

```python
turn.transcript = result.text
turn.delivery_status = "acknowledged"
turn.transcription_source = "final_model"
turn.transcription_model = result.deployment
turn.transcription_finalized_at = datetime.now(UTC)
```

The live-accept route requires an existing user turn with `transcription_source="realtime_live"`, then sets its finalization timestamp without changing text. Add content-free usage events `final_transcription_completed` and `live_transcription_fallback`. Add `POST /api/interviews/{interview_id}/transcription-events` with a schema enum limited to `live_transcription_completed`, `live_transcription_failed`, and `double_transcription_failure`; it writes only the allowlisted event kind and quantity `1`.

Log final completion/failure with request ID, interview ID, safe error code, attempt count, and a coarse latency bucket (`under_1s`, `1s_to_3s`, `3s_to_10s`, or `over_10s`). Never attach transcript text, multipart fields, audio bytes, or provider bodies.

Update `_finalized_input()` so voice candidate turns require non-null `transcription_finalized_at`; legacy and text-mode sessions retain backward compatibility.

- [ ] **Step 5: Run focused backend verification**

Run:

```bash
./venv/bin/python -m pytest tests/test_api.py tests/test_evaluation_service.py tests/test_realtime_service.py tests/test_transcription_service.py -q
./venv/bin/python -m ruff format --check api tests
./venv/bin/python -m ruff check api tests
```

Expected: all commands pass.

- [ ] **Step 6: Commit the API boundary**

```bash
git add api/routes/realtime.py api/realtime_schemas.py api/services/evaluation_jobs.py tests/test_api.py
git commit -m "feat(transcription): finalize candidate turns idempotently"
```

---

### Task 5: Capture candidate utterances in volatile browser memory

**Files:**
- Create: `web/src/voiceCapture.ts`
- Create: `web/src/voiceCapture.test.ts`
- Modify: `docs/BROWSER_COMPATIBILITY.md`

**Interfaces:**
- Consumes: one existing microphone `MediaStream` and Azure VAD item IDs.
- Produces: `selectRecorderMimeType()`, `RecordedUtterance`, and `BufferedUtteranceRecorder` with `start()`, `speechStarted(itemId)`, `speechStopped(itemId)`, and `stop()`.

- [ ] **Step 1: Write fake-MediaRecorder tests first**

Use a deterministic fake MediaRecorder and fake timers to verify:

```typescript
const recorder = new BufferedUtteranceRecorder(stream, {
  onUtterance: vi.fn(),
  onError: vi.fn(),
});
recorder.start();
recorder.speechStarted("voice-item-1");
fakeRecorder.emit(new Blob(["answer"], { type: "audio/webm" }));
recorder.speechStopped("voice-item-1");
vi.advanceTimersByTime(300);
expect(onUtterance).toHaveBeenCalledWith(
  expect.objectContaining({ itemId: "voice-item-1" }),
);
```

Also test MIME priority, an explicit unsupported-browser result when no allowlisted MIME is available, one MediaRecorder for the existing stream, pre-buffer inclusion, mismatched stop IDs ignored, maximum queue error, and `stop()` clearing recorder/chunk/item references.

- [ ] **Step 2: Run the recorder tests and verify failure**

Run:

```bash
npm --prefix web test -- voiceCapture.test.ts
```

Expected: import failure because `voiceCapture.ts` does not exist.

- [ ] **Step 3: Implement the recorder without external dependencies**

Define:

```typescript
export interface RecordedUtterance {
  itemId: string;
  blob: Blob;
  mediaType: string;
  startedAt?: string;
  endedAt?: string;
}

export class BufferedUtteranceRecorder {
  constructor(
    stream: MediaStream,
    callbacks: {
      onUtterance: (utterance: RecordedUtterance) => void;
      onError: (message: string) => void;
    },
    options?: { prebufferChunks?: number; tailMs?: number; maxQueued?: number },
  );
  start(): void;
  speechStarted(itemId: string): void;
  speechStopped(itemId: string): void;
  finish(): Promise<void>;
  stop(): void;
}
```

Use `MediaRecorder.start(250)` and retain at most six idle chunks. Once speech starts, move the pre-buffer into the active segment and append subsequent chunks. After a 300 ms tail, create one Blob with the selected MIME and delete all per-item chunk references before invoking the callback. `finish()` closes an active segment, waits for its tail/data event, emits it once, and then stops recording; `stop()` is immediate cancellation that clears timers, stops an active recorder, and empties all arrays/maps.

- [ ] **Step 4: Run recorder tests, typecheck, lint, and browser documentation checks**

Document the MediaRecorder requirement and supported Chrome/Edge/Safari MIME behavior. Run:

```bash
npm --prefix web test -- voiceCapture.test.ts
npm --prefix web run typecheck
npm --prefix web run lint
```

Expected: all commands pass.

- [ ] **Step 5: Commit volatile capture**

```bash
git add web/src/voiceCapture.ts web/src/voiceCapture.test.ts docs/BROWSER_COMPATIBILITY.md
git commit -m "feat(transcription): capture voice turns in memory"
```

---

### Task 6: Coordinate both transcript lanes and recovery

**Files:**
- Create: `web/src/transcription.ts`
- Create: `web/src/transcription.test.ts`
- Modify: `web/src/api.ts`
- Modify: `web/src/realtime.ts`
- Modify: `web/src/realtime.test.ts`

**Interfaces:**
- Consumes: live completed/failed events, `RecordedUtterance`, `api.saveTurns`, `api.transcribeTurn`, `api.acceptLiveTranscript`, and the content-free telemetry endpoint.
- Produces: `TurnTranscriptionCoordinator` with `liveCompleted()`, `liveFailed()`, `audioReady()`, `awaitIdle()`, `retryRetained()`, and `dispose()`; fatal/recovered callbacks for `PracticePage`.

- [ ] **Step 1: Add API client methods and failing coordinator tests**

Define frontend calls:

```typescript
transcribeTurn: (
  interviewId: string,
  clientTurnId: string,
  utterance: RecordedUtterance,
) => Promise<InterviewRuntime>;

acceptLiveTranscript: (
  interviewId: string,
  clientTurnId: string,
) => Promise<InterviewRuntime>;

recordTranscriptionEvent: (
  interviewId: string,
  kind:
    | "live_transcription_completed"
    | "live_transcription_failed"
    | "double_transcription_failure",
) => Promise<void>;
```

`transcribeTurn` sends FormData fields `file`, `started_at`, and `ended_at` when present. Add coordinator tests for live-first, final-first, duplicate events, final failure/live success, live persistence network failure/final success, live failure/final success, double failure after a 3,000 ms live grace period, retained-audio retry, bounded queue, idle waiting, fatal idle rejection, and disposal clearing all Blob references.

- [ ] **Step 2: Run frontend unit tests and verify failure**

Run:

```bash
npm --prefix web test -- transcription.test.ts realtime.test.ts
```

Expected: missing coordinator/API/event behavior failures.

- [ ] **Step 3: Extend the Realtime event and transport contracts**

Add optional transcription failure fields to `RealtimeEvent` and recognize `conversation.item.input_audio_transcription.failed`. Add:

```typescript
setMicrophoneEnabled(enabled: boolean): void {
  for (const sender of this.peer?.getSenders() ?? []) {
    if (sender.track?.kind === "audio") sender.track.enabled = enabled;
  }
}
```

Test that disabling affects only audio sender tracks and does not stop them.

- [ ] **Step 4: Implement per-item convergence and memory release**

Define:

```typescript
export class TurnTranscriptionCoordinator {
  liveCompleted(itemId: string, transcript: string): void;
  liveFailed(itemId: string): void;
  audioReady(utterance: RecordedUtterance): void;
  awaitIdle(): Promise<void>;
  retryRetained(): Promise<void>;
  dispose(): void;
}
```

Inject API operations and callbacks through the constructor rather than importing React state. Persist live text immediately and report only the allowlisted content-free event kind; a failed live persistence request counts as a live-lane failure. Start final upload as soon as audio exists. If final fails and live succeeds, call `acceptLiveTranscript`; if live fails and final succeeds, keep the final turn; if both fail, retain only that Blob, report `double_transcription_failure`, and call `onFatal`. Every terminal success/fallback removes the item state and Blob reference, resolves idle waiters, and calls `onRuntime` with the latest server runtime. `awaitIdle()` rejects with a safe coordinator error while fatal audio is retained, and `retryRetained()` clears that fatal state only after final persistence succeeds.

- [ ] **Step 5: Run coordinator/API/transport verification**

Run:

```bash
npm --prefix web test -- transcription.test.ts realtime.test.ts
npm --prefix web run typecheck
npm --prefix web run lint
```

Expected: all commands pass.

- [ ] **Step 6: Commit convergence logic**

```bash
git add web/src/transcription.ts web/src/transcription.test.ts web/src/api.ts web/src/realtime.ts web/src/realtime.test.ts
git commit -m "feat(transcription): coordinate live and final transcripts"
```

---

### Task 7: Integrate recording, finalization, and Reconnect into the practice room

**Files:**
- Modify: `web/src/PracticePage.tsx`
- Modify: `web/src/PracticePage.test.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: `BufferedUtteranceRecorder`, `TurnTranscriptionCoordinator`, dual-transcription capabilities, and existing Realtime events/state transitions.
- Produces: one-stream recorder lifecycle, nonblocking fallback status, double-failure pause, retained-audio Reconnect, and completion queue wait.

- [ ] **Step 1: Write component tests for the complete state matrix**

Mock the recorder and coordinator at module boundaries. Cover:

- Voice preflight refuses to start when live or final transcription capability is false and explains which deployment is missing.
- Voice preflight refuses to start before microphone capture when MediaRecorder supports none of the allowlisted MIME types.
- One acquired MediaStream is passed to both Realtime transport and recorder.
- VAD start/stop events forward the same item ID to the recorder.
- Live completed/failed events reach the coordinator.
- Assistant transcript persistence waits for preceding candidate finalization, while assistant audio and live display remain immediate, so database sequence stays candidate-before-assistant even when final transcription arrives first; a fatal candidate state retains the assistant text until Reconnect succeeds.
- Final-only failure shows a nonblocking “Using live transcript” status and remains connected.
- Double failure disables the microphone, closes the transport, displays the safe reason, and offers Reconnect.
- Recorder overflow or capture failure uses the same pause path rather than dropping a queued utterance.
- Reconnect calls `retryRetained()` before re-enabling the microphone and accepting new speech.
- Stop shows “Finalizing your transcript,” awaits `awaitIdle()`, then calls `completeInterview()` once.
- Fatal unresolved audio prevents completion rather than generating an assistant-only report.
- Unmount stops recorder, coordinator, transport, and microphone tracks.
- Text-input tests continue to prove no microphone or recorder creation.

- [ ] **Step 2: Run the component tests and verify failure**

Run:

```bash
npm --prefix web test -- PracticePage.test.tsx
```

Expected: failures for missing recorder/coordinator integration and finalizing/reconnect UI.

- [ ] **Step 3: Integrate lifecycle and event routing**

Create recorder/coordinator refs only after voice preflight returns a valid stream. Route `speech_started`, `speech_stopped`, live completed, and live failed events to the new units before existing assistant transcript handling. Keep delivery-observation timestamps associated with the same item ID. On assistant transcript completion, keep the text in memory, await `coordinator.awaitIdle()`, and then persist the assistant turn; this delays only transcript storage, never Realtime audio playback or the live transcript display.

On coordinator fatal callback:

```typescript
transportRef.current?.setMicrophoneEnabled(false);
transportRef.current?.close(false);
setConnection("reconnecting");
setError("Candidate transcription paused. Reconnect to retry this answer.");
void api.connectionState(interview.id, "reconnecting");
```

On reconnect readiness, await `retryRetained()`, then enable the audio track and mark connected. Do not restart the interview introduction.

- [ ] **Step 4: Gate completion on the bounded queue**

Change `stopInterview()` to disable new microphone input, await `recorder.finish()` so its final tail is emitted, show the finalizing state, await `coordinator.awaitIdle()`, and only then call `api.completeInterview()`. If the coordinator is fatal, remain reconnectable and do not complete.

Add minimal status styles using existing tokens; do not add a modal or a second page.

- [ ] **Step 5: Run all frontend verification**

Run:

```bash
npm --prefix web test
npm --prefix web run typecheck
npm --prefix web run lint
npm --prefix web run format:check
npm --prefix web run build
```

Expected: all commands pass.

- [ ] **Step 6: Commit practice-room integration**

```bash
git add web/src/PracticePage.tsx web/src/PracticePage.test.tsx web/src/styles.css
git commit -m "feat(interview): recover voice transcription failures"
```

---

### Task 8: Align privacy documentation and run release verification

**Files:**
- Modify: `web/src/PrivacyPage.tsx`
- Modify: `docs/RETENTION_AND_DELETION.md`
- Modify: `docs/PRIVATE_ALPHA_CHECKLIST.md`
- Modify: `docs/INCIDENT_RUNBOOK.md`
- Modify: `README.md`
- Modify: `web/src/App.test.tsx` or `web/src/PracticePage.test.tsx` for privacy copy assertions

**Interfaces:**
- Consumes: completed dual-transcription behavior and environment names.
- Produces: truthful user-facing audio lifecycle, operator checks, deployment instructions, and complete verification evidence.

- [ ] **Step 1: Write a failing privacy-copy assertion**

Require the rendered privacy/preflight UI to say that voice audio is processed transiently in memory for live and final transcription, is sent to the configured Azure provider, and is not retained by the application. Assert that it no longer says audio only goes directly to Azure Realtime.

- [ ] **Step 2: Run the copy test and verify failure**

Run:

```bash
npm --prefix web test -- App.test.tsx PracticePage.test.tsx
```

Expected: failure against the old direct-Realtime-only copy.

- [ ] **Step 3: Update privacy, operations, and setup documentation**

Update the named files with:

- the four transcription environment variables and actual deployment-name warning;
- `alembic upgrade head` for revision `20260808_0008` before starting against an existing database;
- raw audio’s browser/API memory-only lifecycle and immediate release conditions;
- safe incident checks for `transcription_deployment_missing`, throttling, timeout, fallback count, and double-failure reconnect count;
- a private-alpha test that disables each transcription deployment independently and then both together;
- no claim that transcription is infallible or that the API server never sees audio bytes.

- [ ] **Step 4: Run the complete automated suite**

Run:

```bash
./venv/bin/python -m pytest tests prototype/tests -q
./venv/bin/python -m ruff format --check api domain evals migrations prompts scripts tests
./venv/bin/python -m ruff check api domain evals migrations prompts scripts tests
npm --prefix web test
npm --prefix web run typecheck
npm --prefix web run lint
npm --prefix web run format:check
npm --prefix web run build
```

Expected: every command passes with no skipped dual-transcription tests.

- [ ] **Step 5: Apply and verify the local migration without deleting user data**

First inspect the configured database URL without printing credentials. Then run:

```bash
./venv/bin/python -m alembic upgrade head
./venv/bin/python -m alembic current
```

Expected: current revision is `20260808_0008`. Do not delete or recreate `data/interview_coach.db`.

- [ ] **Step 6: Run a provider smoke test with non-sensitive synthetic speech**

Start the API and web app using the existing local commands. Use generated non-sensitive speech such as “I built a FastAPI service backed by PostgreSQL” to verify:

1. Realtime interview connects with `gpt-realtime-whisper`.
2. The candidate turn appears once.
3. Its runtime metadata becomes `transcription_source="final_model"` with model `gpt-4o-transcribe`.
4. Stopping reaches `REPORT_READY` and the report cites candidate evidence.
5. Server logs contain request IDs/status/latency but no audio bytes or transcript text.

If browser microphone injection is unavailable, verify the final endpoint with a generated WebM/WAV file and leave the live WebRTC microphone check as an explicit user manual test; do not claim it passed.

- [ ] **Step 7: Commit documentation and verification assertions**

```bash
git add web/src/PrivacyPage.tsx docs/RETENTION_AND_DELETION.md docs/PRIVATE_ALPHA_CHECKLIST.md docs/INCIDENT_RUNBOOK.md README.md web/src/App.test.tsx web/src/PracticePage.test.tsx
git commit -m "docs(transcription): document transient audio processing"
```

---

## Completion Report

Report all of the following before declaring the change complete:

- commit IDs for each feature-sized commit;
- migration revision applied locally;
- exact backend and frontend verification commands with pass/fail results;
- whether the final-provider synthetic smoke test passed;
- whether live WebRTC microphone verification was automated or remains a user manual test;
- proof that final failure falls back without pausing and double failure pauses with Reconnect;
- confirmation that no raw audio files were created and no transcript/audio content appeared in logs;
- any remaining limitation, especially browser MIME support or provider-region availability.
