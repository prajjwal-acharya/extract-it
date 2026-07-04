from unittest.mock import patch

import fitz
import pytest

from io_pipeline.validation import ValidatedFile, ValidationError, ValidationService


def _make_pdf(pages: int = 1, password: str | None = None) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    kwargs: dict = {}
    if password:
        kwargs = dict(
            encryption=fitz.PDF_ENCRYPT_AES_256,
            user_pw=password,
            owner_pw=password,
        )
    return doc.tobytes(**kwargs)


svc = ValidationService()


def test_empty_file_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        svc.validate(b"", "doc.pdf")
    assert exc_info.value.reason == "empty_file"


def test_oversized_file_rejected() -> None:
    with patch("io_pipeline.validation.settings") as mock_settings:
        mock_settings.MAX_UPLOAD_BYTES = 10
        mock_settings.MAX_PDF_PAGES = 50
        with pytest.raises(ValidationError) as exc_info:
            svc.validate(b"x" * 11, "doc.pdf")
    assert exc_info.value.reason.startswith("file_too_large")


def test_unsupported_extension_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        svc.validate(b"anything", "doc.exe")
    assert exc_info.value.reason.startswith("unsupported_extension")


def test_mime_mismatch_rejected() -> None:
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    with pytest.raises(ValidationError) as exc_info:
        svc.validate(png_bytes, "doc.pdf")
    assert exc_info.value.reason.startswith("mime_mismatch")


def test_valid_pdf_accepted() -> None:
    result = svc.validate(_make_pdf(), "passport_ABC_20240101.pdf")
    assert isinstance(result, ValidatedFile)
    assert result.extension == "pdf"
    assert result.mime_type == "application/pdf"


def test_corrupted_pdf_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        svc.validate(b"%PDF-1.4 this is garbage", "doc.pdf")
    assert exc_info.value.reason == "pdf_corrupted"


def test_password_protected_pdf_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        svc.validate(_make_pdf(password="secret"), "doc.pdf")
    assert exc_info.value.reason == "pdf_password_protected"


def test_pdf_page_limit_rejected() -> None:
    with patch("io_pipeline.validation.settings") as mock_settings:
        mock_settings.MAX_UPLOAD_BYTES = 25 * 1024 * 1024
        mock_settings.MAX_PDF_PAGES = 2
        with pytest.raises(ValidationError) as exc_info:
            svc.validate(_make_pdf(pages=3), "doc.pdf")
    assert exc_info.value.reason.startswith("pdf_too_many_pages")


def test_valid_png_accepted() -> None:
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    result = svc.validate(png_bytes, "img.png")
    assert isinstance(result, ValidatedFile)
    assert result.extension == "png"
    assert result.mime_type == "image/png"
