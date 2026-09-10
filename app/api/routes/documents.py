from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.core.config import Settings, get_settings
from app.models.schemas import (
    DocumentType,
    ExtractionResult,
    ProcessResult,
    ValidationRequest,
    ValidationResult,
)
from app.services.extraction import DocumentExtractor
from app.services.validation import DocumentValidator

router = APIRouter(prefix="/documents", tags=["documents"])

_extractor = DocumentExtractor()
_validator = DocumentValidator()


def _ensure_supported(upload: UploadFile, settings: Settings) -> None:
    if upload.content_type not in settings.allowed_content_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported content type: {upload.content_type}",
        )


@router.post("/extract", response_model=ExtractionResult)
async def extract_document(
    file: UploadFile = File(...),
    document_type: DocumentType = Form(DocumentType.GENERIC),
    settings: Settings = Depends(get_settings),
) -> ExtractionResult:
    _ensure_supported(file, settings)
    return await _extractor.process(file, document_type)


@router.post("/validate", response_model=ValidationResult)
async def validate_document(payload: ValidationRequest) -> ValidationResult:
    return _validator.validate(payload.document_type, payload.fields)


@router.post("/process", response_model=ProcessResult)
async def process_document(
    file: UploadFile = File(...),
    document_type: DocumentType = Form(DocumentType.GENERIC),
    settings: Settings = Depends(get_settings),
) -> ProcessResult:
    _ensure_supported(file, settings)
    extraction = await _extractor.process(file, document_type)
    fields = {field.name: field.value for field in extraction.fields}
    validation = _validator.validate(document_type, fields)
    return ProcessResult(extraction=extraction, validation=validation)
