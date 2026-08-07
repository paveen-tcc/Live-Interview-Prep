# Neon asyncpg/TLS investigation — 2026-08-07

## Debug report

- **Symptom:** The first Neon migration failed with
  `TypeError: connect() got an unexpected keyword argument 'sslmode'`. After
  correcting that boundary, TLS correctly failed closed because the macOS Python
  framework could not find the certificate issuer in its default CA store.
- **Root cause:** Neon supplies a libpq-oriented URL containing `sslmode` and
  `channel_binding`. SQLAlchemy forwards URL query options as asyncpg keyword
  arguments, but asyncpg accepts an `ssl` context rather than `sslmode` at that
  boundary. Separately, the local Python framework's default CA store was
  incomplete.
- **Fix:** Neon-only URL normalization removes the incompatible libpq query
  options. Both the application engine and Alembic now pass asyncpg a verified
  `SSLContext` backed by certifi's maintained CA bundle. TLS verification remains
  enabled, including certificate and hostname checks.
- **Evidence:** `alembic upgrade head` connected to Neon and applied migration
  `20260806_0001`. `/api/health/ready` returned database `ok`; the API created a
  `Neon connection verification` session; after stopping and restarting the API,
  `/api/interviews` returned the same session ID.
- **Regression test:** `tests/test_api.py::test_neon_url_is_safe_for_asyncpg`
  verifies URL adaptation and the verified SSL context.
- **Related:** Sandboxed DNS initially produced `socket.gaierror`; the same
  migration reached Neon when run with approved network access, confirming that
  error was environmental rather than application behavior.
- **Status:** DONE
