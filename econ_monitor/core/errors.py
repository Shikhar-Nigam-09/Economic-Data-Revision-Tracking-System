"""Core typed exceptions for the Economic Data Monitoring system."""


class EconMonitorError(Exception):
    """Base class for all system errors."""

    def __init__(self, message: str, code: str = "SYSTEM_ERROR", details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class ConfigurationError(EconMonitorError):
    """Raised when application configuration or environment variables are missing or invalid."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="CONFIG_ERROR", details=details)


class DatabaseError(EconMonitorError):
    """Raised when database operations fail."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="DATABASE_ERROR", details=details)


class SeriesNotFoundError(EconMonitorError):
    """Raised when a requested economic series code is not found."""

    def __init__(self, series_code: str):
        super().__init__(
            f"Series '{series_code}' not found.",
            code="SERIES_NOT_FOUND",
            details={"code": series_code},
        )
