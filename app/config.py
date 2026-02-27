from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ADAPTER_API_KEY: str
    DAYTONA_API_KEY: str
    DAYTONA_API_URL: str | None = None
    REDIS_URL: str | None = None
    SESSION_TTL_SECONDS: int = 1800
    CLEANUP_INTERVAL_SECONDS: int = 60
    UPLOAD_MAX_BYTES: int = 20 * 1024 * 1024

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

