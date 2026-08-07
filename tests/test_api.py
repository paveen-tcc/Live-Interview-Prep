from __future__ import annotations

import base64
import io
import json
import sqlite3
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from docx import Document
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from api.config import PROJECT_ROOT, Settings, get_settings
from api.database import database_connect_args, normalized_database_url
from api.main import create_app
from api.services.realtime import RealtimeClientSecret
from domain.evaluation import (
    CompetencyEvaluation,
    EvaluationReport,
    EvidenceCitation,
)


def test_settings_reject_local_auth_and_sqlite_in_staging(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="staging",
            auth_mode="local",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'staging.db'}",
            auto_create_schema=False,
        )


def test_staging_accepts_managed_auth_with_postgresql() -> None:
    settings = Settings(
        app_env="staging",
        auth_mode="easy_auth",
        database_url="postgresql+asyncpg://user:password@database/app",
        auto_create_schema=False,
    )

    assert settings.auth_mode == "easy_auth"


def test_neon_url_is_safe_for_asyncpg() -> None:
    url = normalized_database_url(
        "postgresql://candidate:secret@ep-example.us-east-2.aws.neon.tech/"
        "interview_coach?sslmode=require&channel_binding=require"
    )

    assert url == (
        "postgresql+asyncpg://candidate:secret@"
        "ep-example.us-east-2.aws.neon.tech/interview_coach"
    )
    connect_args = database_connect_args(url, timeout_seconds=7.5)
    ssl_context = connect_args["ssl"]
    assert isinstance(ssl_context, ssl.SSLContext)
    assert ssl_context.check_hostname is True
    assert ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert connect_args["timeout"] == 7.5


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        auth_mode="local",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        auto_create_schema=True,
        web_dist_dir=tmp_path / "missing-dist",
        enable_text_dev_mode=False,
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _create_session(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/interviews", json={"title": "Untitled practice session"}
    )
    assert response.status_code == 201
    return response.json()


def _docx_bytes() -> bytes:
    document = Document()
    document.add_paragraph("Alex Morgan")
    document.add_paragraph("Skills")
    document.add_paragraph("Python, FastAPI, PostgreSQL")
    document.add_paragraph("Experience")
    document.add_paragraph(
        "Built payment APIs and reduced database query latency by 35%."
    )
    document.add_paragraph("Education")
    document.add_paragraph("BSc Computer Science")
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _ready_session(client: TestClient) -> dict[str, object]:
    interview = _create_session(client)
    upload = client.post(
        "/api/uploads/resume",
        data={"interview_id": interview["id"]},
        files={
            "file": (
                "alex.docx",
                _docx_bytes(),
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document",
            )
        },
    ).json()
    client.post(
        "/api/candidate-profiles/extract",
        json={"interview_id": interview["id"], "upload_id": upload["id"]},
    )
    target = client.post(
        "/api/job-targets",
        json={
            "interview_id": interview["id"],
            "title": "Backend Engineer",
            "seniority": "mid",
            "raw_description": (
                "Build reliable Python and FastAPI services with PostgreSQL, "
                "testing, observability, incident response, and team ownership."
            ),
        },
    ).json()
    client.post(
        "/api/scorecards/generate",
        json={"interview_id": interview["id"], "job_target_id": target["id"]},
    )
    return interview


def _pdf_bytes(*, text: str | None = None, encrypted: bool = False) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if text:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_reference = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
        )
        stream = DecodedStreamObject()
        safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("secret")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_health_reports_database_readiness(client: TestClient) -> None:
    health = client.get("/api/health")
    assert health.json() == {"status": "ok"}
    assert health.headers["X-Content-Type-Options"] == "nosniff"
    assert health.headers["Referrer-Policy"] == "no-referrer"
    assert "camera=()" in health.headers["Permissions-Policy"]
    assert "frame-ancestors 'none'" in health.headers["Content-Security-Policy"]
    assert client.get("/api/health/ready").json() == {
        "status": "ready",
        "database": "ok",
    }


def test_user_creates_session_and_sees_it_after_refresh(client: TestClient) -> None:
    user = client.get("/api/auth/me")
    assert user.status_code == 200
    assert user.json()["email"] == "developer@local.test"

    created = client.post(
        "/api/interviews",
        json={"title": "Untitled practice session"},
    )
    assert created.status_code == 201
    assert created.json()["status"] == "DRAFT"

    refreshed = client.get("/api/interviews")
    assert refreshed.status_code == 200
    assert [item["id"] for item in refreshed.json()["items"]] == [created.json()["id"]]


