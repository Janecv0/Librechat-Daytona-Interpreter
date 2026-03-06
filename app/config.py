from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ADAPTER_API_KEY: str
    DAYTONA_API_KEY: str
    DAYTONA_API_URL: str | None = None
    DAYTONA_SANDBOX_CPU: int = 1
    DAYTONA_SANDBOX_MEMORY: int = 1
    DAYTONA_SANDBOX_DISK: int = 3
    WORKSPACE_ROOT: str = "/workspace"
    REDIS_URL: str | None = None
    SESSION_TTL_SECONDS: int = 300
    CLEANUP_INTERVAL_SECONDS: int = 60
    UPLOAD_MAX_BYTES: int = 20 * 1024 * 1024
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
