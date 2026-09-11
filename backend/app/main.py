import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import documents, frontend, health
from app.core.config import get_settings
from app.core.database import init_db
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger, request_id_var
from app.core.paths import FRONTEND_STATIC_DIR

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("%s started (env=%s)", settings.app_name, settings.app_env)
    yield


app = FastAPI(
    title=settings.app_name,
    description=(
        "Accepts invoice / balance sheet / profit & loss / cash flow statement documents "
        "(PDF, JPG, PNG), validates them, extracts structured fields via OCR + rule-based "
        "parsing, runs financial reconciliation checks, and persists results."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Tag every log line from this request with one id, and time the request."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    duration_ms = int((time.perf_counter() - started) * 1000)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "%s %s -> %s in %sms", request.method, request.url.path, response.status_code, duration_ms
    )
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.warning("Handled application error: %s - %s", exc.code, exc.message)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled server error while processing %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred while processing the request."}},
    )


app.include_router(health.router, prefix=settings.api_v1_prefix)
app.include_router(documents.router, prefix=settings.api_v1_prefix)
app.include_router(frontend.router)

if FRONTEND_STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_STATIC_DIR)), name="static")
