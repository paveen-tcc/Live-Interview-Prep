"""Bounded, signature-checked PDF and DOCX text extraction."""

from __future__ import annotations

import hashlib
import io
import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ..config import Settings

PDF_MEDIA_TYPES = {"application/pdf"}
DOCX_MEDIA_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}


@dataclass(frozen=True)
class ExtractedDocument:
    file_type: str
    media_type: str
    size: int
    sha256: str
    text: str
    segments: list[dict[str, str]]


class UploadValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def validate_and_extract(
    *, filename: str, media_type: str | None, contents: bytes, settings: Settings
) -> ExtractedDocument:
    if not contents:
        raise UploadValidationError("The resume file is empty.")
    if len(contents) > settings.resume_max_bytes:
        raise UploadValidationError(
            "The resume exceeds the "
            f"{settings.resume_max_bytes // 1_000_000} MB limit.",
            status_code=413,
        )

    safe_filename = PurePosixPath(filename or "").name
    extension = PurePosixPath(safe_filename).suffix.lower()
    normalized_media_type = (media_type or "").lower().split(";", 1)[0].strip()
    if extension == ".pdf":
        if normalized_media_type not in PDF_MEDIA_TYPES:
            raise UploadValidationError(
                "The file extension and content type do not identify "
                "the same PDF file.",
                status_code=415,
            )
        segments = _extract_pdf(contents, settings)
        file_type = "pdf"
        expected_media_type = "application/pdf"
    elif extension == ".docx":
        if normalized_media_type not in DOCX_MEDIA_TYPES:
            raise UploadValidationError(
                "The file extension and content type do not identify "
                "the same DOCX file.",
                status_code=415,
            )
        segments = _extract_docx(contents, settings)
        file_type = "docx"
        expected_media_type = next(iter(DOCX_MEDIA_TYPES))
    else:
        raise UploadValidationError(
            "Only PDF and DOCX resume files are supported.", status_code=415
        )

    text = "\n".join(segment["text"] for segment in segments).strip()
    if not text:
        raise UploadValidationError(
            "No selectable text was found. Upload a text-based PDF/DOCX "
            "or paste the details manually."
        )
    if len(text) > settings.resume_max_extracted_characters:
        raise UploadValidationError(
            "The extracted resume text exceeds the supported limit.", status_code=413
        )
    return ExtractedDocument(
        file_type=file_type,
        media_type=expected_media_type,
        size=len(contents),
        sha256=hashlib.sha256(contents).hexdigest(),
        text=text,
        segments=segments,
    )


def _extract_pdf(contents: bytes, settings: Settings) -> list[dict[str, str]]:
    if not contents.startswith(b"%PDF-"):
        raise UploadValidationError(
            "The uploaded file has a .pdf name but does not contain a PDF signature.",
            status_code=415,
        )
    try:
        reader = PdfReader(io.BytesIO(contents), strict=True)
        if reader.is_encrypted:
            raise UploadValidationError(
                "Encrypted or password-protected PDFs are not supported."
            )
        if len(reader.pages) > settings.resume_max_pages:
            raise UploadValidationError(
                f"The PDF exceeds the {settings.resume_max_pages}-page limit.",
                status_code=413,
            )
        segments = []
        for page_number, page in enumerate(reader.pages, start=1):
            page_text = (page.extract_text() or "").strip()
            if page_text:
                segments.append(
                    {
                        "source_id": f"resume:page:{page_number}",
                        "label": f"Resume page {page_number}",
                        "text": page_text,
                    }
                )
        return segments
    except UploadValidationError:
        raise
    except (PdfReadError, KeyError, TypeError, ValueError, OSError) as exc:
        raise UploadValidationError(
            "The PDF is corrupt or could not be read safely."
        ) from exc


def _extract_docx(contents: bytes, settings: Settings) -> list[dict[str, str]]:
    if not contents.startswith(b"PK"):
        raise UploadValidationError(
            "The uploaded file has a .docx name but does not contain a DOCX signature.",
            status_code=415,
        )
    try:
        with zipfile.ZipFile(io.BytesIO(contents)) as archive:
            entries = archive.infolist()
            if len(entries) > settings.resume_max_docx_entries:
                raise UploadValidationError(
                    "The DOCX contains too many internal files.", status_code=413
                )
            total_uncompressed = 0
            names = set()
            for entry in entries:
                normalized = posixpath.normpath(entry.filename)
                if normalized.startswith("../") or normalized.startswith("/"):
                    raise UploadValidationError(
                        "The DOCX contains an unsafe internal path."
                    )
                if entry.flag_bits & 0x1:
                    raise UploadValidationError(
                        "Encrypted DOCX files are not supported."
                    )
                total_uncompressed += entry.file_size
                names.add(normalized)
            if total_uncompressed > settings.resume_max_docx_uncompressed_bytes:
                raise UploadValidationError(
                    "The expanded DOCX exceeds the safe processing limit.",
                    status_code=413,
                )
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                raise UploadValidationError(
                    "The DOCX package is incomplete or corrupt."
                )
            if "word/vbaProject.bin" in names:
                raise UploadValidationError(
                    "Macro-enabled Word files are not supported."
                )
            if archive.testzip() is not None:
                raise UploadValidationError("The DOCX package is corrupt.")

        document = Document(io.BytesIO(contents))
        segments: list[dict[str, str]] = []
        block_number = 0
        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                values = [block.text]
                kind = "paragraph"
            elif isinstance(block, Table):
                values = [
                    " | ".join(
                        cell.text.strip() for cell in row.cells if cell.text.strip()
                    )
                    for row in block.rows
                ]
                kind = "table row"
            else:
                continue
            for value in values:
                normalized_text = " ".join(value.split()).strip()
                if not normalized_text:
                    continue
                block_number += 1
                segments.append(
                    {
                        "source_id": f"resume:block:{block_number}",
                        "label": f"Resume {kind} {block_number}",
                        "text": normalized_text,
                    }
                )
        return segments
    except UploadValidationError:
        raise
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise UploadValidationError(
            "The DOCX is corrupt or could not be read safely."
        ) from exc
