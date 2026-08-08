from __future__ import annotations

import importlib
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType

import httpx
import pytest


def _parser_module() -> ModuleType:
    try:
        module = importlib.import_module("api.services.audio_multipart")
    except ModuleNotFoundError:
        module = None
    assert module is not None, "bounded in-memory audio multipart parser is missing"
    return module


def _multipart_body(
    *, media_type: str, audio: bytes, started_at: str = "2026-08-08T10:00:00Z"
) -> tuple[str, bytes]:
    request = httpx.Request(
        "POST",
        "https://example.test/transcribe",
        data={
            "started_at": started_at,
            "ended_at": "2026-08-08T10:00:03Z",
        },
        files={"file": ("answer.bin", audio, media_type)},
    )
    return request.headers["content-type"], request.read()


async def _chunks(body: bytes, size: int) -> AsyncIterator[bytes]:
    for offset in range(0, len(body), size):
        yield body[offset : offset + size]


@pytest.mark.asyncio
@pytest.mark.parametrize("media_type", ["audio/webm", "audio/mp4", "audio/ogg"])
async def test_parser_keeps_supported_audio_and_timestamps_in_memory(
    media_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _parser_module()
    content_type, body = _multipart_body(media_type=media_type, audio=b"audio-bytes")

    def disk_write_forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("raw candidate audio must never touch the filesystem")

    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", disk_write_forbidden)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", disk_write_forbidden)
    monkeypatch.setattr(Path, "write_bytes", disk_write_forbidden)

    result = await module.parse_candidate_audio_multipart(
        chunks=_chunks(body, 3),
        content_type=content_type,
        max_audio_bytes=32,
    )

    assert result.audio == b"audio-bytes"
    assert result.media_type == media_type
    assert result.started_at.isoformat() == "2026-08-08T10:00:00+00:00"
    assert result.ended_at.isoformat() == "2026-08-08T10:00:03+00:00"


@pytest.mark.asyncio
async def test_oversized_chunked_audio_stops_consuming_at_the_bound() -> None:
    module = _parser_module()
    content_type, body = _multipart_body(media_type="audio/webm", audio=b"x" * 128)
    chunks = [body[offset : offset + 5] for offset in range(0, len(body), 5)]
    consumed = 0

    async def observed_chunks() -> AsyncIterator[bytes]:
        nonlocal consumed
        for chunk in chunks:
            consumed += 1
            yield chunk

    with pytest.raises(module.AudioMultipartError) as caught:
        await module.parse_candidate_audio_multipart(
            chunks=observed_chunks(),
            content_type=content_type,
            max_audio_bytes=8,
        )

    assert caught.value.status_code == 413
    assert consumed < len(chunks)
