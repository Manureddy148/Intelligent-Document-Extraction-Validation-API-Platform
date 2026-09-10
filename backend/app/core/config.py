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
    tesseract_cmd: str | None = None

    financial_tolerance_absolute: float = 1.0
    financial_tolerance_relative: float = 0.01

    # Optional: if set, the extraction service may use an LLM to assist field
    # extraction. When absent, a deterministic rule-based extractor is used.
    llm_api_key: str | None = None
    llm_provider: str | None = None

    upload_dir: str = "./data/uploads"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="IDEV_")


@lru_cache
def get_settings() -> Settings:
    return Settings()
