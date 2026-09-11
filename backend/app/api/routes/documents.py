from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import DocumentNotFoundError
from app.core.logging import get_logger
from app.schemas.document import DocumentListResponse, DocumentSummary
from app.schemas.extraction import DocumentType, ErrorResponse, ProcessingResult
from app.services.document_service import DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])
logger = get_logger(__name__)

# Declared on the routes so Swagger shows callers exactly which controlled
# error envelopes they can receive, not just the success case.
_UPLOAD_ERRORS = {
    400: {"model": ErrorResponse, "description": "Empty, corrupted, or over the page limit"},
    413: {"model": ErrorResponse, "description": "File exceeds the maximum upload size"},
    415: {"model": ErrorResponse, "description": "Not a PDF / JPG / PNG document"},
    422: {"model": ErrorResponse, "description": "Text could not be extracted from the document"},
}


@router.post(
    "/process",
    response_model=ProcessingResult,
    responses=_UPLOAD_ERRORS,
    summary="Upload and process a document",
    description=(
        "Accepts a PDF / JPG / PNG of up to 3 pages as multipart form data. The file is "
        "validated, read (native PDF text or OCR), parsed into structured fields and line "
        "items, reconciled against the financial checks for its type, and stored. Returns "
        "the complete structured result."
    ),
)
async def process_document(
    file: UploadFile = File(..., description="The document to process (PDF, JPG or PNG)."),
    document_type: DocumentType = Form(..., description="Which schema to extract against."),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ProcessingResult:
    content = await file.read()
    service = DocumentService(db, settings)
    return service.process(filename=file.filename or "document", content=content, document_type=document_type)


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List processed documents",
    description="Most recently processed first. Backs the dashboard table.",
)
async def list_documents(
    limit: int = Query(100, ge=1, le=500, description="Maximum records to return."),
    offset: int = Query(0, ge=0, description="Records to skip, for paging."),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DocumentListResponse:
    service = DocumentService(db, settings)
    records, total = service.list_documents(limit=limit, offset=offset)
    return DocumentListResponse(
        total=total,
        documents=[DocumentSummary.model_validate(record) for record in records],
    )


@router.get(
    "/{document_name}",
    response_model=ProcessingResult,
    responses={404: {"model": ErrorResponse, "description": "No result stored under that name"}},
    summary="Retrieve the latest result by document name",
    description="Returns the most recent stored result for this file name.",
)
async def get_document(
    document_name: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ProcessingResult:
    service = DocumentService(db, settings)
    result = service.get_by_name(document_name)
    if result is None:
        raise DocumentNotFoundError(f"No processed result found for document '{document_name}'.")
    return result
