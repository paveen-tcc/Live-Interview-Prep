# Local SQLite Override Design

Date: 2026-08-07
Status: Approved

## Goal

Use a fresh file-backed SQLite database for local development so dashboard and API testing do not depend on Neon network latency. Preserve the existing Neon credentials and data for possible later use.

## Design

Application settings will load `.env` first and an optional `.env.local` second. Values in `.env.local` override matching values from `.env`.

The ignored local override will contain:

```dotenv
DATABASE_URL=sqlite+aiosqlite:///./data/interview_coach.db
AUTO_CREATE_SCHEMA=true
```

The application will therefore create a fresh schema automatically in `data/interview_coach.db`. Existing Neon data will not be copied, modified, or deleted. Removing `.env.local` restores the previous `.env` configuration.

## Repository and security behavior

- `.env.local` will be excluded from Git alongside `.env`.
- `.env.example` remains the documented, shareable configuration template.
- No credentials are copied into the SQLite file or committed.
- Existing environment variables supplied by the process retain Pydantic Settings' normal highest priority.

## Verification

- A settings test proves `.env.local` overrides `.env`.
- The application starts with SQLite and creates the schema automatically.
- `/api/auth/me` and `/api/interviews` return successfully against the fresh database.
- The fresh dashboard contains zero sessions.
- Existing backend tests continue to pass.

## Cloudflare D1 boundary

D1 uses SQLite SQL semantics, but it is accessed through Cloudflare Worker bindings or its HTTP API rather than a local SQLite file or a normal SQLAlchemy database URL. A later deployment phase will introduce a D1-specific persistence adapter and migration workflow; this local override does not claim direct D1 compatibility.
