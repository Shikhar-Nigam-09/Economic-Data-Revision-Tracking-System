"""Centralized application configuration using pydantic-settings."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from econ_monitor.core.errors import ConfigurationError


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database URLs (typed as SecretStr to prevent credential leakage)
    DATABASE_URL: SecretStr | None = Field(
        default=None,
        description="Main PostgreSQL connection URL using psycopg v3 driver",
    )
    TEST_DATABASE_URL: SecretStr | None = Field(
        default=None,
        description="Separate test database URL",
    )

    # Third-Party Secrets
    FRED_API_KEY: SecretStr | None = Field(
        default=None,
        description="FRED / ALFRED API key",
    )
    TELEGRAM_BOT_TOKEN: SecretStr | None = Field(
        default=None,
        description="Telegram Bot API Token",
    )
    TELEGRAM_CHAT_ID: str | None = Field(
        default=None,
        description="Default Telegram Chat ID for alerts",
    )

    # API Configuration
    API_HOST: str = Field(default="127.0.0.1", description="FastAPI bind address")
    API_PORT: int = Field(default=8000, description="FastAPI bind port")
    API_BASE_URL: str = Field(
        default="http://127.0.0.1:8000", description="Base URL for API clients"
    )

    # System Tuning & Thresholds
    NOTIFY_MAX_EVENT_AGE_DAYS: int = Field(
        default=30,
        description="Maximum age in days for notifying past events",
    )
    LOG_LEVEL: str = Field(default="INFO", description="Logging verbosity level")

    def require_database_url(self) -> str:
        """Return the unmasked DATABASE_URL or raise an actionable ConfigurationError."""
        if not self.DATABASE_URL or not self.DATABASE_URL.get_secret_value().strip():
            raise ConfigurationError(
                "DATABASE_URL is not configured. Please define DATABASE_URL in your .env file or "
                "environment (e.g., postgresql+psycopg://user:password@localhost:5432/econ_monitor)."
            )
        return self.DATABASE_URL.get_secret_value()

    def get_test_database_url(self) -> str | None:
        """Return the unmasked TEST_DATABASE_URL if configured."""
        if self.TEST_DATABASE_URL and self.TEST_DATABASE_URL.get_secret_value().strip():
            return self.TEST_DATABASE_URL.get_secret_value()
        return None

    def require_fred_api_key(self) -> str:
        """Return the unmasked FRED_API_KEY or raise an actionable ConfigurationError."""
        if not self.FRED_API_KEY or not self.FRED_API_KEY.get_secret_value().strip():
            raise ConfigurationError(
                "FRED_API_KEY is not configured. Please define FRED_API_KEY in your .env file or "
                "environment."
            )
        return self.FRED_API_KEY.get_secret_value()

    def require_telegram_bot_token(self) -> str:
        """Return the unmasked TELEGRAM_BOT_TOKEN or raise an actionable ConfigurationError."""
        if not self.TELEGRAM_BOT_TOKEN or not self.TELEGRAM_BOT_TOKEN.get_secret_value().strip():
            raise ConfigurationError(
                "TELEGRAM_BOT_TOKEN is not configured. Please define TELEGRAM_BOT_TOKEN "
                "in your .env file or environment."
            )
        return self.TELEGRAM_BOT_TOKEN.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    """Return a cached instance of application settings."""
    return Settings()