def test_m2_resume_profile_jd_and_editable_scorecard_flow(client: TestClient) -> None:
    interview = _create_session(client)
    uploaded = client.post(
        "/api/uploads/resume",
        data={"interview_id": interview["id"]},
        files={
            "file": (
                "alex-morgan.docx",
                _docx_bytes(),
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document",
            )
        },
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["file_type"] == "docx"
    assert uploaded.json()["raw_deleted_at"]

    profile = client.post(
        "/api/candidate-profiles/extract",
        json={
            "interview_id": interview["id"],
            "upload_id": uploaded.json()["id"],
        },
    )
    assert profile.status_code == 201
    profile_body = profile.json()
    assert profile_body["headline"] == "Alex Morgan"
    assert profile_body["claims"]
    assert profile_body["claims"][0]["source"]["source_id"].startswith("resume:block:")

    corrected_claims = [
        {"id": claim["id"], "text": claim["text"]} for claim in profile_body["claims"]
    ]
    corrected_claims[0]["text"] = "Python, FastAPI, PostgreSQL, and Redis"
    corrected = client.patch(
        f"/api/candidate-profiles/{profile_body['id']}",
        json={"headline": "Backend engineer", "claims": corrected_claims},
    )
    assert corrected.status_code == 200
    assert corrected.json()["claims"][0]["edited"] is True
    assert (
        corrected.json()["claims"][0]["original_text"]
        == (profile_body["claims"][0]["text"])
    )

    protected = client.post(
        "/api/candidate-profiles/extract",
        json={
            "interview_id": interview["id"],
            "upload_id": uploaded.json()["id"],
            "replace_existing": True,
        },
    )
    assert protected.status_code == 409
    assert "saved corrections" in protected.json()["error"]["message"]

    extracted_again = client.post(
        "/api/candidate-profiles/extract",
        json={
            "interview_id": interview["id"],
            "upload_id": uploaded.json()["id"],
        },
    )
    assert extracted_again.json()["headline"] == "Backend engineer"
    assert extracted_again.json()["claims"][0]["text"].endswith("and Redis")

    description = (
        "Senior Backend Engineer\n"
        "Python and FastAPI are required for production API development.\n"
        "Strong PostgreSQL and database design experience is essential.\n"
        "AWS or another cloud platform is preferred.\n"
        "Own testing, observability, incidents, and cross-team delivery."
    )
    target = client.post(
        "/api/job-targets",
        json={
            "interview_id": interview["id"],
            "title": "Senior Backend Engineer",
            "seniority": "senior",
            "raw_description": description,
        },
    )
    assert target.status_code == 201
    assert target.json()["structured_requirements"]

    scorecard = client.post(
        "/api/scorecards/generate",
        json={
            "interview_id": interview["id"],
            "job_target_id": target.json()["id"],
        },
    )
    assert scorecard.status_code == 201
    scorecard_body = scorecard.json()
    assert sum(item["weight"] for item in scorecard_body["competencies"]) == 100
    assert all(item["source_references"] for item in scorecard_body["competencies"])
    assert (
        "Shapes system boundaries"
        in scorecard_body["competencies"][0]["seniority_expectation"]
    )

    edits = []
    for competency in scorecard_body["competencies"]:
        edits.append(
            {
                key: competency[key]
                for key in (
                    "id",
                    "name",
                    "description",
                    "weight",
                    "classification",
                    "seniority_expectation",
                    "evidence_to_collect",
                    "question_families",
                )
            }
        )
    edits[0]["name"] = "Backend API design"
    saved_scorecard = client.patch(
        f"/api/scorecards/{scorecard_body['id']}", json={"competencies": edits}
    )
    assert saved_scorecard.status_code == 200
    assert saved_scorecard.json()["version"] == 2
    assert saved_scorecard.json()["competencies"][0]["name"] == "Backend API design"

    setup = client.get(f"/api/interviews/{interview['id']}/setup")
    assert setup.status_code == 200
    assert setup.json()["profile"]["headline"] == "Backend engineer"
    assert setup.json()["scorecard"]["total_weight"] == 100

    refreshed_session = client.get(f"/api/interviews/{interview['id']}")
    assert refreshed_session.json()["status"] == "SCORECARD_READY"
    assert refreshed_session.json()["profile_id"] == profile_body["id"]


@pytest.mark.parametrize(
    ("filename", "media_type", "contents", "expected_status", "message"),
    [
        (
            "spoofed.pdf",
            "application/pdf",
            b"not really a PDF",
            415,
            "does not contain a PDF signature",
        ),
        (
            "resume.docx",
            "application/pdf",
            _docx_bytes(),
            415,
            "content type",
        ),
        (
            "corrupt.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"PKcorrupt",
            422,
            "corrupt",
        ),
        (
            "locked.pdf",
            "application/pdf",
            _pdf_bytes(text="Secret resume", encrypted=True),
            422,
            "Encrypted",
        ),
        (
            "scanned.pdf",
            "application/pdf",
            _pdf_bytes(),
            422,
            "No selectable text",
        ),
        (
            "oversized.pdf",
            "application/pdf",
            b"%PDF-" + b"x" * 5_000_000,
            413,
            "5 MB limit",
        ),
    ],
)
def test_resume_upload_rejects_unsafe_or_unsupported_files(
    client: TestClient,
    filename: str,
    media_type: str,
    contents: bytes,
    expected_status: int,
    message: str,
) -> None:
    interview = _create_session(client)
    response = client.post(
        "/api/uploads/resume",
        data={"interview_id": interview["id"]},
        files={"file": (filename, contents, media_type)},
    )

    assert response.status_code == expected_status
    assert message in response.json()["error"]["message"]


def test_text_pdf_upload_succeeds_with_page_source(client: TestClient) -> None:
    interview = _create_session(client)
    response = client.post(
        "/api/uploads/resume",
        data={"interview_id": interview["id"]},
        files={
            "file": (
                "resume.pdf",
                _pdf_bytes(text="Alex Morgan Backend Engineer"),
                "application/pdf",
            )
        },
    )
    assert response.status_code == 201
    profile = client.post(
        "/api/candidate-profiles/extract",
        json={
            "interview_id": interview["id"],
            "upload_id": response.json()["id"],
        },
    )
    assert profile.status_code == 201
    assert profile.json()["headline"] == "Alex Morgan Backend Engineer"


def test_scorecard_rejects_weights_that_do_not_total_one_hundred(
    client: TestClient,
) -> None:
    interview = _create_session(client)
    target = client.post(
        "/api/job-targets",
        json={
            "interview_id": interview["id"],
            "title": "Backend Engineer",
            "seniority": "mid",
            "raw_description": (
                "Backend engineer required to design APIs, use SQL databases, "
                "test services, debug incidents, and collaborate with a team."
            ),
        },
    ).json()
    scorecard = client.post(
        "/api/scorecards/generate",
        json={"interview_id": interview["id"], "job_target_id": target["id"]},
    ).json()
    edits = []
    for competency in scorecard["competencies"]:
        edit = {
            key: competency[key]
            for key in (
                "id",
                "name",
                "description",
                "weight",
                "classification",
                "seniority_expectation",
                "evidence_to_collect",
                "question_families",
            )
        }
        edits.append(edit)
    edits[0]["weight"] += 1

    response = client.patch(
        f"/api/scorecards/{scorecard['id']}", json={"competencies": edits}
    )
    assert response.status_code == 422


def test_text_dev_mode_is_rejected_when_server_flag_is_off(client: TestClient) -> None:
    interview = _create_session(client)
    response = client.post(
        f"/api/interviews/{interview['id']}/realtime-client-secret",
        json={
            "input_mode": "text_dev",
            "duration_minutes": 15,
            "interview_type": "technical_behavioral",
        },
    )

    assert response.status_code == 403
    assert "disabled by the server" in response.json()["error"]["message"]


def test_m3_text_realtime_flow_is_private_long_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    realtime_settings = Settings(
        app_env="test",
        auth_mode="local",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'm3.db'}",
        auto_create_schema=True,
        web_dist_dir=tmp_path / "missing-dist",
        enable_text_dev_mode=True,
        azure_openai_endpoint="https://example.services.ai.azure.com",
        azure_openai_api_key="permanent-server-key",
        azure_openai_realtime_deployment="realtime-deployment",
    )
    captured: dict[str, object] = {}

    async def fake_secret(**kwargs):
        captured.update(kwargs)
        return RealtimeClientSecret(
            value="ek_temporary",
            expires_at=1_786_000_000,
            calls_url=(
                "https://example.services.ai.azure.com/openai/v1/"
                "realtime/calls?webrtcfilter=on"
            ),
        )

    monkeypatch.setattr(
        "api.routes.realtime.create_realtime_client_secret", fake_secret
    )
    with TestClient(create_app(realtime_settings)) as realtime_client:
        interview = _ready_session(realtime_client)
        capabilities = realtime_client.get("/api/capabilities").json()
        assert capabilities == {
            "text_dev_mode_enabled": True,
            "realtime_configured": True,
            "typed_answer_max_characters": 20_000,
            "supported_durations": [15, 30, 45, 60],
        }

        secret = realtime_client.post(
            f"/api/interviews/{interview['id']}/realtime-client-secret",
            json={
                "input_mode": "text_dev",
                "duration_minutes": 15,
                "interview_type": "technical_behavioral",
            },
        )
        assert secret.status_code == 200
        assert set(secret.json()) == {
            "client_secret",
            "expires_at",
            "calls_url",
            "input_mode",
            "prompt_version",
        }
        assert "permanent-server-key" not in secret.text
        assert "server-owned" not in secret.text
        assert captured["input_mode"] == "text_dev"
        assert "TRUSTED_SESSION_CONTEXT_JSON" in captured["instructions"]

        connected = realtime_client.post(
            f"/api/interviews/{interview['id']}/connection-state",
            json={"state": "connected"},
        )
        assert connected.status_code == 200
        assert connected.json()["status"] == "IN_PROGRESS"
        assert connected.json()["started_at"]
        assert connected.json()["ends_at"]

        long_answer = "🙂" * 20_000
        pending_payload = {
            "items": [
                {
                    "client_turn_id": "item_long_answer",
                    "speaker": "user",
                    "transcript": long_answer,
                    "delivery_status": "pending",
                }
            ]
        }
        pending = realtime_client.post(
            f"/api/interviews/{interview['id']}/turns:batch",
            json=pending_payload,
        )
        assert pending.status_code == 200
        assert len(pending.json()["turns"]) == 1
        assert pending.json()["turns"][0]["delivery_status"] == "pending"

        pending_payload["items"][0]["delivery_status"] = "acknowledged"
        acknowledged = realtime_client.post(
            f"/api/interviews/{interview['id']}/turns:batch",
            json=pending_payload,
        )
        repeated = realtime_client.post(
            f"/api/interviews/{interview['id']}/turns:batch",
            json=pending_payload,
        )
        assert acknowledged.status_code == repeated.status_code == 200
        assert len(repeated.json()["turns"]) == 1
        assert repeated.json()["turns"][0]["delivery_status"] == "acknowledged"

        conflicting = pending_payload.copy()
        conflicting["items"] = [
            {**pending_payload["items"][0], "transcript": "different"}
        ]
        assert (
            realtime_client.post(
                f"/api/interviews/{interview['id']}/turns:batch",
                json=conflicting,
            ).status_code
            == 409
        )

        completed = realtime_client.post(f"/api/interviews/{interview['id']}/complete")
        completed_again = realtime_client.post(
            f"/api/interviews/{interview['id']}/complete"
        )
        assert completed.json()["status"] == "TRANSCRIPT_FINALIZING"
        assert completed_again.json()["started_at"] == completed.json()["started_at"]


