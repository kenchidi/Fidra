from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    app_name: str = "Fidra"
    app_version: str = "0.1.0"
    debug: bool = False

    # Database. Use Postgres in production (DATABASE_URL); SQLite is for local dev only.
    database_url: str = "sqlite:///./fidra.db"

    # Admin key required to create institutions. Leave empty to disable that endpoint.
    admin_api_key: str = ""

    # Seeds demo institutions and prints their keys at startup. Local development only.
    seed_demo_data: bool = False

    # Comma-separated list of allowed browser origins, e.g. "https://app.example.com".
    cors_origins: str = "*"

    token_expire_days: int = 7

    # Signed reference forms uploaded by the requesting bank are deleted after this many days.
    signed_form_retention_days: int = 90

    # Staff dashboard sessions. Set SESSION_SECRET in production so logins survive restarts.
    session_secret: str = ""
    session_https_only: bool = False   # set true on Render (HTTPS)

    # Notifications (stubs for v1)
    sms_enabled: bool = False
    email_enabled: bool = False

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
