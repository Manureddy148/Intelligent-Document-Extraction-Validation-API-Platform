import io
import re

from fastapi import UploadFile

from app.models.schemas import DocumentType, ExtractedField, ExtractionResult

_FIELD_PATTERNS: dict[str, str] = {
    "email": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "date": r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    "amount": r"(?:USD|INR|\$|₹)\s?\d+(?:,\d{3})*(?:\.\d{1,2})?",
    "invoice_number": r"invoice\s*(?:no\.?|number|#)?[:\s-]*([A-Za-z0-9-]{3,})",
    "id_number": r"\b[A-Z0-9]{6,}\b",
}


class DocumentExtractor:
    """Extracts raw text and heuristic fields from an uploaded document."""

    def extract_text(self, filename: str, content: bytes, content_type: str | None) -> str:
        if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
            return self._extract_pdf_text(content)
        if content_type and content_type.startswith("image/"):
            return self._extract_image_text(content)
        return content.decode("utf-8", errors="ignore")

    def _extract_pdf_text(self, content: bytes) -> str:
        import pdfplumber

        text_parts: list[str] = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                text_parts.append(page.extract_text() or "")
        return "\n".join(text_parts)

    def _extract_image_text(self, content: bytes) -> str:
        import pytesseract
        from PIL import Image

        image = Image.open(io.BytesIO(content))
        return pytesseract.image_to_string(image)

    def extract_fields(self, raw_text: str) -> list[ExtractedField]:
        fields: list[ExtractedField] = []
        for name, pattern in _FIELD_PATTERNS.items():
            match = re.search(pattern, raw_text, flags=re.IGNORECASE)
            if match:
                value = match.group(1) if match.groups() else match.group(0)
                fields.append(ExtractedField(name=name, value=value.strip()))
        return fields

    async def process(self, upload: UploadFile, document_type: DocumentType) -> ExtractionResult:
        content = await upload.read()
        filename = upload.filename or "document"
        raw_text = self.extract_text(filename, content, upload.content_type)
        fields = self.extract_fields(raw_text)
        return ExtractionResult(
            document_type=document_type,
            filename=filename,
            raw_text=raw_text,
            fields=fields,
        )
