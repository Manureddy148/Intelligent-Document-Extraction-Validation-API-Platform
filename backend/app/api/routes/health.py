from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.schemas.health import HealthResponse
from app.services.ocr_service import tesseract_version

router = APIRouter(tags=["health"])
logger = get_logger(__name__)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description=(
        "Reports whether the service can reach its database and its OCR engine. "
        "Returns 503 when a dependency the pipeline needs is unavailable, so a "
        "platform health probe fails the instance instead of routing traffic to it."
    ),
)
async def health_check(
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    try:
        db.execute(text("SELECT 1"))
        database_ok = True
    except Exception:
        # A health endpoint that reports "ok" while the database is unreachable
        # is worse than none: it keeps a broken instance in the load balancer.
        logger.exception("Health check could not reach the database")
        database_ok = False

    ocr_version = tesseract_version()
    status = "ok" if database_ok else "degraded"
    if not database_ok:
        response.status_code = 503

    return HealthResponse(
        status=status,
        app_env=settings.app_env,
        database="ok" if database_ok else "unavailable",
        ocr_engine=f"tesseract {ocr_version}" if ocr_version else "unavailable",
    )
