"""Centralized logging configuration with automatic secrets redaction."""

import logging
import re
import sys

# Regex patterns for redacting sensitive values from log messages
SENSITIVE_PATTERNS = [
    re.compile(r"(api_key=)[^&\s]+", re.IGNORECASE),
    re.compile(r"(bot\d+:)[^/\s]+", re.IGNORECASE),
    re.compile(r"(postgresql(?:\+psycopg)?:\/\/[^:]+:)[^@]+(@)", re.IGNORECASE),
    re.compile(r"(password=)[^\s&]+", re.IGNORECASE),
    re.compile(r"(token=)[^\s&]+", re.IGNORECASE),
]


class RedactingFormatter(logging.Formatter):
    """Custom formatter that scrubs passwords, API keys, and tokens from records."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        redacted = original
        for pattern in SENSITIVE_PATTERNS:
            if "postgresql" in pattern.pattern:
                redacted = pattern.sub(r"\1***\2", redacted)
            else:
                redacted = pattern.sub(r"\1***", redacted)
        return redacted


def setup_logging(level: str = "INFO") -> None:
    """Configure system-wide logging with redaction formatting."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Avoid duplicate handlers on re-initialization
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(log_level)
        formatter = RedactingFormatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            handler.setLevel(log_level)
            formatter = RedactingFormatter(
                fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            handler.setFormatter(formatter)


def get_logger(name: str) -> logging.Logger:
    """Return a logger configured under the application namespace."""
    return logging.getLogger(name)
