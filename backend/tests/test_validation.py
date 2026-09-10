import pytest

from app.core.config import get_settings
from app.core.exceptions import CorruptedFileError, EmptyFileError, UnsupportedFileTypeError
from app.services.document_validation_service import DocumentValidationService


@pytest.fixture
def validation_service() -> DocumentValidationService:
    return DocumentValidationService(get_settings())


def test_rejects_empty_file(validation_service):
    with pytest.raises(EmptyFileError):
        validation_service.validate("empty.pdf", b"")


def test_rejects_unsupported_file_type(validation_service):
    with pytest.raises(UnsupportedFileTypeError):
        validation_service.validate("notes.txt", b"just some plain text, not a real document")


def test_rejects_corrupted_pdf(validation_service):
    with pytest.raises(CorruptedFileError):
        validation_service.validate("broken.pdf", b"%PDF-1.4\nthis is not a valid pdf body")


def test_accepts_valid_pdf(validation_service, blank_pdf_bytes):
    result = validation_service.validate("sample.pdf", blank_pdf_bytes)
    assert result.status == "PASS"
    assert result.is_supported is True
    assert result.is_readable is True
    assert result.page_count == 1
    assert result.file_type == "application/pdf"


def test_rejects_page_limit_exceeded(validation_service, blank_pdf_bytes):
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(get_settings().max_pages + 1):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)

    from app.core.exceptions import PageLimitExceededError

    with pytest.raises(PageLimitExceededError):
        validation_service.validate("too_long.pdf", buffer.getvalue())
