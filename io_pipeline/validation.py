from __future__ import annotations

from dataclasses import dataclass

import fitz  # PyMuPDF

from config.settings import settings
from shared.utils.mime import detect_mime_from_bytes, mime_from_filename

ALLOWED_EXTENSIONS: frozenset[str] = frozenset({"pdf", "png", "jpg", "jpeg", "tiff"})


class ValidationError(Exception):
    """Raised when a document fails any ingestion validation check."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ValidatedFile:
    data: bytes
    mime_type: str
    file_size: int
    extension: str


class ValidationService:
    """Stateless validator.  Call validate() once per upload."""

    def validate(self, data: bytes, filename: str) -> ValidatedFile:
        # 1. empty
        if not data:
            raise ValidationError("empty_file")

        # 2. size
        if len(data) > settings.MAX_UPLOAD_BYTES:
            raise ValidationError(f"file_too_large:{len(data)}")

        # 3. extension whitelist
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            raise ValidationError(f"unsupported_extension:{ext!r}")

        # 4. MIME consistency (magic bytes vs declared extension)
        detected = detect_mime_from_bytes(data)
        declared = mime_from_filename(filename)
        if detected and detected != declared:
            raise ValidationError(f"mime_mismatch:detected={detected!r},declared={declared!r}")
        mime = detected or declared

        # 5–7. PDF-specific checks
        if ext == "pdf":
            try:
                doc = fitz.open(stream=data, filetype="pdf")
            except Exception as exc:
                raise ValidationError("pdf_corrupted") from exc
            try:
                if doc.needs_pass:
                    raise ValidationError("pdf_password_protected")
                if len(doc) > settings.MAX_PDF_PAGES:
                    raise ValidationError(f"pdf_too_many_pages:{len(doc)}")
            finally:
                doc.close()

        return ValidatedFile(
            data=data,
            mime_type=mime,
            file_size=len(data),
            extension=ext,
        )
