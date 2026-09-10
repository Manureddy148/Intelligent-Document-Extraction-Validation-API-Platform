from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import ProcessedDocument


class DocumentRepository:
    """Persistence layer for processed-document results (upsert-by-name)."""

    def __init__(self, db: Session):
        self.db = db

    def upsert(self, document_name: str, document_type: str, processing_status: str, result_json: dict) -> ProcessedDocument:
        existing = self.get_by_name(document_name)
        if existing:
            existing.document_type = document_type
            existing.processing_status = processing_status
            existing.result_json = result_json
        else:
            existing = ProcessedDocument(
                document_name=document_name,
                document_type=document_type,
                processing_status=processing_status,
                result_json=result_json,
            )
            self.db.add(existing)
        self.db.commit()
        self.db.refresh(existing)
        return existing

    def get_by_name(self, document_name: str) -> ProcessedDocument | None:
        stmt = select(ProcessedDocument).where(ProcessedDocument.document_name == document_name)
        return self.db.execute(stmt).scalar_one_or_none()

    def list_all(self, limit: int = 100, offset: int = 0) -> tuple[list[ProcessedDocument], int]:
        total = self.db.execute(select(ProcessedDocument)).scalars().all()
        stmt = (
            select(ProcessedDocument)
            .order_by(ProcessedDocument.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = self.db.execute(stmt).scalars().all()
        return list(rows), len(total)
