from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET_KEY = "dev-only-change-me-dev-only-change-me"

class Settings(BaseSettings):
    """All configuration comes from the environment (prefix AGENTLINK_) or .env file."""

    model_config = SettingsConfigDict(env_prefix="AGENTLINK_", env_file=".env", extra="ignore")

    env: Literal["dev", "staging", "prod"] = "dev"
    database_url: str = "sqlite+pysqlite:///agentlink.db"
    bus:Literal["memory", "kafka"] = "memory"
    kafka_bootstrap : str = "localhost:9092"
    kafka_group_prefix : str = "agentlink"
    kafka_partition : int = 3
    jwt_secret: str = DEV_SECRET
    jwt_ttl_minutes: int = 60
    max_retries: int = 3
    retry_backoff_seconds: float = 0.5
    window_sweep_seconds: int = 60
    max_reading_age_days: int = 60

    def assert_production_safe(self) -> None:
        if self.env != "dev" and (self.jwt_secret == DEV_SECRET or len(self.jwt_secret) < 32):
            raise RuntimeError(
                "AGENTLINK_JWT_SECRET must be set to a random value of at least 32 characters "
                "outside the dev environment"
            )
