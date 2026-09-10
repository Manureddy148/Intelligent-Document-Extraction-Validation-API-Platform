import io

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from PIL import Image, UnidentifiedImageError

from app.core.config import Settings
from app.core.exceptions import (
    CorruptedFileError,
    EmptyFileError,
    PageLimitExceededError,
    UnsupportedFileTypeError,
)
from app.core.logging import get_logger
from app.schemas.extraction import FileValidation

logger = get_logger(__name__)

_MAGIC_BYTES: dict[bytes, str] = {
    b"%PDF": "application/pdf",
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
}


def _sniff_content_type(content: bytes) -> str | None:
    for magic, content_type in _MAGIC_BYTES.items():
        if content.startswith(magic):
            return content_type
    return None


class DocumentValidationService:
    """Validates uploaded files before OCR/extraction: type, integrity, page count."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def validate(self, filename: str, content: bytes) -> FileValidation:
        if not content:
            logger.warning("Rejected empty upload: %s", filename)
            raise EmptyFileError(f"Uploaded file '{filename}' is empty.")

        content_type = _sniff_content_type(content)
        if content_type is None or content_type not in self.settings.allowed_content_types:
            logger.warning("Rejected unsupported file type for %s (sniffed=%s)", filename, content_type)
            raise UnsupportedFileTypeError("Only PDF / JPG / PNG documents are supported.")

        if content_type == "application/pdf":
            page_count = self._validate_pdf(filename, content)
        else:
            page_count = self._validate_image(filename, content)

        if page_count > self.settings.max_pages:
            logger.warning("Rejected %s: page_count=%s exceeds limit=%s", filename, page_count, self.settings.max_pages)
            raise PageLimitExceededError(
                f"Document has {page_count} pages; the maximum supported is {self.settings.max_pages}."
            )

        return FileValidation(
            file_type=content_type,
            is_supported=True,
            is_readable=True,
            page_count=page_count,
            status="PASS",
        )

    def _validate_pdf(self, filename: str, content: bytes) -> int:
        try:
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception as exc:
                    raise CorruptedFileError(f"'{filename}' is password-protected and cannot be read.") from exc
            page_count = len(reader.pages)
            if page_count == 0:
                raise CorruptedFileError(f"'{filename}' contains no readable pages.")
            return page_count
        except CorruptedFileError:
            raise
        except (PdfReadError, Exception) as exc:
            logger.exception("Failed to read PDF %s", filename)
            raise CorruptedFileError(f"'{filename}' could not be read as a valid PDF.") from exc

    def _validate_image(self, filename: str, content: bytes) -> int:
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
            return 1
        except (UnidentifiedImageError, OSError) as exc:
            logger.exception("Failed to read image %s", filename)
            raise CorruptedFileError(f"'{filename}' could not be read as a valid image.") from exc
