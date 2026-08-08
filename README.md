# AI Interview Coach

A browser-based interview-practice product built as one React + TypeScript and
FastAPI application. It covers the M1–M6 product path: secure résumé/JD setup,
browser Realtime interviews with a quiet-room text mode, evidence-backed
reports, optional speaking-delivery coaching, and private-alpha quota,
retention, deletion, and usage controls.

It deploys to [Zerops](https://docs.zerops.io/) as a single service, with
PostgreSQL on Supabase and sign-in through Clerk.

## Repository layout

```text
server/       FastAPI application, domain contracts, migrations, and tests
web/          React + TypeScript dashboard and Vite build
zerops.yml    Build and deploy pipeline
zerops-import.yml
              One-time Zerops infrastructure definition
```

Working notes, specs, plans, and the preserved M0 desktop prototype live in
`.meta/`, which is deliberately untracked. Nothing in the build depends on it.

## Local development

Python 3.11+ and Node.js 22.14+ are recommended.

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -e "./server[dev]"
npm --prefix web install
```

The default local configuration uses a file-backed SQLite database and an
explicit local developer identity, so there is no sign-in step and no Clerk
account needed to run the app locally.

Run the API from the `server/` directory:

```bash
cd server && uvicorn api.main:app --reload
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
ENABLE_TEXT_DEV_MODE=true
```

The endpoint and API key already used for AI profile extraction are reused.
`ENABLE_TEXT_DEV_MODE` is intended for local testing and should stay disabled in
production. In text mode the page creates no camera or microphone input track;
Azure still returns the interviewer's spoken audio. Typed answers accept up to
20,000 Unicode characters by default and are rejected explicitly above that
limit rather than truncated.

### Configure dual voice transcription

Voice interviews transcribe each answer twice: live during the interview so the
candidate sees text immediately, and again after the answer ends with a
higher-accuracy model whose text replaces the live text. Both lanes are
required — voice preflight refuses to start when either is unconfigured, and
names the missing one.

```text
AZURE_OPENAI_REALTIME_TRANSCRIPTION_MODEL=gpt-realtime-whisper
AZURE_OPENAI_FINAL_TRANSCRIPTION_DEPLOYMENT=gpt-4o-transcribe
AZURE_OPENAI_TRANSCRIPTION_LANGUAGE=en
AZURE_OPENAI_TRANSCRIPTION_DELAY=low
AZURE_OPENAI_TRANSCRIPTION_API_VERSION=2024-06-01
```

These two values must be your **Azure deployment names, not model names**. They
are identical only when the deployment was created with its model's name. A
deployment named `whisper-prod` must be written as `whisper-prod` here; a
model-name value that does not exist as a deployment fails at request time with
`transcription_deployment_missing` rather than at startup.

Final transcription calls the deployment-scoped route
`/openai/deployments/<deployment>/audio/transcriptions`, selected by
`AZURE_OPENAI_TRANSCRIPTION_API_VERSION`. Azure AI Foundry resources answer
`DeploymentNotFound` on the unified `/openai/v1/audio/transcriptions` surface
even when the deployment exists, so do not switch to it without verifying
against the target resource first.

`AZURE_OPENAI_TRANSCRIPTION_LANGUAGE` biases both lanes toward one language.
`AZURE_OPENAI_TRANSCRIPTION_DELAY` trades live latency against live accuracy;
it does not affect the final transcript. Timeout and upload size are tunable
with `AZURE_OPENAI_FINAL_TRANSCRIPTION_TIMEOUT_SECONDS` (default 30) and
`AZURE_OPENAI_FINAL_TRANSCRIPTION_MAX_BYTES` (default 25 MB).

Answer audio is buffered in browser memory and streamed through the API to
Azure without ever being written to disk on either side. See
`.meta/docs/RETENTION_AND_DELETION.md` for the exact
lifecycle and release points.

Transcription is not infallible. When the final lane fails the interview
continues on the live transcript and shows a nonblocking status; when both lanes
fail for one answer the microphone pauses and the candidate must press
**Reconnect**, which retries the retained answer.

Running against a database created before this feature requires the new
transcript-provenance columns. The head revision is `20260808_0008`.

For an Alembic-managed database:

```bash
cd server && alembic upgrade head
```

For a database first created with `AUTO_CREATE_SCHEMA=true`, `upgrade head`
fails with "table users already exists": the tables exist but no
`alembic_version` row records that. Tell Alembic what the schema already matches
before upgrading:

```bash
cd server
alembic stamp 20260807_0007
alembic upgrade head
```

Only stamp a database whose schema really is at `20260807_0007`; stamping
records a revision without running it. Revision `20260808_0008` adds three
nullable-or-defaulted columns to `interview_turns` and backfills existing rows
with `transcription_source="legacy"`, so no turn, session, or report is lost.
Back the file up first anyway:

```bash
cp data/interview_coach.db data/interview_coach.db.bak
```

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
cd server
alembic upgrade head
uvicorn api.main:app --reload
```

In another terminal, start the dashboard with `npm --prefix web run dev`. Create
a session and refresh to confirm that it persists in Neon.

SQLite remains the default zero-setup option for local work.

### Use Supabase PostgreSQL

Supabase is the deployed database. In the Supabase dashboard open **Project
Settings → Database → Connection string → URI** and copy the **session pooler**
URL, which looks like:

```text
postgresql://postgres.PROJECTREF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres
```

Four details matter, and the application handles each of them for you:

- **Use the pooler, not the direct endpoint.** `db.PROJECTREF.supabase.co`
  publishes only an AAAA record — it is IPv6-only, and unreachable from an
  IPv4-only network. The pooler is IPv4 on every tier. Note the pooler username
  is `postgres.PROJECTREF`, not plain `postgres`.
- **Port 5432 is session mode; 6543 is transaction mode.** Session mode is the
  simpler default. If you do use 6543, the app detects the port and turns off
  both asyncpg's and SQLAlchemy's prepared-statement caches, because a
  transaction pooler multiplexes connections and would otherwise fail with
  `DuplicatePreparedStatement`.
- **TLS uses Supabase's own CA.** Supabase serves a certificate issued by
  `Supabase Root 2021 CA`, which is not in any public trust store, so verifying
  against certifi fails with `CERTIFICATE_VERIFY_FAILED`. That root is a public
  certificate and is vendored at `server/certs/supabase-prod-ca-2021.crt`;
  Supabase hosts use it automatically. `DATABASE_SSL_ROOT_CERT` overrides it if
  Supabase ever rotates the root.
- **`sslmode` and `channel_binding` are stripped** from the URL before it
  reaches asyncpg, which does not accept them. Verification still happens —
  this is `verify-full`, not a downgrade.

Set in `.env`:

```text
APP_ENV=local
AUTH_MODE=local
AUTO_CREATE_SCHEMA=false
DATABASE_URL=postgresql://postgres.PROJECTREF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres
```

Then apply the schema with `cd server && alembic upgrade head`.

### Private-alpha privacy and operations

The **Privacy & usage** workspace shows daily session quota, content-free usage
counts, active retention defaults, and whether provider cost telemetry is
available. A session can be deleted from its card after confirmation. Account
deletion requires typing `DELETE MY ACCOUNT`; repeated deletion calls are
idempotent and a PII-free terminal receipt prevents silent recreation.

Run configured retention cleanup with:

```bash
cd server && python scripts/run_retention.py
```

Operational references — retention and deletion, the browser and
accessibility matrix, the incident runbook, and the private-alpha exit
checklist — live under `.meta/docs/`, which is untracked and local-only.

## One-artifact production build

```bash
npm --prefix web run build
cd server && uvicorn api.main:app --host 127.0.0.1 --port 8000
```

FastAPI serves `web/dist` and all `/api` routes from the same origin, so there
is no CORS configuration and no second domain. Zerops builds this same
arrangement.

## Authentication

- `local`: the server supplies one explicit developer identity. No sign-in, no
  Clerk account, no tokens. This is the default and what CI runs.
- `clerk`: the browser sends a Clerk session token as a bearer token and the
  server verifies its RS256 signature, issuer, expiry, and authorized party
  before trusting the subject. Required in staging and production, alongside
  PostgreSQL and Alembic-owned schema.

### Clerk setup

1. Create a Clerk application. For a Zerops `*.zerops.app` URL use the
   **development** instance keys — a Clerk production instance requires a custom
   domain with a CNAME record, which a platform subdomain cannot satisfy.
2. **Add email and name to the session token.** Clerk's default session token
   contains neither, and the API rejects a token without an email. Under
   **Configure → Sessions → Customize session token**, set:

   ```json
   { "email": "{{user.primary_email_address}}", "name": "{{user.full_name}}" }
   ```

3. Set `CLERK_SECRET_KEY` and `CLERK_PUBLISHABLE_KEY`. Copy the PEM public key
   into `CLERK_JWT_KEY` so verification never makes a network call.
4. The web build reads `VITE_CLERK_PUBLISHABLE_KEY` at build time. Without it,
   the client renders no sign-in and expects `AUTH_MODE=local`.

The Content-Security-Policy adds Clerk's origins only when `AUTH_MODE=clerk`,
including `challenges.cloudflare.com` for Clerk's bot protection widget.

## Deployment (Zerops)

One-time setup:

```bash
zcli login
zcli project project-import zerops-import.yml
```

Then set the real secrets in the Zerops GUI under **Service → Environment
variables** — `zerops-import.yml` ships placeholders on purpose.

Repeat deploys run from [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml)
on every push to `develop`. It needs:

- Repository secret `ZEROPS_TOKEN` (Zerops → Settings → Access Token Management)
- Repository variables `ZEROPS_SERVICE_ID` and `APP_URL`

The workflow is skipped while `ZEROPS_SERVICE_ID` is unset, so it can land
before the project exists. Zerops runs the build itself from
[`zerops.yml`](zerops.yml): Vite compiles the client, `web/dist` and `server/`
are deployed under `/var/www`, `alembic upgrade head` runs on each container
start, and readiness is gated on `/api/health/ready`.

`server/requirements.txt` is what Zerops installs at runtime, because the
prepare container cannot see the build tree and so cannot `pip install .`. Keep
it in sync with `[project].dependencies` in `server/pyproject.toml`.

## Verification

Backend, from `server/`:

```bash
python -m ruff format --check api domain evals migrations prompts scripts tests
python -m ruff check api domain evals migrations prompts scripts tests
python -m pytest tests
```

Frontend:

```bash
npm --prefix web run format:check
npm --prefix web run lint
npm --prefix web run typecheck
npm --prefix web run test
npm --prefix web run build
```

CI repeats these checks on pull requests and on pushes to `main` and `develop`.

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
11. In voice mode, speak one non-sensitive answer such as "I built a FastAPI service backed by PostgreSQL". The candidate turn must appear once, first as live text and then replaced by the final transcript, ending with `transcription_source="final_model"`.

Voice-mode completion on current Chrome, Safari, and Edge, plus the documented
latency and impaired-network measurements, remain manual M3 release checks.
The dual-transcription degradation matrix in
`.meta/docs/PRIVATE_ALPHA_CHECKLIST.md` is also a manual check.
