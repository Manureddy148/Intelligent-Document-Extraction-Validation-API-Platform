import io
from dataclasses import dataclass, field

import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image

from app.core.config import Settings
from app.core.exceptions import ExtractionFailedError
from app.core.logging import get_logger
from app.utils.layout import LayoutRow, Word, group_words_into_rows

logger = get_logger(__name__)


@dataclass
class PageText:
    page_number: int
    text: str
    ocr_used: bool
    rows: list[LayoutRow] = field(default_factory=list)


class OcrService:
    """Extracts per-page text *and word geometry* from PDFs and images.

    Native PDF text is used when a text layer exists; otherwise the page is
    rasterised and OCR'd. Both paths return word bounding boxes so the
    extraction layer can rebuild table rows and columns.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

    def extract_pages(self, content: bytes, content_type: str) -> list[PageText]:
        try:
            if content_type == "application/pdf":
                return self._extract_pdf(content)
            return self._extract_image(content)
        except ExtractionFailedError:
            raise
        except Exception as exc:
            logger.exception("Text extraction failed")
            raise ExtractionFailedError("The document could not be read for text extraction.") from exc

    def _extract_pdf(self, content: bytes) -> list[PageText]:
        pages: list[PageText] = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            page_count = len(pdf.pages)
            for index, page in enumerate(pdf.pages, start=1):
                native_text = (page.extract_text() or "").strip()
                if len(native_text) >= self.settings.ocr_min_native_text_chars:
                    words = [
                        Word(text=w["text"], x0=w["x0"], x1=w["x1"], top=w["top"], bottom=w["bottom"])
                        for w in page.extract_words()
                    ]
                    rows = group_words_into_rows(words, index)
                    pages.append(PageText(page_number=index, text=native_text, ocr_used=False, rows=rows))
                else:
                    logger.info("Page %s of %s has no usable text layer; running OCR", index, page_count)
                    pages.append(self._ocr_pdf_page(content, index))
        return pages

    def _ocr_pdf_page(self, content: bytes, page_number: int) -> PageText:
        images = convert_from_bytes(
            content,
            dpi=self.settings.ocr_dpi,
            first_page=page_number,
            last_page=page_number,
        )
        if not images:
            return PageText(page_number=page_number, text="", ocr_used=True)
        return self._ocr_image(images[0], page_number)

    def _extract_image(self, content: bytes) -> list[PageText]:
        with Image.open(io.BytesIO(content)) as image:
            return [self._ocr_image(image, 1)]

    def _ocr_image(self, image: Image.Image, page_number: int) -> PageText:
        config = f"--psm {self.settings.ocr_psm}"
        data = pytesseract.image_to_data(image, config=config, output_type=pytesseract.Output.DICT)

        words: list[Word] = []
        for index, text in enumerate(data["text"]):
            if not text or not text.strip():
                continue
            if int(data["conf"][index]) < self.settings.ocr_min_confidence:
                continue
            left, top = float(data["left"][index]), float(data["top"][index])
            words.append(
                Word(
                    text=text.strip(),
                    x0=left,
                    x1=left + float(data["width"][index]),
                    top=top,
                    bottom=top + float(data["height"][index]),
                )
            )

        rows = group_words_into_rows(words, page_number)
        text = "\n".join(row.text for row in rows)
        return PageText(page_number=page_number, text=text, ocr_used=True, rows=rows)
