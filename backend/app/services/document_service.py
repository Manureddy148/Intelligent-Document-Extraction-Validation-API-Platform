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
from app.services.llm_extraction_service import LlmExtractionService
from app.services.ocr_service import OcrService
from app.utils.invoice_parsing import InvoiceLineItem

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
        self.llm_service = LlmExtractionService(settings)
        self.repository = DocumentRepository(db)

    def process(self, filename: str, content: bytes, document_type: DocumentType) -> dict:
        started_at = time.perf_counter()
        logger.info("Processing document '%s' as %s", filename, document_type.value)

        file_validation = self.validation_service.validate(filename, content)
        logger.info(
            "Validated '%s': %s, %s page(s), %.1fKB",
            filename, file_validation.file_type, file_validation.page_count, len(content) / 1024,
        )

        ocr_started = time.perf_counter()
        pages = self.ocr_service.extract_pages(content, file_validation.file_type)
        logger.info(
            "Read %s page(s) of '%s' in %sms (ocr=%s)",
            len(pages), filename, int((time.perf_counter() - ocr_started) * 1000),
            any(page.ocr_used for page in pages),
        )

        outcome = self.extraction_service.extract(pages, document_type)

        llm_assisted = self._recover_missing_fields(
            pages, outcome, document_type, content, file_validation.file_type
        )

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
            extraction_method="rule_based_layout" + ("+llm_assisted" if llm_assisted else ""),
            llm_model=self.llm_service.model if llm_assisted else None,
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

    def _recover_missing_fields(
        self, pages, outcome, document_type: DocumentType, content: bytes, file_type: str
    ) -> bool:
        """Use the optional LLM pass to fill fields the rule-based extractor left null.

        Only fields still null after the deterministic pass are asked about, so a
        value grounded in a source row is never replaced by a model's reading of
        the same page.
        """
        if not self.llm_service.enabled:
            return False

        missing = [
            key
            for key, field in outcome.extracted_data.items()
            if isinstance(field, dict) and "value" in field and field.get("value") is None
        ]
        if not missing:
            return False

        document_text = "\n".join(f"--- page {page.page_number} ---\n{page.text}" for page in pages)
        page_images = (
            self.ocr_service.render_page_images(content, file_type)
            if self.llm_service.provider == "gemini"
            else []
        )

        started = time.perf_counter()
        # An invoice carries one figure per field; "current" is only a sentinel
        # that lets the validation loop share a shape with the statements. Asking
        # the model for values per period would return {"current": 126.27} where
        # the deterministic path returns 126.27.
        periods = [] if document_type == DocumentType.INVOICE else outcome.periods
        recovered = self.llm_service.recover_missing_fields(
            document_text, document_type.value, missing, page_images, periods
        )
        logger.info(
            "LLM recovery pass (%s) filled %s of %s missing fields in %sms",
            self.llm_service.provider, len(recovered), len(missing),
            int((time.perf_counter() - started) * 1000),
        )
        for key, field in recovered.items():
            outcome.extracted_data[key] = field
        self._apply_to_invoice_context(outcome, document_type, recovered)

        recovered_items = self._recover_line_items(outcome, document_type, page_images)
        return bool(recovered) or recovered_items

    def _apply_to_invoice_context(self, outcome, document_type: DocumentType, recovered: dict) -> None:
        """Mirror recovered invoice figures onto the context the checks read.

        The invoice checks work from the parsed context rather than the response
        dict, so a subtotal or tax line the vision pass recovered would
        otherwise be reported to the caller but invisible to the reconciliation
        that needs it.
        """
        ctx = outcome.invoice_context
        if document_type != DocumentType.INVOICE or ctx is None:
            return

        annotations = getattr(type(ctx), "__annotations__", {})
        # The amount payable is what every other figure is judged against, so it
        # has to be in place before they are checked.
        ordered = sorted(recovered.items(), key=lambda kv: kv[0] != "total_amount")
        for key, field in ordered:
            if key not in annotations:
                continue
            value = field.get("value")
            declared = annotations[key]
            wants_number = "float" in str(declared) or "int" in str(declared)
            # A model can return "10%" for a rate or "RM 9.00" for an amount.
            # Assigning that to a numeric field turns a later subtraction into a
            # TypeError and a 500, so only a matching type is mirrored across.
            if wants_number and not isinstance(value, (int, float)):
                logger.info("Not mirroring '%s' onto the invoice context: %r is not numeric", key, value)
                continue
            if not wants_number and not isinstance(value, str):
                continue
            if not self._plausible_invoice_value(ctx, key, value):
                logger.info("Not mirroring '%s'=%r onto the invoice context: implausible", key, value)
                continue
            setattr(ctx, key, value)

    @staticmethod
    def _plausible_invoice_value(ctx, key: str, value) -> bool:
        """Reject a recovered figure the invoice itself contradicts.

        Tax and the net subtotal are both parts of the amount payable, so
        neither can exceed it. One receipt came back with the grand total
        recovered as the tax line, which then made subtotal + tax overshoot the
        total and reported the document as inconsistent when the reading was.
        """
        total = ctx.total_amount
        if total is None or not isinstance(value, (int, float)) or total <= 0:
            return True
        # Tax is one part of the amount payable, so it cannot reach the whole of
        # it. A subtotal legitimately can, when nothing is added on top.
        if key in ("tax_amount", "discount") and value >= total:
            return False
        if key == "subtotal" and value > total * 1.01:
            return False
        if key == "tax_rate_percent" and not 0 <= value <= 100:
            return False
        return True

    def _recover_line_items(self, outcome, document_type: DocumentType, page_images) -> bool:
        """Read the item table off the page when the parser found no rows.

        Only invoices: a statement's rows come from the section walk, which is
        driven by the same text the vision pass would be second-guessing.
        """
        if document_type != DocumentType.INVOICE or not page_images:
            return False
        if outcome.extracted_data.get("line_items"):
            return False

        items = self.llm_service.recover_line_items(document_type.value, page_images)
        if not items:
            return False

        outcome.extracted_data["line_items"] = items
        # Feed them back into the invoice context so the reconciliation checks
        # read the same rows that are reported, rather than a second list.
        if outcome.invoice_context is not None:
            outcome.invoice_context.line_items = [
                InvoiceLineItem(
                    description=row["description"],
                    quantity=row["quantity"],
                    unit_price=row["unit_price"],
                    amount=row["amount"],
                    page_number=row.get("page_number"),
                    source_text=row.get("source_text"),
                )
                for row in items
            ]
            # Reported, but never reconciled. Being shown the whole page is not
            # proof of having listed every row of it, and on a noisy receipt the
            # model both misses rows and misreads columns. Summing a list we
            # cannot confirm is whole would report shortfalls belonging to the
            # reading rather than to the document - exactly the false finding
            # this pipeline exists to avoid. The rows still appear in
            # extracted_data, each marked llm_assisted and carrying the line it
            # was read from, so an evaluator can check them against the page.
            outcome.invoice_context.line_items_incomplete = True
        return True

    def get_by_name(self, document_name: str) -> dict | None:
        record = self.repository.get_by_name(document_name)
        return record.result_json if record else None

    def list_documents(self, limit: int = 100, offset: int = 0):
        return self.repository.list_all(limit=limit, offset=offset)