def test_m3_reconnect_rejects_an_expired_recovery_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    realtime_settings = Settings(
        _env_file=None,
        app_env="test",
        auth_mode="local",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'm3-reconnect.db'}",
        auto_create_schema=True,
        web_dist_dir=tmp_path / "missing-dist",
        enable_text_dev_mode=True,
        realtime_reconnect_window_seconds=-1,
        azure_openai_endpoint="https://example.services.ai.azure.com",
        azure_openai_api_key="permanent-server-key",
        azure_openai_realtime_deployment="realtime-deployment",
    )

    async def fake_secret(**_kwargs):
        return RealtimeClientSecret(
            value="ek_temporary",
            expires_at=1_786_000_000,
            calls_url=(
                "https://example.services.ai.azure.com/openai/v1/"
                "realtime/calls?webrtcfilter=on"
            ),
        )

    monkeypatch.setattr(
        "api.routes.realtime.create_realtime_client_secret", fake_secret
    )
    with TestClient(create_app(realtime_settings)) as realtime_client:
        interview = _ready_session(realtime_client)
        request_payload = {
            "input_mode": "text_dev",
            "duration_minutes": 15,
            "interview_type": "technical_behavioral",
        }
        assert (
            realtime_client.post(
                f"/api/interviews/{interview['id']}/realtime-client-secret",
                json=request_payload,
            ).status_code
            == 200
        )
        realtime_client.post(
            f"/api/interviews/{interview['id']}/connection-state",
            json={"state": "connected"},
        )
        realtime_client.post(
            f"/api/interviews/{interview['id']}/connection-state",
            json={"state": "reconnecting"},
        )

        expired = realtime_client.post(
            f"/api/interviews/{interview['id']}/realtime-client-secret",
            json=request_payload,
        )
        assert expired.status_code == 409
        assert "recovery window has expired" in expired.json()["error"]["message"]


