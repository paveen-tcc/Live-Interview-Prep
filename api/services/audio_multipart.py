"""Bounded, memory-only multipart parsing for candidate audio."""

from __future__ import annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import UTC, datetime

from python_multipart.exceptions import FormParserError, MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header

_MAX_MULTIPART_OVERHEAD_BYTES = 64 * 1024
_MAX_TEXT_FIELD_BYTES = 128
_MAX_FILENAME_BYTES = 255
_ALLOWED_FIELDS = {"file", "started_at", "ended_at"}


@dataclass(frozen=True)
class CandidateAudioMultipart:
    audio: bytes
    media_type: str
    filename: str
    started_at: datetime | None
    ended_at: datetime | None


class AudioMultipartError(ValueError):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class _CandidateAudioCollector:
    def __init__(self, max_audio_bytes: int) -> None:
        self.max_audio_bytes = max_audio_bytes
        self.header_field = bytearray()
        self.header_value = bytearray()
        self.headers: dict[bytes, bytes] = {}
        self.part_name: str | None = None
        self.part_data = bytearray()
        self.audio: bytes | None = None
        self.media_type = ""
        self.filename = ""
        self.fields: dict[str, str] = {}
        self.complete = False

    def on_part_begin(self) -> None:
        self.headers = {}
        self.part_name = None
        self.part_data = bytearray()

    def on_header_begin(self) -> None:
        self.header_field = bytearray()
        self.header_value = bytearray()

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self.header_field.extend(data[start:end])

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self.header_value.extend(data[start:end])

    def on_header_end(self) -> None:
        self.headers[bytes(self.header_field).lower()] = bytes(self.header_value)

    def on_headers_finished(self) -> None:
        disposition, options = parse_options_header(
            self.headers.get(b"content-disposition")
        )
        if disposition != b"form-data" or b"name" not in options:
            raise AudioMultipartError(
                "Each multipart part must have a form-data field name.",
                status_code=422,
            )
        try:
            name = options[b"name"].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AudioMultipartError(
                "Multipart field names must be UTF-8.", status_code=422
            ) from exc
        if name not in _ALLOWED_FIELDS:
            raise AudioMultipartError(
                "The multipart request contains an unsupported field.",
                status_code=422,
            )
        self.part_name = name
        if name != "file":
            if b"filename" in options:
                raise AudioMultipartError(
                    "Only the file field may contain a filename.", status_code=422
                )
            return
        if self.audio is not None:
            raise AudioMultipartError(
                "The multipart request must contain exactly one file.",
                status_code=422,
            )
        raw_filename = options.get(b"filename", b"")
        if not raw_filename or len(raw_filename) > _MAX_FILENAME_BYTES:
            raise AudioMultipartError(
                "Candidate audio must include a valid filename.", status_code=422
            )
        try:
            self.filename = raw_filename.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AudioMultipartError(
                "Candidate audio filename must be UTF-8.", status_code=422
            ) from exc
        self.media_type = (
            self.headers.get(b"content-type", b"")
            .decode("latin-1")
            .split(";", 1)[0]
            .strip()
            .lower()
        )

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self.part_name is None:
            raise AudioMultipartError(
                "Multipart data arrived before its headers.", status_code=422
            )
        value = data[start:end]
        maximum = (
            self.max_audio_bytes if self.part_name == "file" else _MAX_TEXT_FIELD_BYTES
        )
        if len(self.part_data) + len(value) > maximum:
            if self.part_name == "file":
                raise AudioMultipartError(
                    "Candidate audio exceeds the configured size limit.",
                    status_code=413,
                )
            raise AudioMultipartError(
                "A multipart text field is too large.", status_code=422
            )
        self.part_data.extend(value)

    def on_part_end(self) -> None:
        if self.part_name is None:
            raise AudioMultipartError(
                "Multipart data arrived before its headers.", status_code=422
            )
        if self.part_name == "file":
            self.audio = bytes(self.part_data)
            return
        if self.part_name in self.fields:
            raise AudioMultipartError(
                "Multipart text fields cannot be repeated.", status_code=422
            )
        try:
            self.fields[self.part_name] = self.part_data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AudioMultipartError(
                "Multipart text fields must be UTF-8.", status_code=422
            ) from exc

    def on_end(self) -> None:
        self.complete = True


async def parse_candidate_audio_multipart(
    *,
    chunks: AsyncIterable[bytes],
    content_type: str,
    max_audio_bytes: int,
    content_length: int | None = None,
) -> CandidateAudioMultipart:
    """Parse one multipart upload without Starlette's spooled-file machinery."""

    media_type, options = parse_options_header(content_type)
    boundary = options.get(b"boundary")
    if media_type != b"multipart/form-data" or not boundary:
        raise AudioMultipartError(
            "Candidate audio must use multipart/form-data.", status_code=415
        )
    if max_audio_bytes < 1:
        raise ValueError("max_audio_bytes must be positive")
    maximum_body_bytes = max_audio_bytes + _MAX_MULTIPART_OVERHEAD_BYTES
    if content_length is not None and content_length > maximum_body_bytes:
        raise AudioMultipartError(
            "Candidate audio exceeds the configured size limit.", status_code=413
        )

    collector = _CandidateAudioCollector(max_audio_bytes)
    callbacks = {
        "on_part_begin": collector.on_part_begin,
        "on_part_data": collector.on_part_data,
        "on_part_end": collector.on_part_end,
        "on_header_begin": collector.on_header_begin,
        "on_header_field": collector.on_header_field,
        "on_header_value": collector.on_header_value,
        "on_header_end": collector.on_header_end,
        "on_headers_finished": collector.on_headers_finished,
        "on_end": collector.on_end,
    }
    try:
        parser = MultipartParser(
            boundary,
            callbacks,
            max_size=maximum_body_bytes,
            max_header_count=4,
            max_header_size=1_024,
        )
        body_bytes = 0
        async for chunk in chunks:
            body_bytes += len(chunk)
            if body_bytes > maximum_body_bytes:
                raise AudioMultipartError(
                    "Candidate audio exceeds the configured size limit.",
                    status_code=413,
                )
            if chunk:
                parser.write(chunk)
        parser.finalize()
    except AudioMultipartError:
        raise
    except (FormParserError, MultipartParseError, UnicodeDecodeError) as exc:
        raise AudioMultipartError(
            "The multipart audio request is malformed.", status_code=422
        ) from exc

    if not collector.complete or collector.audio is None:
        raise AudioMultipartError(
            "The multipart request must contain exactly one file.", status_code=422
        )
    if not collector.audio:
        raise AudioMultipartError("Candidate audio must not be empty.", status_code=422)
    started_at = _parse_timestamp(collector.fields.get("started_at"), "started_at")
    ended_at = _parse_timestamp(collector.fields.get("ended_at"), "ended_at")
    return CandidateAudioMultipart(
        audio=collector.audio,
        media_type=collector.media_type,
        filename=collector.filename,
        started_at=started_at,
        ended_at=ended_at,
    )


def _parse_timestamp(value: str | None, field: str) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AudioMultipartError(
            f"Multipart field {field} must be an ISO 8601 timestamp.",
            status_code=422,
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
