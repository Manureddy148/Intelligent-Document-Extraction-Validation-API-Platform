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
    tesseract_cmd: str | None = None

    financial_tolerance_absolute: float = 1.0
    financial_tolerance_relative: float = 0.01

    # Optional LLM-assisted recovery of fields OCR could not resolve. Disabled
    # unless an API key is supplied; the pipeline is deterministic without it.
    llm_api_key: str | None = None
    llm_model: str = "claude-opus-5"
    llm_max_tokens: int = 4096
    llm_max_text_chars: int = 20000

    upload_dir: str = "./data/uploads"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="IDEV_")


@lru_cache
def get_settings() -> Settings:
    return Settings()