def test_m3_rejects_invalid_state_transitions(client: TestClient) -> None:
    interview = _create_session(client)

    connected = client.post(
        f"/api/interviews/{interview['id']}/connection-state",
        json={"state": "connected"},
    )
    completed = client.post(f"/api/interviews/{interview['id']}/complete")

    assert connected.status_code == 409
    assert completed.status_code == 409


def test_m3_timer_is_server_authoritative_and_utc_on_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "m3-timer.db"
    realtime_settings = Settings(
        _env_file=None,
        app_env="test",
        auth_mode="local",
        database_url=f"sqlite+aiosqlite:///{database_path}",
        auto_create_schema=True,
        web_dist_dir=tmp_path / "missing-dist",
        enable_text_dev_mode=True,
        azure_openai_endpoint="https://example.services.ai.azure.com",
        azure_openai_api_key="server-key",
        azure_openai_realtime_deployment="realtime-deployment",
    )

    async def fake_secret(**_kwargs):
        return RealtimeClientSecret(
            value="ek_temporary",
            expires_at=2_000_000_000,
            calls_url="https://example.services.ai.azure.com/openai/v1/realtime/calls",
        )

    monkeypatch.setattr(
        "api.routes.realtime.create_realtime_client_secret", fake_secret
    )
    with TestClient(create_app(realtime_settings)) as realtime_client:
        interview = _ready_session(realtime_client)
        secret_payload = {
            "input_mode": "text_dev",
            "duration_minutes": 15,
            "interview_type": "technical_behavioral",
        }
        assert (
            realtime_client.post(
                f"/api/interviews/{interview['id']}/realtime-client-secret",
                json=secret_payload,
            ).status_code
            == 200
        )
        connected = realtime_client.post(
            f"/api/interviews/{interview['id']}/connection-state",
            json={"state": "connected"},
        )
        assert connected.json()["started_at"].endswith("Z")

        refreshed = realtime_client.get(f"/api/interviews/{interview['id']}/runtime")
        assert refreshed.json()["started_at"].endswith("Z")
        assert refreshed.json()["ends_at"].endswith("Z")

        expired_start = datetime.now(UTC) - timedelta(minutes=16)
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "UPDATE interview_sessions SET started_at = ? WHERE id = ?",
                (expired_start.replace(tzinfo=None), interview["id"]),
            )
            connection.commit()

        expired_runtime = realtime_client.get(
            f"/api/interviews/{interview['id']}/runtime"
        )
        assert expired_runtime.json()["status"] == "TRANSCRIPT_FINALIZING"
        assert (
            realtime_client.post(
                f"/api/interviews/{interview['id']}/realtime-client-secret",
                json=secret_payload,
            ).status_code
            == 409
        )
        assert (
            realtime_client.post(
                f"/api/interviews/{interview['id']}/turns:batch",
                json={
                    "items": [
                        {
                            "client_turn_id": "late_turn",
                            "speaker": "user",
                            "transcript": "This arrived after the server deadline.",
                        }
                    ]
                },
            ).status_code
            == 409
        )


