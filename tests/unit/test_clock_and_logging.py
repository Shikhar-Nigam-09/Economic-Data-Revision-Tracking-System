"""Unit tests for system clock and sensitive logging redaction."""

import logging
from datetime import UTC, datetime

from econ_monitor.core.clock import get_utc_now, get_utc_today, set_custom_now
from econ_monitor.core.logging import RedactingFormatter


def test_clock_utc_and_freeze():
    """Verify UTC awareness and provider freezing."""
    now = get_utc_now()
    assert now.tzinfo is not None
    assert now.tzinfo == UTC

    # Freeze time
    frozen_dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC)
    set_custom_now(lambda: frozen_dt)
    try:
        assert get_utc_now() == frozen_dt
        assert get_utc_today() == frozen_dt.date()
    finally:
        set_custom_now(None)


def test_logging_redaction():
    """Verify that credentials and tokens are redacted from log outputs."""
    formatter = RedactingFormatter("%(message)s")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=(
            "Connecting to postgresql+psycopg://myuser:supersecretpass@localhost:5432/econ_monitor "
            "with api_key=abc123xyz and bot123456:SECRET_TELEGRAM_TOKEN"
        ),
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    assert "supersecretpass" not in formatted
    assert "abc123xyz" not in formatted
    assert "SECRET_TELEGRAM_TOKEN" not in formatted
    assert "postgresql+psycopg://myuser:***@localhost:5432/econ_monitor" in formatted
    assert "api_key=***" in formatted
    assert "bot123456:***" in formatted
