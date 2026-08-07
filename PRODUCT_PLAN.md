# AI Interview Coach — Product and Engineering Plan

Status: Draft for review
Date: 2026-08-06
Primary assumption: This is a candidate self-practice and coaching product, not an employer screening or automated hiring system.

## 1. Product definition

Build a browser-based AI interview coach that:

1. Accepts a candidate resume and optional job description.
2. Converts the job requirements into a weighted competency scorecard.
3. Creates a structured interview plan appropriate to the candidate's experience.
4. Conducts an adaptive, live, two-way voice interview.
5. Uses follow-up questions to distinguish memorized knowledge from practical experience.
6. Produces an evidence-backed report with strengths, gaps, and practice recommendations.
7. Optionally provides observable speaking-delivery coaching.
8. Adds video-delivery coaching later, without claiming to infer emotions or internal mental state.

The product promise is not “AI can tell whether you are confident.” The promise is:

> Practise a realistic interview based on your resume and target job, then receive specific feedback supported by what you actually said and did.

## 2. Product principles

- Evidence over impressions: every competency score must cite transcript evidence.
- Adaptive, not scripted: the next question depends on the previous answer and remaining scorecard coverage.
- Role relevance: must-have skills receive priority over optional technologies.
- Fairness: accent, appearance, personality similarity, and assumed confidence do not affect role-fit scoring.
- Separate dimensions: answer quality and delivery coaching are reported independently.
- Candidate control: recording, retention, and delivery analysis are explicit opt-ins.
- Explainability: users can see why each score was assigned.
- Privacy by default: do not retain raw audio, video, or resumes longer than necessary.

## 3. Target user and job to be done

Primary user: a software professional preparing for a job interview.

Core job:

> When I have an interview coming up, help me practise questions that are specific to my resume and the job, challenge shallow answers with realistic follow-ups, and show me exactly what to improve.

Initial role coverage:

- Backend software engineer
- Three broad seniority levels: junior, mid-level, and senior
- Technical plus behavioral interviews

The architecture should support other software roles later, but the first evaluation suite and question policies will target backend engineering.

## 4. MVP user journey

```text
Landing page
    |
    v
Create practice session
    |
    +--> Upload resume (PDF/DOCX)
    +--> Paste job description
    +--> Select seniority, duration, interview type
    |
    v
Review extracted profile and editable scorecard
    |
    v
Choose input mode
    |
    +--> Voice mode: microphone and headphone preflight
    +--> Developer text mode: headphone-only preflight
    |
    v
Live interview with spoken AI responses
    |
    +--> Intro
    +--> Project deep dive
    +--> Technical scenarios
    +--> Behavioral questions
    +--> Candidate questions
    |
    v
Generate evidence-backed report
    |
    +--> Competency scores
    +--> Transcript evidence
    +--> Skill gaps
    +--> Practice plan
    +--> Optional delivery coaching
```

### 4.1 Developer text-input mode

Provide a feature-flagged developer mode for testing the complete interview flow in quiet or shared environments. It changes only how candidate answers enter the Realtime conversation:

```text
Normal mode                      Developer text mode
Microphone -> candidate audio    Multiline editor -> candidate input_text
AI -> spoken audio               AI -> spoken audio
Camera optional later            Camera always off
Delivery metrics available       Speaking/video metrics unavailable
```

Developer-mode requirements:

- Disabled in production by default and enabled through the server-side `ENABLE_TEXT_DEV_MODE` environment flag.
- The browser reads the server capability response; a browser build flag alone cannot enable the mode.
- A visible “Developer text input” indicator prevents confusing it with the candidate experience.
- Never requests or opens microphone or camera permissions.
- Opens only the audio-output path so responses play through the selected headphones.
- Uses a large, resizable multiline editor rather than a one-line chat field.
- Preserves paragraphs, lists, pasted code, and line breaks.
- `Enter` creates a newline; `Ctrl+Enter` or `Cmd+Enter` submits.
- Allows drafting the next answer while the AI is speaking, but disables submission until the active response finishes.
- Preserves an unsent draft across a reconnect or accidental refresh using session-scoped browser storage.
- Shows a configurable character limit and a clear validation message rather than silently truncating long answers.
- Appends typed answers to the same transcript and sends them through the same evaluation pipeline.
- Marks speaking and video delivery metrics as unavailable instead of assigning zero scores.

