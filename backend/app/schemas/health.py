from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    app_env: str
    database: str
    ocr_engine: str
