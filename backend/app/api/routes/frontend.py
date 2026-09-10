from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings
from app.core.paths import FRONTEND_TEMPLATES_DIR

router = APIRouter(tags=["frontend"], include_in_schema=False)
templates = Jinja2Templates(directory=str(FRONTEND_TEMPLATES_DIR))


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "app_name": settings.app_name, "api_prefix": settings.api_v1_prefix},
    )


@router.get("/documents/{document_name}", response_class=HTMLResponse)
async def document_result(request: Request, document_name: str) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        "document_result.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "api_prefix": settings.api_v1_prefix,
            "document_name": document_name,
        },
    )
