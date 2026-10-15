from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    app_name: str = "Fidra"
    app_version: str = "0.1.0"
    debug: bool = False

    # Database
    database_url: str = "sqlite:///./fidra.db"

    # Security
    secret_key: str = "change-me-in-production-fidra-dev-secret-key-2026"
    token_expire_days: int = 7

    # Notifications (stubs for v1)
    sms_enabled: bool = False
    email_enabled: bool = False

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
