import io
from dataclasses import dataclass

import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PageText:
    page_number: int
    text: str
    ocr_used: bool


class OcrService:
    """Extracts per-page text from PDFs (native text or OCR fallback) and images (OCR)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

    def extract_pages(self, content: bytes, content_type: str) -> list[PageText]:
        if content_type == "application/pdf":
            return self._extract_pdf(content)
        return self._extract_image(content)

    def _extract_pdf(self, content: bytes) -> list[PageText]:
        pages: list[PageText] = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                native_text = (page.extract_text() or "").strip()
                if len(native_text) >= self.settings.ocr_min_native_text_chars:
                    pages.append(PageText(page_number=index, text=native_text, ocr_used=False))
                else:
                    logger.info("Page %s has little/no text layer; falling back to OCR", index)
                    ocr_text = self._ocr_pdf_page(content, index)
                    pages.append(PageText(page_number=index, text=ocr_text, ocr_used=True))
        return pages

    def _ocr_pdf_page(self, content: bytes, page_number: int) -> str:
        images = convert_from_bytes(
            content,
            dpi=self.settings.ocr_dpi,
            first_page=page_number,
            last_page=page_number,
        )
        if not images:
            return ""
        return pytesseract.image_to_string(images[0])

    def _extract_image(self, content: bytes) -> list[PageText]:
        with Image.open(io.BytesIO(content)) as image:
            text = pytesseract.image_to_string(image)
        return [PageText(page_number=1, text=text, ocr_used=True)]