def test_m3_start_is_idempotent_and_setup_freezes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    realtime_settings = Settings(
        _env_file=None,
        app_env="test",
        auth_mode="local",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'm3-freeze.db'}",
        auto_create_schema=True,
        web_dist_dir=tmp_path / "missing-dist",
        enable_text_dev_mode=True,
        azure_openai_endpoint="https://example.services.ai.azure.com",
        azure_openai_api_key="server-key",
        azure_openai_realtime_deployment="realtime-deployment",
    )
    calls = 0

    async def fake_secret(**_kwargs):
        nonlocal calls
        calls += 1
        return RealtimeClientSecret(
            value="ek_same_attempt",
            expires_at=2_000_000_000,
            calls_url="https://example.services.ai.azure.com/openai/v1/realtime/calls",
        )

    monkeypatch.setattr(
        "api.routes.realtime.create_realtime_client_secret", fake_secret
    )
    with TestClient(create_app(realtime_settings)) as realtime_client:
        interview = _ready_session(realtime_client)
        setup = realtime_client.get(f"/api/interviews/{interview['id']}/setup").json()
        secret_payload = {
            "input_mode": "text_dev",
            "duration_minutes": 15,
            "interview_type": "technical_behavioral",
        }
        first = realtime_client.post(
            f"/api/interviews/{interview['id']}/realtime-client-secret",
            json=secret_payload,
        )
        repeated = realtime_client.post(
            f"/api/interviews/{interview['id']}/realtime-client-secret",
            json=secret_payload,
        )
        assert first.json() == repeated.json()
        assert calls == 1

        profile = setup["profile"]
        profile_edit = {
            "headline": profile["headline"],
            "claims": [
                {"id": claim["id"], "text": claim["text"]}
                for claim in profile["claims"]
            ],
        }
        scorecard = setup["scorecard"]
        scorecard_edit = {
            "competencies": [
                {
                    key: competency[key]
                    for key in (
                        "id",
                        "name",
                        "description",
                        "weight",
                        "classification",
                        "seniority_expectation",
                        "evidence_to_collect",
                        "question_families",
                    )
                }
                for competency in scorecard["competencies"]
            ]
        }
        assert (
            realtime_client.patch(
                f"/api/candidate-profiles/{profile['id']}", json=profile_edit
            ).status_code
            == 409
        )
        assert (
            realtime_client.patch(
                f"/api/scorecards/{scorecard['id']}", json=scorecard_edit
            ).status_code
            == 409
        )


def test_database_engine_hides_private_parameters(client: TestClient) -> None:
    assert client.app.state.engine.sync_engine.hide_parameters is True


def _principal_headers(subject: str, email: str) -> dict[str, str]:
    principal = {
        "claims": [
            {"typ": "oid", "val": subject},
            {"typ": "preferred_username", "val": email},
            {"typ": "name", "val": email.split("@", 1)[0]},
        ]
    }
    encoded = base64.b64encode(json.dumps(principal).encode()).decode()
    return {"X-MS-CLIENT-PRINCIPAL": encoded}


