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


def test_managed_postgres_url_is_normalised_for_sqlalchemy():
    """Render/Heroku hand out `postgres://`, which SQLAlchemy 2 refuses to parse."""
    from app.core.database import _normalise_database_url

    assert _normalise_database_url("postgres://user:pw@host:5432/db") == "postgresql+psycopg2://user:pw@host:5432/db"
    assert _normalise_database_url("sqlite:///./data/documents.db") == "sqlite:///./data/documents.db"


def test_rejects_oversized_upload(validation_service):
    from app.core.exceptions import FileTooLargeError

    oversized = b"%PDF-1.4" + b"0" * (get_settings().max_upload_size_mb * 1024 * 1024 + 1)
    with pytest.raises(FileTooLargeError):
        validation_service.validate("huge.pdf", oversized)


def test_safe_document_name_normalises_hostile_names():
    from app.utils.filenames import safe_document_name

    assert safe_document_name("../../../etc/passwd") == "passwd"
    assert safe_document_name("C:\\Windows\\system32\\x.pdf") == "x.pdf"
    assert safe_document_name("a\x00b.pdf") == "ab.pdf"
    assert safe_document_name("") == "document"
    assert safe_document_name(None) == "document"
    assert safe_document_name("....") == "document"
    assert len(safe_document_name("A" * 600 + ".pdf")) <= 200
    # A legitimate name, including non-Latin script, is left alone.
    assert safe_document_name("Consolidated Balance Sheet 2017.pdf") == "Consolidated Balance Sheet 2017.pdf"
    assert safe_document_name("发票.pdf") == "发票.pdf"