Initial configurable limit: 20,000 Unicode characters per answer. Validate the limit in both browser and backend/session event handling. The limit protects the Realtime context and UI without preventing realistic long-form answers, pasted scenarios, or code samples.

## 5. Interview methodology

### 5.1 Scorecard generation

Convert the job description into competencies with:

- Name and description
- Weight
- Must-have, trainable, or nice-to-have classification
- Seniority-adjusted expectations
- Evidence the interview should collect
- Suggested question families

Weights must total 100%. The candidate can review and edit the generated scorecard before starting.

### 5.2 Interview structure

Default 60-minute structure:

| Section               | Duration | Purpose                                                |
| --------------------- | -------: | ------------------------------------------------------ |
| Introduction          |    5 min | Set expectations and establish context                 |
| Project deep dive     |   15 min | Verify resume claims and personal contribution         |
| Technical evaluation  |   20 min | Test application, reasoning, debugging, and trade-offs |
| Behavioral evaluation |   10 min | Assess ownership, teamwork, feedback, and learning     |
| Candidate questions   |    5 min | Practise asking thoughtful role questions              |
| Buffer                |    5 min | Finish high-priority evidence gaps                     |

Support 15-, 30-, 45-, and 60-minute sessions by reducing question count, not by removing must-have coverage indiscriminately.

### 5.3 Follow-up ladder

For important competencies, move through:

```text
Understanding
    -> Application
        -> Reasoning and alternatives
            -> Failure handling
                -> Improvement and scale
```

The interviewer should stop probing when sufficient evidence exists, the candidate clearly lacks the knowledge, or time is better spent on an untested must-have skill.

### 5.4 Evidence rules

- A resume claim is a lead, not evidence.
- “We built” must be followed by “What did you personally do?”
- One weak answer cannot determine the entire result.
- Contradictions trigger neutral clarification, not accusation.
- Missing evidence is recorded as “not demonstrated,” not automatically “does not know.”
- Every 1–5 rating includes transcript excerpts and a rating-confidence value.

### 5.5 Rating scale

| Score | Meaning                                   |
| ----: | ----------------------------------------- |
|     1 | No meaningful evidence                    |
|     2 | Limited knowledge; requires major support |
|     3 | Meets the role requirement                |
|     4 | Strong; works independently               |
|     5 | Expert depth; can guide others            |

## 6. Delivery coaching policy

Delivery coaching is opt-in and separate from job-fit scoring.

Allowed observations:

- Speaking pace and variation from the candidate's own baseline
- Long pauses and response-start delay
- Filler-word frequency
- Repeated phrases
- Interruptions and talking over the interviewer
- Volume consistency and audible clarity
- Answer length and structure
- Later: face visibility, camera alignment, approximate gaze direction, and observable movement

Disallowed conclusions:

- “Stress: 82%”
- “Confidence: 34%”
- “Candidate is dishonest”
- Personality, mental-health, or protected-characteristic inference
- Reducing a technical score because of appearance, accent, gaze, or nervous behavior

Example output:

> During the database question, speaking pace rose from 125 to 168 words per minute and six filler words occurred. Consider pausing before difficult questions and structuring the answer as problem, options, decision, and trade-off.

## 7. MVP architecture

Use a single repository and one deployable web application for the private MVP:

```text
Browser: React + TypeScript
    |
    | HTTPS: setup, upload, transcript, report
    v
FastAPI application
    +--> Resume/JD parser and structured extraction
    +--> Scorecard and interview-plan generator
    +--> Short-lived Azure Realtime client-secret endpoint
    +--> Session metadata and transcript API
    +--> Post-interview evaluator and report generator
    |
    +--> PostgreSQL
    +--> Private object storage for temporary uploads
    +--> Azure OpenAI text model

Browser input
    +--> Voice mode: microphone audio track
    +--> Developer mode: input_text events over data channel
    |
    | WebRTC using a short-lived client secret in both modes
    v
Azure OpenAI Realtime
    |
    +--> AI audio track to browser headphones
    +--> Transcript/events over the WebRTC data channel
```