def test_managed_auth_accepts_external_id_email_claim(tmp_path: Path) -> None:
    easy_auth_settings = Settings(
        app_env="test",
        auth_mode="easy_auth",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'external-id.db'}",
        auto_create_schema=True,
        web_dist_dir=tmp_path / "missing-dist",
    )
    principal = {
        "claims": [
            {"typ": "sub", "val": "external-user"},
            {"typ": "emails", "val": '["candidate@example.test"]'},
            {"typ": "name", "val": "Candidate"},
        ]
    }
    headers = {
        "X-MS-CLIENT-PRINCIPAL": base64.b64encode(
            json.dumps(principal).encode()
        ).decode()
    }

    with TestClient(create_app(easy_auth_settings)) as easy_auth_client:
        response = easy_auth_client.get("/api/auth/me", headers=headers)

    assert response.status_code == 200
    assert response.json()["email"] == "candidate@example.test"
    assert response.json()["display_name"] == "Candidate"


def test_managed_users_cannot_see_each_others_sessions(tmp_path: Path) -> None:
    easy_auth_settings = Settings(
        app_env="test",
        auth_mode="easy_auth",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'ownership.db'}",
        auto_create_schema=True,
        web_dist_dir=tmp_path / "missing-dist",
    )
    with TestClient(create_app(easy_auth_settings)) as easy_auth_client:
        first_user = _principal_headers("user-a", "a@example.test")
        second_user = _principal_headers("user-b", "b@example.test")
        assert (
            easy_auth_client.post(
                "/api/interviews",
                headers=first_user,
                json={"title": "Private session"},
            ).status_code
            == 201
        )

        second_user_sessions = easy_auth_client.get(
            "/api/interviews", headers=second_user
        )
        assert second_user_sessions.json() == {"items": []}


def test_unauthenticated_managed_request_has_error_id(tmp_path: Path) -> None:
    easy_auth_settings = Settings(
        app_env="test",
        auth_mode="easy_auth",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}",
        auto_create_schema=True,
        web_dist_dir=tmp_path / "missing-dist",
    )
    with TestClient(create_app(easy_auth_settings)) as easy_auth_client:
        response = easy_auth_client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.headers["X-Error-ID"] == response.json()["error"]["id"]


def test_validation_errors_have_safe_error_ids(client: TestClient) -> None:
    response = client.post("/api/interviews", json={"title": "   "})

    assert response.status_code == 422
    assert response.headers["X-Error-ID"] == response.json()["error"]["id"]
    assert response.json()["error"]["message"] == (
        "The request did not pass validation."
    )


def test_react_build_is_served_from_fastapi(tmp_path: Path) -> None:
    dist_directory = PROJECT_ROOT / "web" / "dist"
    assert (dist_directory / "index.html").is_file(), "Run npm run build first."
    static_settings = Settings(
        app_env="test",
        auth_mode="local",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'static.db'}",
        auto_create_schema=True,
        web_dist_dir=dist_directory,
    )

    with TestClient(create_app(static_settings)) as static_client:
        response = static_client.get("/practice/example")

    assert response.status_code == 200
    assert "<title>AI Interview Coach</title>" in response.text


def test_m4_completion_generates_one_evidence_backed_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluation_settings = Settings(
        _env_file=None,
        app_env="test",
        auth_mode="local",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'm4.db'}",
        auto_create_schema=True,
        web_dist_dir=tmp_path / "missing-dist",
        enable_text_dev_mode=True,
        azure_openai_endpoint="https://example.services.ai.azure.com",
        azure_openai_api_key="permanent-server-key",
        azure_openai_realtime_deployment="realtime-deployment",
        azure_openai_text_deployment="text-deployment",
    )
    model_calls = 0

    async def fake_secret(**_kwargs):
        return RealtimeClientSecret(
            value="ek_temporary",
            expires_at=1_786_000_000,
            calls_url="https://example.invalid/openai/v1/realtime/calls",
        )

    async def fake_evaluation(**kwargs):
        nonlocal model_calls
        model_calls += 1
        scorecard = kwargs["scorecard"]
        turns = kwargs["turns"]
        candidate_turn = next(turn for turn in turns if turn.speaker == "user")
        results = [
            CompetencyEvaluation(
                competency_id=competency.id,
                assessment="scored",
                score=4,
                rating_confidence="high",
                evidence=[
                    EvidenceCitation(
                        turn_id=candidate_turn.id,
                        quote="idempotency keys",
                    )
                ],
                evidence_summary="The candidate described a concrete safeguard.",
                gaps=[],
                recommendations=["Compare storage and retry trade-offs."],
            )
            for competency in scorecard.competencies
        ]
        return EvaluationReport(
            evaluator_version="test-evaluator-v1",
            competency_results=results,
            overall_score=4,
            assessed_weight=100,
            total_weight=100,
            coverage_percentage=100,
            strength_competency_ids=[results[0].competency_id],
            gap_competency_ids=[],
            practice_exercises=[],
            evidence_locations=[],
            validation_attempts=1,
        )

    monkeypatch.setattr(
        "api.routes.realtime.create_realtime_client_secret", fake_secret
    )
    monkeypatch.setattr(
        "api.services.evaluation_jobs.evaluate_transcript", fake_evaluation
    )
    with TestClient(create_app(evaluation_settings)) as evaluation_client:
        interview = _ready_session(evaluation_client)
        secret_payload = {
            "input_mode": "text_dev",
            "duration_minutes": 15,
            "interview_type": "technical_behavioral",
        }
        assert (
            evaluation_client.post(
                f"/api/interviews/{interview['id']}/realtime-client-secret",
                json=secret_payload,
            ).status_code
            == 200
        )
        assert (
            evaluation_client.post(
                f"/api/interviews/{interview['id']}/connection-state",
                json={"state": "connected"},
            ).status_code
            == 200
        )
        turn = evaluation_client.post(
            f"/api/interviews/{interview['id']}/turns:batch",
            json={
                "items": [
                    {
                        "client_turn_id": "candidate-answer-1",
                        "speaker": "user",
                        "transcript": (
                            "I used idempotency keys and a unique database "
                            "constraint to make retries safe."
                        ),
                        "delivery_status": "acknowledged",
                    }
                ]
            },
        )
        assert turn.status_code == 200

        first = evaluation_client.post(f"/api/interviews/{interview['id']}/complete")
        repeated = evaluation_client.post(f"/api/interviews/{interview['id']}/complete")
        report = evaluation_client.get(f"/api/interviews/{interview['id']}/report")

    assert first.status_code == repeated.status_code == report.status_code == 200
    assert model_calls == 1
    assert report.json()["status"] == "REPORT_READY"
    assert report.json()["overall_score"] == 4
    assert report.json()["coverage_percentage"] == 100
    assert report.json()["delivery_coaching"]["status"] == "unavailable"
    assert report.json()["delivery_coaching"]["unavailable_reason"] == "text_input_mode"
    assert all(item["evidence"] for item in report.json()["competency_results"])
    assert all(
        item["evidence"][0]["quote"] == "idempotency keys"
        for item in report.json()["competency_results"]
    )


