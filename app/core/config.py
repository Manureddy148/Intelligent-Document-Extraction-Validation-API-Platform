from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Intelligent Document Extraction & Validation API"
    api_v1_prefix: str = "/api/v1"
    max_upload_size_mb: int = 10
    allowed_content_types: tuple[str, ...] = (
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "text/plain",
    )

    model_config = SettingsConfigDict(env_file=".env", env_prefix="IDEV_")


@lru_cache
def get_settings() -> Settings:
    return Settings()