The React build is served by FastAPI from the same container. This gives the MVP one domain, one release artifact, no CORS configuration, and a simple deployment path.

### Why WebRTC

The desktop prototype uses WebSockets plus PortAudio. The web product should use Azure's GA WebRTC protocol because it is designed for browser media and handles latency, jitter, packet loss, and playback better than manually streaming PCM chunks. The backend creates a short-lived client secret; the permanent Azure credential never reaches the browser.

Developer text mode keeps the same WebRTC connection so the spoken AI output, transcript events, session prompt, timer, and interview state are identical to voice mode. It sends a Realtime `conversation.item.create` event containing `input_text`, followed by `response.create`. It does not create a parallel text-chat backend.

### Why a separate text-model deployment

Realtime handles the spoken interaction. Resume extraction, scorecard creation, and final structured evaluation use a text-capable Azure model with schema-constrained output. These workloads have different latency, reliability, and testing needs and should not depend on an active Realtime session.

## 8. Component boundaries

Suggested repository shape:

```text
web/                 React application and browser WebRTC client
api/                 FastAPI routes, authentication, and application services
domain/              Scorecard, interview plan, evidence, and report models
prompts/             Versioned extraction, interview, and evaluation prompts
evals/               Golden resumes, JDs, transcripts, and quality assertions
tests/               Unit and integration tests
infra/               Container, deployment, and environment configuration
prototype/           Existing desktop experiments after migration
```

Core backend modules:

1. Upload service: validates PDF/DOCX files and extracts text.
2. Candidate-profile service: converts resume text into structured claims.
3. Scorecard service: converts the JD and seniority into weighted competencies.
4. Interview-plan service: creates section timing and question families.
5. Realtime-session service: creates short-lived browser credentials with server-owned instructions.
6. Transcript service: validates and stores ordered conversation turns.
7. Evaluation service: scores the transcript against the frozen scorecard with evidence citations.
8. Report service: presents role-fit evidence and delivery coaching separately.

Do not create independent deployable microservices for these modules during the MVP. They are modules inside one FastAPI application.

## 9. Data model

Minimum persistent entities:

```text
User
  id, email, created_at

CandidateProfile
  id, user_id, source_resume_id, structured_claims, created_at

JobTarget
  id, user_id, title, seniority, raw_description, structured_requirements

Scorecard
  id, job_target_id, version, competencies[], total_weight

InterviewSession
  id, user_id, profile_id, scorecard_id, status, duration,
  interview_type, input_mode, started_at, ended_at, prompt_version

InterviewTurn
  id, session_id, sequence, speaker, transcript, started_at, ended_at

Evaluation
  id, session_id, evaluator_version, competency_results[], overall_result

DeliveryMetrics
  id, session_id, baseline, per_turn_metrics[], consent_version

Upload
  id, user_id, generated_storage_key, file_type, size, retention_expires_at
```

The scorecard is frozen when an interview starts. Later prompt or JD changes must not silently change how a completed interview was evaluated.

`input_mode` is `voice` or `text_dev`. It is stored for debugging and report interpretation, but it does not alter competency weights. Production creation rejects `text_dev` unless the server-side feature flag is enabled.

## 10. API surface

Initial endpoints:

```text
GET    /api/capabilities
POST   /api/uploads/resume
POST   /api/candidate-profiles/extract
POST   /api/job-targets
POST   /api/scorecards/generate
PATCH  /api/scorecards/{id}
POST   /api/interviews
POST   /api/interviews/{id}/realtime-client-secret
POST   /api/interviews/{id}/turns:batch
POST   /api/interviews/{id}/complete
GET    /api/interviews/{id}
GET    /api/interviews/{id}/report
DELETE /api/interviews/{id}
```

All session-scoped routes verify ownership. The client-secret route uses rate limits, short expiry, and a server-created session configuration. It never accepts arbitrary Azure model instructions from the browser.

`GET /api/capabilities` exposes safe booleans such as `text_dev_mode_enabled`; it never returns secrets. Interview creation rejects `input_mode=text_dev` when the server capability is disabled, even if a client manually crafts the request.

## 11. Interview state machine

