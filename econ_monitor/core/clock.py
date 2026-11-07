"""System clock provider for deterministic and testable time operations."""

from collections.abc import Callable
from datetime import UTC, date, datetime

_custom_now_provider: Callable[[], datetime] | None = None


def set_custom_now(provider: Callable[[], datetime] | None) -> None:
    """Set a custom datetime provider function (useful for tests)."""
    global _custom_now_provider
    _custom_now_provider = provider


def get_utc_now() -> datetime:
    """Return the current datetime in UTC timezone."""
    if _custom_now_provider is not None:
        dt = _custom_now_provider()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    return datetime.now(UTC)


def get_utc_today() -> date:
    """Return the current date in UTC."""
    return get_utc_now().date()
