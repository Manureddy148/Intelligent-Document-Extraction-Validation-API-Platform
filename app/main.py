from fastapi import FastAPI

from app.api.routes import documents, health
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name)

app.include_router(health.router)
app.include_router(documents.router, prefix=settings.api_v1_prefix)
