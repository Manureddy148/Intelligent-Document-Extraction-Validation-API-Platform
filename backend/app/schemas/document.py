import datetime

from pydantic import BaseModel


class DocumentSummary(BaseModel):
    document_name: str
    document_type: str
    processing_status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    total: int
    documents: list[DocumentSummary]
