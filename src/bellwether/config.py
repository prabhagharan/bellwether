from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    admin_username: str
    admin_password: str
    detect_model: str = "anthropic/claude-haiku-4-5"
    extract_model: str = "anthropic/claude-sonnet-5"
    relevance_threshold: float = 0.5
    worker_poll_interval_seconds: float = 5.0
    worker_stale_reclaim_seconds: float = 300.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
