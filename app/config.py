from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or a local .env file."""

    database_url: str = "postgresql+psycopg://nba:nba@localhost:5432/nba_injuries"
    scraper_user_agent: str = "nba-injury-database/0.1 (low-frequency research client)"
    scraper_timeout_seconds: float = Field(default=30.0, gt=0)
    scraper_request_interval_seconds: float = Field(default=2.0, ge=0)
    scraper_max_retries: int = Field(default=3, ge=0, le=10)
    scraper_backoff_base_seconds: float = Field(default=1.0, ge=0)
    scraper_max_pages: int = Field(default=2_000, gt=0)
    nba_pdf_user_agent: str = "nba-injury-database/0.1 (official NBA PDF research client)"
    nba_pdf_timeout_seconds: float = Field(default=30.0, gt=0)
    nba_pdf_request_interval_seconds: float = Field(default=1.0, ge=0)
    nba_pdf_max_retries: int = Field(default=3, ge=0, le=10)
    nba_pdf_backoff_base_seconds: float = Field(default=1.0, ge=0)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