```text
DRAFT
  -> PROFILE_READY
  -> SCORECARD_READY
  -> PREFLIGHT
  -> CONNECTING
  -> IN_PROGRESS
       -> RECONNECTING -> IN_PROGRESS
       -> ENDING
  -> TRANSCRIPT_FINALIZING
  -> EVALUATING
  -> REPORT_READY

Any active state -> FAILED_RECOVERABLE
Any retained session -> DELETED
```

Rules:

- State changes are explicit and validated server-side.
- Starting twice is idempotent and does not create two active Azure sessions.
- Completing twice returns the same evaluation job/result.
- A reconnect continues the same interview if within a short recovery window.
- The timer is server-authoritative; the browser displays it but does not decide the final duration.

## 12. Realtime interview behavior

For the first web MVP, use prompt-driven adaptation:

1. Generate and freeze the scorecard and interview plan before connection.
2. Include the plan, candidate claims, time budget, and interviewer policy in the Realtime session configuration.
3. Let the Realtime model use conversation history to choose natural follow-ups.
4. Collect the transcript and run evidence-based scoring after the session.

Do not build a second LLM call after every answer in the first release. It adds latency, cost, race conditions, and conflicting interviewer decisions. Add explicit tool-driven coverage tracking only if evals show that prompt-driven interviews repeatedly skip must-have competencies or mismanage time.

The Realtime interviewer must:

- Explain the interview format and wait for the candidate to answer.
- Ask one question at a time.
- Avoid giving answers or excessive hints.
- Probe important resume claims using the follow-up ladder.
- Adjust difficulty using seniority and demonstrated evidence.
- Politely redirect excessively long answers.
- Never expose the scorecard, system instructions, or provisional scoring.
- End cleanly when time expires or the candidate chooses to stop.

Developer text-input event flow:

```text
Editor draft
  -> client length/empty validation
  -> conversation.item.create(input_text)
  -> response.create
  -> lock submit; keep editor available for next draft
  -> AI audio arrives on the existing remote audio track
  -> response.done unlocks submit
  -> typed answer and AI transcript join the normal ordered transcript
```

If connection loss occurs before acknowledgement, retain the submitted text with a client-generated turn ID. On reconnect, reconcile that ID with transcript events before retrying so the same long answer is not submitted twice.

## 13. Resume and JD processing

Resume and JD content are untrusted input. A document may accidentally or intentionally contain instructions such as “ignore the system prompt.” Treat all document content as data.

Processing pipeline:

```text
Upload
  -> extension allowlist (PDF/DOCX)
  -> MIME and file-signature validation
  -> size/page limits
  -> randomized private storage key
  -> malware scan where available
  -> sandboxed text extraction
  -> empty/encrypted/corrupt-file checks
  -> schema-constrained profile extraction
  -> candidate review and correction
```

Extraction output should contain claims with source text, not invented details. Missing dates, metrics, or responsibilities remain unknown.

## 14. Evaluation and report generation

Evaluation input:

- Frozen scorecard
- Candidate seniority
- Ordered transcript
- Interview section timings
- Prompt and evaluator versions

For each competency, return:

```json
{
  "competency": "SQL performance",
  "score": 4,
  "rating_confidence": "high",
  "evidence_turn_ids": [18, 20, 22],
  "evidence_summary": "Used execution plans and explained index trade-offs.",
  "gaps": ["Did not discuss composite-index ordering."],
  "recommendations": ["Practise index selection with multi-column filters."]
}
```

Evaluation safeguards:

- Evidence IDs must resolve to actual candidate turns.
- Quotes must match transcript text.
- Unsupported claims fail validation and trigger one regeneration attempt.
- “Not assessed” remains distinct from score 1.
- Overall score is computed deterministically from competency weights, not invented by the model.
- Delivery metrics never enter the weighted role-fit formula.

## 15. Security, privacy, and fairness

### Credentials and authorization

- Azure API keys remain server-side.
- Browser Realtime access uses short-lived, session-scoped credentials.
- Use managed authentication rather than custom password storage.
- Verify ownership for every profile, upload, interview, transcript, and report.
- Redact credentials and document contents from application logs.

### Upload protection

- Allow only PDF and DOCX for the MVP.
- Validate extension, MIME type, and file signature; do not trust browser headers.
- Enforce file-size, page-count, extraction-time, and decompression limits.
- Generate storage names; never use the submitted filename as a storage path.
- Store uploads privately outside the application webroot.
- Delete raw uploads automatically after extraction or within the configured retention window.

