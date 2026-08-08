from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from api.config import Settings
from api.database import create_database, create_schema
from api.models import (
    DeliveryCoachingRecord,
    InterviewSession,
    UsageEvent,
    User,
)
from api.services.retention import run_retention


@pytest.mark.asyncio
async def test_m6_retention_expires_stale_drafts_delivery_metrics_and_usage(
    tmp_path,
) -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        auth_mode="local",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'retention.db'}",
        auto_create_schema=True,
        transcript_retention_days=30,
        draft_retention_days=30,
        delivery_metrics_retention_days=7,
        usage_event_retention_days=90,
    )
    engine, session_factory = create_database(settings)
    await create_schema(engine)
    now = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)

    async with session_factory() as database:
        user = User(
            auth_subject="retention-user",
            email="retention@example.test",
            display_name="Retention User",
        )
        database.add(user)
        await database.flush()
        stale_draft = InterviewSession(
            user_id=user.id,
            title="Expired draft",
            status="DRAFT",
            created_at=now - timedelta(days=31),
        )
        retained_report = InterviewSession(
            user_id=user.id,
            title="Retained report",
            status="REPORT_READY",
            created_at=now - timedelta(days=12),
            ended_at=now - timedelta(days=10),
        )
        database.add_all([stale_draft, retained_report])
        await database.flush()
        database.add(
            DeliveryCoachingRecord(
                session_id=retained_report.id,
                consented=True,
                consent_version="delivery-v1",
                speech_observations=[{"turn_id": "turn-1"}],
                metrics=[{"turn_id": "turn-1"}],
                baseline={"turn_count": 2},
                observations=[{"turn_id": "turn-1"}],
                suggestions=["Pause before answering."],
            )
        )
        database.add_all(
            [
                UsageEvent(
                    user_id=user.id,
                    session_id=None,
                    kind="session_created",
                    created_at=now - timedelta(days=91),
                ),
                UsageEvent(
                    user_id=user.id,
                    session_id=retained_report.id,
                    kind="session_created",
                    created_at=now - timedelta(days=1),
                ),
            ]
        )
        await database.commit()

        result = await run_retention(database, settings=settings, now=now)

        assert result.deleted_sessions == 1
        assert result.deleted_delivery_records == 1
        assert result.deleted_usage_events == 1
        assert await database.get(InterviewSession, stale_draft.id) is None
        assert await database.get(InterviewSession, retained_report.id) is not None
        delivery = await database.scalar(
            select(DeliveryCoachingRecord).where(
                DeliveryCoachingRecord.session_id == retained_report.id
            )
        )
        assert delivery is not None
        assert delivery.consented is False
        assert delivery.deleted_at is not None
        assert delivery.deleted_at.replace(tzinfo=UTC) == now
        assert delivery.metrics == []
        usage = list(await database.scalars(select(UsageEvent)))
        assert len(usage) == 1

    await engine.dispose()
