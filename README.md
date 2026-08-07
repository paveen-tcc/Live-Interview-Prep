# AI Interview Coach

A browser-based interview-practice product built as one React + TypeScript and
FastAPI application. The local build now covers the M1–M6 product path: secure
résumé/JD setup, browser Realtime interviews with a quiet-room text mode,
evidence-backed reports, optional speaking-delivery coaching, and private-alpha
quota, retention, deletion, and usage controls. Cloud deployment remains
intentionally deferred during local product development.

The completed desktop experiment is preserved under [`prototype/`](prototype/README.md).

## Repository layout

```text
api/          FastAPI routes, auth, persistence, and M2 application services
domain/       Validated candidate-profile and scorecard contracts
web/          React + TypeScript dashboard and Vite build
migrations/   Alembic database migrations
tests/        Backend unit, security, integration, and migration tests
infra/        Staging deployment contract
prototype/    Preserved M0 desktop experiments
```

## Local development

Python 3.11+ and Node.js 22.14+ are recommended.

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -e ".[dev]"
npm --prefix web install
```

The default local configuration uses a file-backed SQLite database and an
explicit local developer identity. Custom passwords are never implemented.

Run the API:

```bash
uvicorn api.main:app --reload
```

In another terminal, run the Vite client:

```bash
npm --prefix web run dev
```

Open `http://localhost:5173`. Create a practice session, upload a PDF or DOCX
resume, review its extracted claims, then paste a backend job description. The
generated scorecard is editable but cannot be saved unless its weights total
exactly 100%. Continue to preflight to start an M3 interview.

Resume controls are configurable in `.env`. Defaults are 5 MB, 10 PDF pages,
200,000 extracted characters, 500 DOCX package entries, 20 MB expanded DOCX
data, and an 8-second extraction timeout. The server validates extension, MIME
type, and file signature; rejects encrypted, corrupt, empty/scanned-only,
oversized, and macro-enabled files; and discards raw file bytes immediately after
extraction. Only normalized text, source segments, a content hash, and upload
metadata persist.

When `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY` are present, local
résumé intake uses one structured-output call to the configured
`AZURE_OPENAI_TEXT_DEPLOYMENT` (default: `gpt-5.6-luna`). The result is cached in
the candidate profile; page loads and edits do not call the model. Every AI
evidence item must contain a source ID and a supporting quote that the server
can match back to extracted résumé text. Unsupported evidence rejects the
response instead of being stored. Set `PROFILE_EXTRACTION_MODE=rules` for the
deterministic fallback. Existing rule-extracted profiles show an explicit
**Improve with AI** action, which refuses to overwrite saved user corrections.

### Configure browser Realtime interviews

Keep the permanent Azure key on the FastAPI server. The browser receives only a
short-lived client secret and the fixed Azure WebRTC calls URL. Add these values
to `.env`:

```text
AZURE_OPENAI_REALTIME_DEPLOYMENT=gpt-realtime-2.1
AZURE_OPENAI_REALTIME_VOICE=alloy
AZURE_OPENAI_TRANSCRIPTION_MODEL=gpt-4o-mini-transcribe
ENABLE_TEXT_DEV_MODE=true
```

The endpoint and API key already used for AI profile extraction are reused.
`ENABLE_TEXT_DEV_MODE` is intended for local testing and should stay disabled in
production. In text mode the page creates no camera or microphone input track;
Azure still returns the interviewer's spoken audio. Typed answers accept up to
20,000 Unicode characters by default and are rejected explicitly above that
limit rather than truncated.

### Use Neon PostgreSQL locally

Create a Neon project, open its **Connect** dialog, disable connection pooling,
and copy the direct connection string. Do not paste the connection string into
chat or commit it because it contains the database password.

Copy `.env.example` to `.env`, then set:

```text
APP_ENV=local
AUTH_MODE=local
AUTO_CREATE_SCHEMA=false
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@ep-example.REGION.aws.neon.tech/neondb?sslmode=verify-full
```

If Neon supplied a URL beginning with `postgresql://`, the application adds the
`asyncpg` driver automatically. It also adapts Neon's libpq-oriented
`channel_binding` option to asyncpg with full TLS certificate and hostname
verification.

Apply the schema, then start the API:

```bash
source venv/bin/activate
alembic upgrade head
uvicorn api.main:app --reload
```

In another terminal, start the dashboard with `npm --prefix web run dev`. Create
a session and refresh to confirm that it persists in Neon.

`compose.yaml` remains available as an offline PostgreSQL alternative. SQLite
remains the default zero-setup option.

### Private-alpha privacy and operations

The **Privacy & usage** workspace shows daily session quota, content-free usage
counts, active retention defaults, and whether provider cost telemetry is
available. A session can be deleted from its card after confirmation. Account
deletion requires typing `DELETE MY ACCOUNT`; repeated deletion calls are
idempotent and a PII-free terminal receipt prevents silent recreation.

Run configured retention cleanup with:

```bash
python scripts/run_retention.py
```

See [retention and deletion](docs/RETENTION_AND_DELETION.md), the [browser and
accessibility matrix](docs/BROWSER_COMPATIBILITY.md), the [incident
runbook](docs/INCIDENT_RUNBOOK.md), and the [private-alpha exit
checklist](docs/PRIVATE_ALPHA_CHECKLIST.md).

## One-artifact production build

```bash
npm --prefix web run build
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

FastAPI serves `web/dist` and all `/api` routes from the same origin. The
multi-stage `Dockerfile` builds this exact arrangement.

## Authentication environments

- `local`: the server supplies one explicit developer identity for local work.
- `staging` and `production`: configuration requires PostgreSQL,
  `AUTH_MODE=easy_auth`, and schema migrations. Microsoft Entra External ID sends
  a one-time code to the user's email; users do not create a password or need a
  Microsoft account. FastAPI trusts only Azure Container Apps managed-auth
  headers and never accepts browser-selected users.

Unauthenticated staging users see a passwordless email sign-in action. Every
session query is scoped to the authenticated database user.

## Verification

```bash
python -m ruff format --check api domain evals migrations prompts scripts tests
python -m ruff check api domain evals migrations prompts scripts tests
python -m pytest tests
python -m unittest discover -s prototype/tests -v
npm --prefix web run format:check
npm --prefix web run lint
npm --prefix web run typecheck
npm --prefix web run test
npm --prefix web run build
```

CI repeats these checks and builds the OCI container. Azure staging is currently
deferred; the retained deployment contract is documented in
[`infra/staging/README.md`](infra/staging/README.md).

## M3 manual acceptance test

1. Create a practice session. The setup page should open immediately.
2. Upload a text-based PDF or DOCX under 5 MB and select **Extract profile**.
3. Generate and save a scorecard whose weights total 100%, then select **Continue to preflight**.
4. Play the headphone test sound and confirm it. Select **Developer text** when speaking is not practical. The browser must not request camera or microphone permission.
5. Start a 15-minute interview. Confirm spoken AI audio, the live transcript, connection state, and countdown.
6. Submit multiline text with Ctrl/Cmd+Enter. Enter alone must add a newline. Refresh before submitting a second draft and confirm the draft returns.
7. Disconnect and reconnect once. A pending submitted answer must not appear twice.
8. Stop the interview and wait for the evidence-backed report. Expand one transcript excerpt and confirm its quote matches what was submitted.
9. In text mode, confirm **Speaking delivery** is marked unavailable rather than scored.
10. Open **Privacy & usage** and verify the active quota/retention policy. Test session deletion only with a disposable session.

Voice-mode completion on current Chrome, Safari, and Edge, plus the documented
latency and impaired-network measurements, remain manual M3 release checks.