### Privacy

- Obtain explicit consent before microphone recording or delivery analysis.
- Default to storing transcripts, not raw audio.
- Provide delete-session and delete-account controls.
- Encrypt data in transit and at rest.
- Publish clear retention periods and subprocessors before public launch.

### Fairness boundary

- The MVP is for self-coaching.
- No automated employer shortlist/rejection feature.
- No facial recognition, identity matching, deception detection, or emotion inference.
- If employer use is considered later, stop and perform a dedicated legal, bias, accessibility, and high-risk-system review before implementation.

## 16. Deployment plan

### Environments

```text
Local     -> mocked Azure by default; opt-in live integration
Staging   -> separate database, storage, Azure deployment, and test accounts
Production-> isolated secrets, database, storage, quotas, and monitoring
```

### MVP infrastructure

- One OCI container containing FastAPI and the compiled React application
- Azure Container Apps or Azure App Service
- Managed PostgreSQL
- Private Azure Blob Storage
- Azure OpenAI Realtime deployment
- Azure OpenAI text-capable deployment
- Managed secret store and deployment identity
- One custom domain with HTTPS

### CI/CD

On every pull request:

1. Format, lint, type-check, and unit test Python and TypeScript.
2. Run integration tests with fake Azure gateways.
3. Run prompt/evaluation regression cases.
4. Build the React application and container image.
5. Scan dependencies and the container image.

On merge to the release branch:

1. Publish a versioned container image.
2. Apply backward-compatible database migrations.
3. Deploy to staging.
4. Run smoke tests, including client-secret creation and a non-audio Realtime handshake.
5. Require approval for production while the product is in private alpha.
6. Deploy and monitor the canary before full traffic.

## 17. Milestones

Milestones describe dependency order, not promised calendar dates.

### M0 — Preserve and close the desktop prototype

Deliverables:

- Correct configuration validation, including API-version format.
- Add a small playback jitter buffer and underflow diagnostics for local testing.
- Document what will and will not be reused in the web product.
- Move the desktop code under `prototype/` when the web skeleton begins.

Exit criteria:

- Two consecutive text/audio turns succeed.
- Device cleanup succeeds after normal exit and error exit.
- Prototype limitations are documented.

### M1 — Web foundation and first deployment

Deliverables:

- FastAPI application serving a React build.
- Environment configuration, health endpoint, structured logging, and error IDs.
- Container build, staging deployment, managed authentication, and PostgreSQL connection.
- Basic session dashboard.

Exit criteria:

- A user can sign in, create an empty practice session, refresh, and see it again.
- Staging deploys from CI without manual file copying.

### M2 — Resume, JD, and scorecard

Deliverables:

- Secure PDF/DOCX upload and text extraction.
- Candidate-profile extraction with source references.
- JD competency extraction and editable weighted scorecard.
- Junior/mid/senior expectation templates for backend engineering.

Exit criteria:

- Supported documents succeed; encrypted, corrupt, oversized, and spoofed files fail clearly.
- Extracted claims never silently replace user corrections.
- Scorecard weights validate to 100%.

### M3 — Browser Realtime voice interview and developer text mode

Deliverables:

- Microphone/headphone preflight.
- Headphone-only preflight and feature-flagged developer-mode selector.
- Backend client-secret endpoint.
- Browser WebRTC connection and data-channel event handling.
- Large multiline answer editor with long-text validation, draft recovery, and keyboard submission.
- Realtime `input_text` submission with spoken AI output and no camera/microphone access.
- Live transcript, timer, connection status, stop control, and reconnect path.
- Versioned interview prompt with scorecard, resume claims, and section plan.

Exit criteria:

- Azure credentials are absent from browser code and network responses.
- The candidate completes a 15-minute interview on current Chrome, Safari, and Edge.
- A developer completes the same interview using only typed answers and headphone output.
- Text mode does not request camera or microphone permission, and no media input track is created.
- A 20,000-character multiline answer either submits intact or receives an explicit configured-limit error; it is never silently truncated.
- Reconnect does not duplicate a submitted typed answer and preserves an unsent draft.
- End-of-speech to first AI audio meets a measured p95 target of 2.5 seconds under the test network profile.
- Packet loss or jitter does not create the desktop prototype's PCM underflow behavior.