def test_m5_delivery_consent_metrics_disable_and_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery_settings = Settings(
        _env_file=None,
        app_env="test",
        auth_mode="local",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'm5.db'}",
        auto_create_schema=True,
        web_dist_dir=tmp_path / "missing-dist",
        azure_openai_endpoint="https://example.services.ai.azure.com",
        azure_openai_api_key="permanent-server-key",
        azure_openai_realtime_deployment="realtime-deployment",
    )

    async def fake_secret(**_kwargs):
        return RealtimeClientSecret(
            value="ek_temporary",
            expires_at=1_786_000_000,
            calls_url="https://example.invalid/openai/v1/realtime/calls",
        )

    monkeypatch.setattr(
        "api.routes.realtime.create_realtime_client_secret", fake_secret
    )
    with TestClient(create_app(delivery_settings)) as delivery_client:
        interview = _ready_session(delivery_client)
        consent = delivery_client.post(
            f"/api/interviews/{interview['id']}/delivery-consent",
            json={"enabled": True, "consent_version": "delivery-v1"},
        )
        assert consent.status_code == 200
        assert consent.json()["status"] == "collecting"
        delivery_client.post(
            f"/api/interviews/{interview['id']}/realtime-client-secret",
            json={
                "input_mode": "voice",
                "duration_minutes": 15,
                "interview_type": "technical_behavioral",
            },
        )
        delivery_client.post(
            f"/api/interviews/{interview['id']}/connection-state",
            json={"state": "connected"},
        )
        turns = delivery_client.post(
            f"/api/interviews/{interview['id']}/turns:batch",
            json={
                "items": [
                    {
                        "client_turn_id": "assistant-question",
                        "speaker": "assistant",
                        "transcript": "Describe an API you designed.",
                        "delivery_status": "acknowledged",
                        "started_at": "2026-08-07T10:00:00Z",
                        "ended_at": "2026-08-07T10:00:03Z",
                    },
                    {
                        "client_turn_id": "candidate-answer",
                        "speaker": "user",
                        "transcript": (
                            "Um I designed a payment API with idempotency keys "
                            "and clear transaction boundaries."
                        ),
                        "delivery_status": "acknowledged",
                        "started_at": "2026-08-07T10:00:04Z",
                        "ended_at": "2026-08-07T10:00:10Z",
                    },
                ]
            },
        ).json()["turns"]
        candidate_turn = next(item for item in turns if item["speaker"] == "user")
        observed = delivery_client.post(
            f"/api/interviews/{interview['id']}/delivery-observations",
            json={
                "items": [
                    {
                        "turn_id": candidate_turn["id"],
                        "speech_segments": [
                            {
                                "started_at": "2026-08-07T10:00:04Z",
                                "ended_at": "2026-08-07T10:00:06Z",
                            },
                            {
                                "started_at": "2026-08-07T10:00:07Z",
                                "ended_at": "2026-08-07T10:00:10Z",
                            },
                        ],
                    }
                ]
            },
        )
        assert observed.status_code == 200
        assert observed.json()["status"] == "available"
        assert observed.json()["metrics"][0]["pause_count"] == 1
        assert observed.json()["metrics"][0]["filler_count"] == 1

        disabled = delivery_client.post(
            f"/api/interviews/{interview['id']}/delivery-consent",
            json={"enabled": False, "consent_version": "delivery-v1"},
        )
        assert disabled.json()["status"] == "disabled"
        assert disabled.json()["metrics"]
        deleted = delivery_client.delete(
            f"/api/interviews/{interview['id']}/delivery-metrics"
        )
        assert deleted.json()["status"] == "deleted"
        assert deleted.json()["metrics"] == []


