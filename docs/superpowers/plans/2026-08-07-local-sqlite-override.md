# Local SQLite Override Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make local development use a fresh file-backed SQLite database through a reversible, ignored `.env.local` override while preserving the existing Neon configuration.

**Architecture:** Pydantic Settings will read the existing project `.env` followed by `.env.local`, allowing the local file to override only database-related values. The SQLite override enables automatic schema creation and writes to the already-ignored `data/` directory; removing `.env.local` restores Neon.

**Tech Stack:** Python 3.12, Pydantic Settings, FastAPI, SQLAlchemy asyncio, aiosqlite, pytest

## Global Constraints

- Existing Neon credentials and data must not be modified or copied.
- `.env.local` must never be committed.
- The local database must be `sqlite+aiosqlite:///./data/interview_coach.db`.
- `AUTO_CREATE_SCHEMA` must be `true` for the local SQLite override.
- Process environment variables retain higher priority than dotenv files.
- Cloudflare D1 remains a later persistence adapter; this change does not claim direct D1 compatibility.

---

### Task 1: Add and verify the reversible SQLite override

**Files:**
- Modify: `api/config.py:12-70`
- Modify: `.gitignore:1-20`
- Create locally but never commit: `.env.local`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `PROJECT_ROOT: pathlib.Path` and `Settings(BaseSettings)` from `api.config`.
- Produces: `ENV_FILES: tuple[Path, Path]`, ordered as `.env` then `.env.local`; `Settings.model_config["env_file"]` uses this tuple.

- [ ] **Step 1: Write the failing precedence test**

Add this test to `tests/test_api.py`:

```python
def test_settings_load_optional_local_env_after_base_env(tmp_path: Path) -> None:
    base_env = tmp_path / ".env"
    local_env = tmp_path / ".env.local"
    base_env.write_text(
        "DATABASE_URL=postgresql+asyncpg://user:password@database/app\n"
        "AUTO_CREATE_SCHEMA=false\n",
        encoding="utf-8",
    )
    local_env.write_text(
        "DATABASE_URL=sqlite+aiosqlite:///./data/interview_coach.db\n"
        "AUTO_CREATE_SCHEMA=true\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=(base_env, local_env))

    assert settings.database_url == "sqlite+aiosqlite:///./data/interview_coach.db"
    assert settings.auto_create_schema is True
    assert Settings.model_config["env_file"] == (
        PROJECT_ROOT / ".env",
        PROJECT_ROOT / ".env.local",
    )
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
./venv/bin/python -m pytest tests/test_api.py::test_settings_load_optional_local_env_after_base_env -q
```

Expected: FAIL because `Settings.model_config["env_file"]` currently contains only `PROJECT_ROOT / ".env"`.

- [ ] **Step 3: Implement dotenv precedence**

In `api/config.py`, define the ordered files and use them in `SettingsConfigDict`:

```python
ENV_FILES = (PROJECT_ROOT / ".env", PROJECT_ROOT / ".env.local")


class Settings(BaseSettings):
    # existing settings remain unchanged
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )
```

- [ ] **Step 4: Ignore and create the local override**

Add `.env.local` immediately after `.env` in `.gitignore`. Create `.env.local` with exactly:

```dotenv
DATABASE_URL=sqlite+aiosqlite:///./data/interview_coach.db
AUTO_CREATE_SCHEMA=true
```

Do not stage `.env.local`.

- [ ] **Step 5: Run focused and full verification**

Run:

```bash
./venv/bin/python -m pytest tests/test_api.py::test_settings_load_optional_local_env_after_base_env -q
./venv/bin/python -m ruff format --check api tests/test_api.py
./venv/bin/python -m ruff check api tests/test_api.py
./venv/bin/python -m pytest tests prototype/tests -q
git check-ignore .env.local
git diff --check
```

Expected: all tests and checks pass; `git check-ignore` prints `.env.local`.

- [ ] **Step 6: Verify the running local application**

Restart the FastAPI development server if its reloader does not restart automatically. Verify safe settings without printing credentials, then call the API:

```bash
./venv/bin/python -c "from api.config import Settings; s=Settings(); print(s.database_url, s.auto_create_schema)"
curl -sS http://127.0.0.1:8000/api/auth/me
curl -sS http://127.0.0.1:8000/api/interviews
```

Expected: settings report the SQLite URL and `True`; both endpoints return 200; `/api/interviews` returns an empty `items` list.

- [ ] **Step 7: Commit source and test changes**

Do not include `.env.local` in the commit:

```bash
git add api/config.py .gitignore tests/test_api.py
git commit -m "feat(config): add local SQLite override"
```