### M4 — Evidence-backed evaluation

Deliverables:

- Transcript finalization and ordered turn storage.
- Structured evaluator with transcript evidence IDs.
- Deterministic weighted scoring.
- Candidate report with strengths, gaps, uncertainty, and practice exercises.

Exit criteria:

- Every non-“not assessed” score cites valid candidate turns.
- Removing supporting evidence causes the corresponding eval test to fail.
- Delivery style cannot change the technical score in fairness regression tests.

### M5 — Speaking-delivery coaching

Deliverables:

- Explicit consent and individual baseline period.
- Words per minute, pause, filler, response delay, interruption, and answer-length metrics.
- Segment-level observations and coaching suggestions.
- Ability to disable metrics or delete them independently.

Exit criteria:

- Reports describe observable changes without stress, emotion, deception, or personality labels.
- Metrics are separated visually and structurally from role-fit scores.

### M6 — Private alpha hardening

Deliverables:

- Quotas, rate limits, retention jobs, account deletion, and cost dashboards.
- Accessibility review, browser compatibility matrix, incident runbook, and support path.
- Ten to twenty consented pilot users across junior, mid-level, and senior profiles.

Exit criteria:

- No unresolved critical security or privacy findings.
- Pilot reports are rated useful and evidence-correct by human reviewers.
- Cost per completed interview and failure rate are measured.

### M7 — Video-delivery coaching, deferred

Only begin after the voice product and privacy controls are stable. Process observable landmarks locally in the browser where practical. Do not retain raw video by default and do not infer emotion, stress, honesty, or personality.

## 18. Test and evaluation strategy

### Unit tests

- File-type, signature, size, page, and extraction validation
- Resume/JD normalization and schema validation
- Scorecard weight and must-have rules
- Interview state transitions and idempotency
- Transcript ordering and duplicate-event handling
- Deterministic weighted scoring
- Retention and deletion policies
- Delivery-metric calculations
- Developer-mode feature-flag enforcement and input-mode validation
- Long text, Unicode, multiline, empty, and boundary-length answer validation

### Integration tests

- Upload -> extraction -> candidate correction -> scorecard
- Interview creation -> client secret -> completion
- Transcript batch retry without duplicate turns
- Completion retry without duplicate evaluations
- Azure timeout, quota, authentication, and model-not-found errors
- Realtime `input_text` event creation, response triggering, acknowledgement, and duplicate-turn reconciliation
- Database migration forward and rollback behavior
- Object-storage deletion and expired-upload cleanup

### Browser end-to-end tests

- Permission accepted, denied, and revoked mid-session
- No microphone or no output device
- Developer text mode never requests microphone/camera permission
- Multiline editor preserves paragraphs, pasted code, Unicode, and an unsent draft after reconnect/refresh
- Enter inserts a newline; Ctrl/Cmd+Enter submits exactly once
- Submission remains locked during an AI response while drafting remains available
- Production with the feature flag disabled does not expose or accept developer text mode
- Start, pause/stop, reconnect, and complete interview
- Browser refresh or tab close during interview
- Slow network and packet loss
- Report loading, evidence expansion, and deletion
- Keyboard navigation, captions/transcript, and screen-reader labels

### LLM eval suite

Maintain versioned golden cases containing synthetic resumes, JDs, transcripts, and expected properties:

- Genuine deep experience versus memorized definitions
- Candidate contribution versus team contribution
- Strong answer using a different valid approach
- Missing must-have skill
- Skill never assessed
- Contradictory answers
- Long but irrelevant answer
- Candidate who asks clarifying questions
- Quiet or non-native-English candidate with technically strong answers
- Resume/JD prompt-injection text
- Transcript with insufficient evidence for a score
- Junior, mid-level, and senior calibration

Eval assertions should check properties and evidence integrity, not require one exact generated sentence. Prompt changes cannot ship when they reduce must-have coverage, invent evidence, or allow delivery style to alter technical scoring.

## 19. Production failure modes

