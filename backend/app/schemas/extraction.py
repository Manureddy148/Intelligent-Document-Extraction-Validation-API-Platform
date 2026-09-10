from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    INVOICE = "invoice"
    BALANCE_SHEET = "balance_sheet"
    PROFIT_AND_LOSS = "profit_and_loss"
    CASH_FLOW_STATEMENT = "cash_flow_statement"


class ProcessingStatus(str, Enum):
    PASS = "PASS"
    FAILED = "FAILED"


class ValidationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class FileValidation(BaseModel):
    file_type: str
    is_supported: bool
    is_readable: bool
    page_count: int
    status: str
    reason: str | None = None


class ValidationCheck(BaseModel):
    name: str
    formula: str
    operands: dict[str, float | None]
    calculated_value: float | None
    reported_value: float | None
    variance: float | None
    status: ValidationStatus
    message: str | None = None


class ValidationSummary(BaseModel):
    checks: list[ValidationCheck] = Field(default_factory=list)
    overall_status: ValidationStatus
    issues: list[str] = Field(default_factory=list)


class ProcessingMetadata(BaseModel):
    ocr_used: bool
    ocr_engine: str | None = None
    processed_at: datetime
    processing_time_ms: int
    pages_processed: int


class ProcessingResult(BaseModel):
    document_name: str
    document_type: DocumentType
    processing_status: ProcessingStatus
    overall_confidence: float | None = None
    file_validation: FileValidation
    extracted_data: dict[str, Any]
    validation: ValidationSummary
    processing_metadata: ProcessingMetadata


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
