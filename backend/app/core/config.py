from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, sourced from environment variables / .env."""

    app_name: str = "Intelligent Document Extraction & Validation API"
    app_env: str = "development"
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"

    database_url: str = "sqlite:///./data/documents.db"

    max_pages: int = 3
    max_upload_size_mb: int = 15
    allowed_content_types: tuple[str, ...] = (
        "application/pdf",
        "image/jpeg",
        "image/png",
    )

    ocr_dpi: int = 200
    ocr_min_native_text_chars: int = 20
    # Mode 3 (fully automatic segmentation) recognises these statements best;
    # rows are rebuilt from word geometry afterwards, so its block ordering
    # does not matter. Mode 6 preserves reading order but drops bold totals.
    ocr_psm: int = 3
    ocr_min_confidence: int = 30
    # Below this mean word confidence a page is treated as possibly sideways and
    # re-read at other orientations (kept only if it measurably reads better).
    # Upright scans in the dataset score 75-90; a rotated one scored 58.
    ocr_min_mean_confidence: float = 70.0
    # Longest edge, in pixels, of the downscaled copy used to choose a rotation.
    ocr_orientation_probe_px: int = 800
    tesseract_cmd: str | None = None

    financial_tolerance_absolute: float = 1.0
    financial_tolerance_relative: float = 0.01

    # Optional LLM-assisted recovery of fields OCR could not resolve. Disabled
    # unless an API key is supplied; the pipeline is deterministic without it.
    # Gemini reads the rendered page image, which is what poor scans need: where
    # OCR dropped a row label the label is still plainly on the page. Anthropic
    # is a text-only fallback over the OCR output. Whichever key is set decides;
    # with neither, the pipeline is fully deterministic.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-flash-latest"
    # Tried in order when the configured model is at capacity. Free-tier models
    # return 503 "high demand" for minutes at a time, and a document should not
    # lose its recovery pass because one model is busy.
    gemini_fallback_models: tuple[str, ...] = ("gemini-flash-lite-latest", "gemini-3-flash-preview")
    llm_api_key: str | None = None
    llm_model: str = "claude-opus-5"
    # Enough headroom for every field of a statement, each with its own value
    # per reporting period and the source line it was read from; at 4096 the
    # answer was truncated mid-string and discarded as invalid JSON.
    llm_max_tokens: int = 16384
    llm_max_text_chars: int = 20000
    llm_timeout_seconds: int = 90
    llm_max_attempts: int = 3
    # Whole-pass budget across every retry and fallback model, so a busy free
    # tier cannot hold a request open indefinitely.
    llm_total_budget_seconds: int = 45
    # Longest edge of the page images sent to the vision model.
    vision_max_image_px: int = 1600

    upload_dir: str = "./data/uploads"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="IDEV_")


@lru_cache
def get_settings() -> Settings:
    return Settings()