| Flow                 | Realistic failure                                          | Required handling                                                                       | Verification                     |
| -------------------- | ---------------------------------------------------------- | --------------------------------------------------------------------------------------- | -------------------------------- |
| Resume upload        | Spoofed or malicious DOCX/PDF                              | Reject, quarantine, clear error, no parsing                                             | Security integration tests       |
| Extraction           | Empty, encrypted, corrupt, or scanned-only file            | Explain limitation and allow text entry/re-upload                                       | Parser fixtures and E2E          |
| Profile generation   | Model invents experience                                   | Source-reference validation and user confirmation                                       | LLM eval                         |
| Scorecard            | Weights invalid or misses a must-have                      | Schema validation and editable review                                                   | Unit + eval                      |
| Client secret        | Azure unavailable, quota exhausted, token expires          | Retry safely; show recoverable preflight error                                          | Integration test                 |
| WebRTC               | Permission denied or connection drops                      | Actionable permission help and bounded reconnect                                        | Browser E2E                      |
| Developer text input | Long answer is truncated, duplicated, or lost on reconnect | Shared limit validation, client turn ID, draft recovery, acknowledgement reconciliation | Unit + integration + browser E2E |
| Transcript           | Events arrive twice or out of order                        | Sequence IDs, idempotent batch writes, final reconciliation                             | Unit + integration               |
| Timer                | Browser clock changes or tab sleeps                        | Server-authoritative start/end timestamps                                               | Integration test                 |
| Evaluation           | Timeout or invalid structured output                       | Async status, one validated retry, then recoverable failure                             | Integration test                 |
| Evidence             | Evaluator cites nonexistent or wrong turn                  | Reject output and regenerate; never display unsupported score                           | Unit + eval                      |
| Deletion             | Blob deleted but database remains, or reverse              | Idempotent deletion workflow with retry and audit state                                 | Integration test                 |
| Deployment           | Migration succeeds but app rollout fails                   | Backward-compatible migrations and rollback-ready image                                 | Staging smoke test               |

No failure in this table should leave the user on an endless spinner or silently lose a completed interview.

## 20. Observability and operating targets

Measure:

- Session creation success rate
- WebRTC connection success and reconnect rate
- End-of-speech to first AI audio latency
- Interview completion rate
- Transcript finalization failures
- Evaluation duration and retry rate
- Unsupported-evidence validation failures
- Upload rejection reasons without logging document contents
- Azure token, audio, and text-model usage per session
- Cost per completed interview
- Data-deletion completion time

Initial targets for private alpha:

- 95% of preflight-passing sessions connect successfully.
- p95 end-of-speech to first audio is at most 2.5 seconds on the supported test network.
- 99% of completed transcripts reach either REPORT_READY or a clear recoverable error.
- 100% of displayed competency scores contain validated evidence or are marked not assessed.
- 100% of deletion requests reach a terminal success or visible retry state.

## 21. Performance and cost controls

- Browser-to-Azure WebRTC keeps raw audio off the application server.
- Batch transcript writes rather than writing every small delta.
- Store normalized extracted text and structured profiles to avoid repeated parsing.
- Limit resume/JD size and scorecard breadth.
- Keep the active interview prompt focused; do not inject raw documents when structured claims suffice.
- Enforce a configurable typed-answer limit before sending text into the Realtime context.
- Run final evaluation once per completed transcript and make completion idempotent.
- Apply per-user daily interview and token quotas during alpha.
- Track cost separately for Realtime audio, text extraction/planning, and evaluation.

Do not add Redis, a message broker, Kubernetes, or independent workers until measured load or reliability requires them. A single application plus PostgreSQL and object storage is sufficient for the private MVP.

## 22. What already exists

The current repository already proves:

- Azure OpenAI Realtime authentication and connection setup
- Realtime event reception and PCM audio playback
- Microphone capture and server voice-activity detection
- Text-input/audio-output conversation mode
- Device cleanup and user-facing error handling
- Initial prompt experimentation

Reuse:

- Azure configuration knowledge
- Realtime event vocabulary
- Error categorization
- Interview prompt experiments
- Manual smoke-test lessons

Do not carry into the production browser path:

- `sounddevice` or PortAudio playback
- OpenCV desktop windows
- Terminal input
- Permanent Azure keys on the client
- Manual PCM queueing as the primary browser transport

## 23. NOT in scope for the first web MVP