def test_migrations_upgrade_and_roll_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    get_settings.cache_clear()
    configuration = Config(PROJECT_ROOT / "alembic.ini")

    command.upgrade(configuration, "head")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "users",
        "interview_sessions",
        "uploads",
        "candidate_profiles",
        "job_targets",
        "scorecards",
        "interview_turns",
        "evaluations",
        "delivery_coaching",
        "usage_events",
        "deletion_receipts",
        "alembic_version",
    } <= tables

    command.downgrade(configuration, "base")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "users" not in tables
    assert "interview_sessions" not in tables
    assert "uploads" not in tables
    assert "candidate_profiles" not in tables
    assert "job_targets" not in tables
    assert "scorecards" not in tables
    assert "interview_turns" not in tables
    assert "evaluations" not in tables
    assert "delivery_coaching" not in tables
    assert "usage_events" not in tables
    assert "deletion_receipts" not in tables
    get_settings.cache_clear()


def test_m6_daily_session_quota_and_usage_summary(tmp_path: Path) -> None:
    quota_settings = Settings(
        _env_file=None,
        app_env="test",
        auth_mode="local",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'quota.db'}",
        auto_create_schema=True,
        web_dist_dir=tmp_path / "missing-dist",
        daily_interview_quota=2,
    )

    with TestClient(create_app(quota_settings)) as quota_client:
        assert _create_session(quota_client)["id"]
        assert _create_session(quota_client)["id"]

        blocked = quota_client.post("/api/interviews", json={"title": "Over quota"})
        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"]

        usage = quota_client.get("/api/operations/usage")
        assert usage.status_code == 200
        assert usage.json()["daily_interview_quota"] == 2
        assert usage.json()["daily_interviews_used"] == 2
        assert usage.json()["events"]["session_created"] == 2
        assert usage.json()["estimated_cost_usd"] == "0.000000"


def test_m6_session_deletion_is_idempotent_and_removes_private_data(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "delete-session.db"
    deletion_settings = Settings(
        _env_file=None,
        app_env="test",
        auth_mode="local",
        database_url=f"sqlite+aiosqlite:///{database_path}",
        auto_create_schema=True,
        web_dist_dir=tmp_path / "missing-dist",
    )

    with TestClient(create_app(deletion_settings)) as deletion_client:
        interview = _ready_session(deletion_client)
        response = deletion_client.request(
            "DELETE",
            f"/api/interviews/{interview['id']}",
            json={"confirmation": "DELETE"},
        )
        repeated = deletion_client.request(
            "DELETE",
            f"/api/interviews/{interview['id']}",
            json={"confirmation": "DELETE"},
        )

        assert response.status_code == repeated.status_code == 204
        assert deletion_client.get("/api/interviews").json() == {"items": []}

    with sqlite3.connect(database_path) as connection:
        for table in (
            "interview_sessions",
            "interview_turns",
            "evaluations",
            "delivery_coaching",
            "candidate_profiles",
            "uploads",
            "scorecards",
            "job_targets",
        ):
            assert (
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            )
        receipt = connection.execute(
            "SELECT kind, status FROM deletion_receipts"
        ).fetchone()
        assert receipt == ("session", "completed")


def test_m6_account_deletion_is_idempotent_and_blocks_recreation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "delete-account.db"
    deletion_settings = Settings(
        _env_file=None,
        app_env="test",
        auth_mode="local",
        database_url=f"sqlite+aiosqlite:///{database_path}",
        auto_create_schema=True,
        web_dist_dir=tmp_path / "missing-dist",
    )

    with TestClient(create_app(deletion_settings)) as deletion_client:
        _ready_session(deletion_client)
        response = deletion_client.request(
            "DELETE",
            "/api/account",
            json={"confirmation": "DELETE MY ACCOUNT"},
        )
        repeated = deletion_client.request(
            "DELETE",
            "/api/account",
            json={"confirmation": "DELETE MY ACCOUNT"},
        )

        assert response.status_code == repeated.status_code == 204
        assert deletion_client.get("/api/auth/me").status_code == 410

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        receipt = connection.execute(
            "SELECT kind, status FROM deletion_receipts WHERE kind = 'account'"
        ).fetchone()
        assert receipt == ("account", "completed")
