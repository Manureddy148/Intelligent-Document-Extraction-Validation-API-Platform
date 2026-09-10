from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import DocumentNotFoundError
from app.core.logging import get_logger
from app.schemas.document import DocumentListResponse, DocumentSummary
from app.schemas.extraction import DocumentType
from app.services.document_service import DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])
logger = get_logger(__name__)


@router.post("/process")
async def process_document(
    file: UploadFile = File(...),
    document_type: DocumentType = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    content = await file.read()
    service = DocumentService(db, settings)
    return service.process(filename=file.filename or "document", content=content, document_type=document_type)


@router.get("")
async def list_documents(
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DocumentListResponse:
    service = DocumentService(db, settings)
    records, total = service.list_documents(limit=limit, offset=offset)
    return DocumentListResponse(
        total=total,
        documents=[DocumentSummary.model_validate(record) for record in records],
    )


@router.get("/{document_name}")
async def get_document(
    document_name: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    service = DocumentService(db, settings)
    result = service.get_by_name(document_name)
    if result is None:
        raise DocumentNotFoundError(f"No processed result found for document '{document_name}'.")
    return result