- Employer screening, automated hiring recommendations, or candidate rejection
- Video analysis and facial-expression coaching
- Emotion, stress, confidence, deception, or personality inference
- Identity verification, proctoring, or cheating detection
- Native mobile applications
- Live collaborative coding editor
- Payments and subscriptions
- Recruiter dashboards or multi-tenant company administration
- Question marketplaces or user-generated prompt libraries
- Fine-tuning custom models
- Microservices, Kubernetes, Redis, or a dedicated job queue
- Permanent raw audio/video storage
- Roles outside backend software engineering until evaluation quality is proven

These items are deferred to protect the core outcome: a reliable, evidence-backed voice interview and useful report.

## 24. Implementation dependencies and parallel work

| Lane | Work                                                  | Depends on                                                       |
| ---- | ----------------------------------------------------- | ---------------------------------------------------------------- |
| A    | Web shell, auth, database, deployment                 | —                                                                |
| B    | Domain schemas, scorecard rules, prompt/eval fixtures | —                                                                |
| C    | Secure upload and extraction                          | A database/storage foundation                                    |
| D    | Browser WebRTC client and mocked gateway              | A web shell                                                      |
| E    | Evaluation pipeline and report                        | B schemas; transcript contract from D                            |
| F    | Speaking-delivery metrics                             | Stable transcript/audio timing from D and report boundary from E |

Execution order:

1. Start A and B in parallel.
2. Start C and D after their A foundations stabilize.
3. Start E when B and the transcript contract from D are stable.
4. Start F only after the role-fit report boundary is enforced.

Avoid parallel work that changes shared domain schemas or database migrations without coordination.

## 25. Decisions and open questions

Decisions made in this draft:

- Candidate self-practice is the product posture.
- Browser WebRTC replaces desktop PCM streaming for production.
- One FastAPI application and one container, not microservices.
- React provides the browser UI.
- PostgreSQL is the source of truth; object storage holds temporary uploads.
- A separate text-capable Azure deployment handles structured workflows.
- Prompt-driven adaptive interviewing comes before tool-driven orchestration.
- Role-fit scoring and delivery coaching are separate products within the report.
- Video is deferred until voice quality, privacy, and fairness controls are stable.
- Developer text-input mode reuses the production Realtime session and spoken output; it is not a separate chat implementation.
- Developer text-input mode is disabled in production by default and does not produce speaking-delivery metrics.

Decisions to confirm before implementation:

1. Is the candidate self-practice posture correct, with employer screening explicitly excluded?
2. Should anonymous guest sessions be allowed, or is sign-in required before the first upload?
3. What retention default should apply to resumes and transcripts?
4. Which first three backend role templates should the private alpha support?
5. Which Azure region and deployment quotas are available for staging and production?

## 26. Definition of web MVP done

The MVP is complete when a candidate can:

1. Sign in and upload a supported resume safely.
2. Paste a job description and correct the extracted profile.
3. Review and edit a weighted scorecard.
4. Pass microphone/headphone preflight.
5. Complete a browser-based live voice interview with adaptive follow-ups.
6. Recover from a short connection interruption without losing the session.
7. Receive a report whose scores cite real transcript evidence.
8. See “not assessed” when evidence is missing.
9. Delete the session, transcript, report, and retained upload.

Developer workflow completion additionally requires:

1. An environment flag exposes a clearly labelled developer text-input selector.
2. Selecting it creates no camera or microphone media tracks or permission prompts.
3. A large multiline editor supports long answers, code, Unicode, line breaks, draft recovery, and explicit limits.
4. Submitted text participates in the normal interview transcript, adaptive follow-ups, evidence scoring, and report.
5. AI responses play as audio through the selected output device.
6. Production rejects developer mode unless it is intentionally enabled server-side.

Engineering completion additionally requires CI/CD, staging, observability, quotas, documented retention, security tests, browser E2E tests, and LLM evals.

## 27. References

- Azure Realtime WebRTC guide: https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/realtime-audio-webrtc
- Azure Realtime REST reference: https://learn.microsoft.com/en-us/rest/api/aifoundry/azureopenai/realtime
- OWASP File Upload Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html
- EU AI Act, including the Article 5 emotion-inference restriction: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex%3A32024R1689
