import io
from dataclasses import dataclass, field

import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image, ImageOps

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


def _readable_word_count(words: list[Word]) -> int:
    """Words with enough letters to be real text rather than OCR speckle."""
    return sum(1 for word in words if sum(ch.isalpha() for ch in word.text) >= 3)


@dataclass
class OcrPass:
    """One OCR attempt and how well it read.

    Letter counts alone cannot tell text from nonsense - a sideways page yields
    confident-looking rubbish like "JOOdVHO" that passes any such test - so
    quality combines how much readable text came out with how sure Tesseract was
    of it.
    """

    words: list[Word]
    mean_confidence: float

    @property
    def quality(self) -> float:
        return _readable_word_count(self.words) * self.mean_confidence


def tesseract_version() -> str | None:
    """The installed Tesseract version, or None if the binary is unreachable.

    Used by the health endpoint: OCR is a system binary rather than a Python
    dependency, so a deployment can start cleanly and still be unable to read a
    scanned page.
    """
    try:
        return str(pytesseract.get_tesseract_version())
    except Exception:
        logger.exception("Tesseract is not available")
        return None


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

    def render_page_images(self, content: bytes, content_type: str) -> list[bytes]:
        """The pages as PNG bytes, for the optional vision-based recovery pass.

        Downscaled first: the model needs to read the page, not to receive every
        pixel of a 12-megapixel photograph, and the smaller payload is faster and
        cheaper for no loss of legibility.
        """
        try:
            if content_type == "application/pdf":
                images = convert_from_bytes(
                    content, dpi=self.settings.ocr_dpi, last_page=self.settings.max_pages
                )
            else:
                images = [Image.open(io.BytesIO(content))]
        except Exception:
            logger.exception("Could not render page images for the vision pass")
            return []

        rendered: list[bytes] = []
        for image in images[: self.settings.max_pages]:
            copy = image.convert("RGB")
            copy.thumbnail((self.settings.vision_max_image_px,) * 2)
            buffer = io.BytesIO()
            copy.save(buffer, format="PNG", optimize=True)
            rendered.append(buffer.getvalue())
        return rendered

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
        """OCR a page. Dropping colour first recovers thin type on faint scans."""
        grey = ImageOps.grayscale(image)
        upright = self._ocr_pass(grey)
        best = self._best_orientation(grey, upright, page_number)
        rows = group_words_into_rows(best.words, page_number)
        return PageText(
            page_number=page_number,
            text="\n".join(row.text for row in rows),
            ocr_used=True,
            rows=rows,
        )

    def _best_orientation(self, image: Image.Image, upright: OcrPass, page_number: int) -> OcrPass:
        """Re-read a page sideways when reading it upright went badly.

        A photographed receipt is often rotated, and Tesseract returns confident
        nonsense rather than failing. Orientation detection alone is not enough:
        it reports low confidence on exactly these pages. So its suggestion is
        only taken when re-reading the page that way measurably reads better.

        Orientation is decided on a downscaled copy - picking a rotation needs a
        comparison, not a good read - and only the winner is re-read at full
        resolution. Probing all four orientations at full size cost 22s on a
        12-megapixel photograph; this costs a little over a second. The whole
        path only runs on a page that already read poorly, so upright documents
        are not slowed down at all.
        """
        if upright.mean_confidence >= self.settings.ocr_min_mean_confidence:
            return upright

        probe = image.copy()
        probe.thumbnail((self.settings.ocr_orientation_probe_px,) * 2)
        baseline = self._ocr_pass(probe).quality

        try:
            # Orientation detection also runs on the downscaled copy: on a
            # 12-megapixel photograph it costs 2.8s at full size against 0.8s here.
            osd = pytesseract.image_to_osd(probe, output_type=pytesseract.Output.DICT)
            suggested = int(osd.get("rotate", 0)) % 360
        except Exception:
            logger.exception("Orientation detection failed on page %s; keeping it upright", page_number)
            suggested = 0

        candidates = [suggested] if suggested else []
        # A low-confidence OSD reading is a hint, not an answer; on a page this
        # poor the other orientations are worth probing rather than trusting it.
        candidates += [rotation for rotation in (90, 180, 270) if rotation != suggested]

        best_rotation, best_quality = 0, baseline
        for rotation in candidates:
            quality = self._ocr_pass(probe.rotate(-rotation, expand=True)).quality
            if quality > best_quality * 1.25:
                best_rotation, best_quality = rotation, quality
                # The suggestion was right; the remaining orientations cost a
                # full OCR pass each and cannot be upright if this one is.
                break

        if not best_rotation:
            return upright

        rotated = self._ocr_pass(image.rotate(-best_rotation, expand=True))
        logger.info(
            "Page %s read better rotated %s degrees (probe quality %.0f -> %.0f); using the rotated read",
            page_number, best_rotation, baseline, best_quality,
        )
        return rotated if rotated.quality > upright.quality else upright

    def _ocr_words(self, image: Image.Image) -> list[Word]:
        return self._ocr_pass(image).words

    def _ocr_pass(self, image: Image.Image) -> OcrPass:
        config = f"--psm {self.settings.ocr_psm}"
        data = pytesseract.image_to_data(image, config=config, output_type=pytesseract.Output.DICT)

        words: list[Word] = []
        confidences: list[float] = []
        for index, text in enumerate(data["text"]):
            if not text or not text.strip():
                continue
            if int(data["conf"][index]) < self.settings.ocr_min_confidence:
                continue
            confidences.append(float(data["conf"][index]))
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

        mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return OcrPass(words=words, mean_confidence=mean_confidence)
