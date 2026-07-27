from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UNICORE_", env_file=".env", extra="ignore")

    service_name: str = "unicore-api"
    environment: str = "dev"

    database_url: str = "postgresql+asyncpg://unicore:unicore@localhost:5432/unicore"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: list[str] = ["http://localhost:3000"]

    # AUTH locked decision #1: password + OTP. This toggle exists for dev/test
    # convenience ONLY (UNICORE_OTP_LOGIN_ENABLED=false) and cannot be turned
    # off in production — the app refuses to start (fail closed).
    otp_login_enabled: bool = True

    @model_validator(mode="after")
    def _otp_mandatory_in_production(self) -> "Settings":
        if self.environment == "production" and not self.otp_login_enabled:
            raise ValueError(
                "OTP login cannot be disabled in production (AUTH locked decision #1)."
            )
        return self

    @property
    def sync_database_url(self) -> str:
        # Alembic runs migrations over a sync driver against the same database.
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()
