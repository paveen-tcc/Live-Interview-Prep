"""Run the configured privacy-retention policy once."""

from __future__ import annotations

import asyncio
import json

from api.config import get_settings
from api.database import create_database
from api.services.retention import run_retention


async def main() -> None:
    settings = get_settings()
    engine, session_factory = create_database(settings)
    try:
        async with session_factory() as database:
            result = await run_retention(database, settings=settings)
        print(json.dumps(result.__dict__, sort_keys=True))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
