"""Environment-based configuration. Every setting can be overridden by an environment variable."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(BACKEND_DIR / ".env",), extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://scanner:scanner@localhost:5432/scanner"

    # Gemini (server-side only)
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_thinking_level: Literal["minimal", "low", "medium", "high", "off"] = "low"
    model_timeout_seconds: float = Field(default=90, ge=5, le=300)
    model_max_attempts: int = Field(default=3, ge=1, le=5)
    extraction_passes: int = Field(default=1, ge=1, le=2)
    daily_model_call_limit: int = Field(default=500, ge=0)

    # Browser access
    frontend_origin: str = "http://localhost:5173"
    demo_access_code: str | None = None

    # Uploads and storage
    storage_backend: Literal["database", "local"] = "database"
    upload_dir: Path = BACKEND_DIR / "data" / "uploads"
    max_upload_mb: int = Field(default=10, ge=1, le=20)
    max_pdf_pages: int = Field(default=5, ge=1, le=20)
    uploads_per_hour_per_client: int = Field(default=30, ge=1)

    # Processing
    worker_mode: Literal["inline", "off"] = "inline"
    worker_poll_seconds: float = Field(default=2.0, ge=0.2, le=60)
    job_lease_seconds: int = Field(default=300, ge=30)
    max_job_attempts: int = Field(default=3, ge=1, le=10)
    auto_approve_clean_receipts: bool = False
    receipt_max_age_days: int = Field(default=730, ge=1)

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, value: str) -> str:
        # Managed hosts hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the psycopg driver name.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix):]
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.frontend_origin.split(",") if origin.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
