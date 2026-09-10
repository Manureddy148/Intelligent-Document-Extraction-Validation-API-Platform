import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.repositories.document_repository import DocumentRepository
from app.schemas.extraction import (
    DocumentType,
    ProcessingMetadata,
    ProcessingResult,
    ProcessingStatus,
)
from app.services.document_validation_service import DocumentValidationService
from app.services.extraction_service import ExtractionService
from app.services.financial_validation_service import FinancialValidationService
from app.services.ocr_service import OcrService

logger = get_logger(__name__)

# Top-level keys, per document type, whose presence indicates extraction found
# meaningful content. Used to decide PASS vs FAILED processing status.
_KEY_FIELDS: dict[DocumentType, tuple[str, ...]] = {
    DocumentType.INVOICE: ("total_amount", "vendor_name", "invoice_date"),
    DocumentType.BALANCE_SHEET: ("total_assets", "total_liabilities"),
    DocumentType.PROFIT_AND_LOSS: ("total_income", "total_expenditure"),
    DocumentType.CASH_FLOW_STATEMENT: ("operating_cash_flow", "net_change_in_cash", "closing_cash"),
}


def _has_meaningful_data(extracted_data: dict, document_type: DocumentType) -> bool:
    for key in _KEY_FIELDS.get(document_type, ()):
        field = extracted_data.get(key)
        if isinstance(field, dict) and field.get("value") not in (None, {}):
            return True
    return False


class DocumentService:
    """Orchestrates the full pipeline: validate -> OCR/extract -> financial validation -> persist."""

    def __init__(self, db: Session, settings: Settings):
        self.settings = settings
        self.validation_service = DocumentValidationService(settings)
        self.ocr_service = OcrService(settings)
        self.extraction_service = ExtractionService()
        self.financial_validation_service = FinancialValidationService(settings)
        self.repository = DocumentRepository(db)

    def process(self, filename: str, content: bytes, document_type: DocumentType) -> dict:
        started_at = time.perf_counter()
        logger.info("Processing document '%s' as %s", filename, document_type.value)

        file_validation = self.validation_service.validate(filename, content)

        pages = self.ocr_service.extract_pages(content, file_validation.file_type)
        outcome = self.extraction_service.extract(pages, document_type)

        validation_summary = self.financial_validation_service.validate(document_type, outcome)

        processing_status = (
            ProcessingStatus.PASS
            if _has_meaningful_data(outcome.extracted_data, document_type)
            else ProcessingStatus.FAILED
        )

        processing_time_ms = int((time.perf_counter() - started_at) * 1000)
        metadata = ProcessingMetadata(
            ocr_used=outcome.ocr_used,
            ocr_engine="tesseract" if outcome.ocr_used else None,
            processed_at=datetime.now(timezone.utc),
            processing_time_ms=processing_time_ms,
            pages_processed=outcome.pages_processed,
        )

        result = ProcessingResult(
            document_name=filename,
            document_type=document_type,
            processing_status=processing_status,
            file_validation=file_validation,
            extracted_data=outcome.extracted_data,
            validation=validation_summary,
            processing_metadata=metadata,
        )
        result_dict = result.model_dump(mode="json")

        self.repository.upsert(
            document_name=filename,
            document_type=document_type.value,
            processing_status=processing_status.value,
            result_json=result_dict,
        )
        logger.info(
            "Processed '%s': status=%s validation=%s in %sms",
            filename,
            processing_status.value,
            validation_summary.overall_status.value,
            processing_time_ms,
        )
        return result_dict

    def get_by_name(self, document_name: str) -> dict | None:
        record = self.repository.get_by_name(document_name)
        return record.result_json if record else None

    def list_documents(self, limit: int = 100, offset: int = 0):
        return self.repository.list_all(limit=limit, offset=offset)
