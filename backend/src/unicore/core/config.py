from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UNICORE_", env_file=".env", extra="ignore")

    service_name: str = "unicore-api"
    environment: str = "dev"

    database_url: str = "postgresql+asyncpg://unicore:unicore@localhost:5432/unicore"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: list[str] = ["http://localhost:3000"]

    @property
    def sync_database_url(self) -> str:
        # Alembic runs migrations over a sync driver against the same database.
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()
