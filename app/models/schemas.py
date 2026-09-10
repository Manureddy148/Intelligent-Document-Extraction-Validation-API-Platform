from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    INVOICE = "invoice"
    RECEIPT = "receipt"
    ID_CARD = "id_card"
    GENERIC = "generic"


class ExtractedField(BaseModel):
    name: str
    value: str
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)


class ExtractionResult(BaseModel):
    document_type: DocumentType
    filename: str
    raw_text: str
    fields: list[ExtractedField]
    extracted_at: datetime = Field(default_factory=datetime.utcnow)


class ValidationRequest(BaseModel):
    document_type: DocumentType
    fields: dict[str, str]


class ValidationIssue(BaseModel):
    field: str
    message: str


class ValidationResult(BaseModel):
    document_type: DocumentType
    is_valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)


class ProcessResult(BaseModel):
    extraction: ExtractionResult
    validation: ValidationResult
