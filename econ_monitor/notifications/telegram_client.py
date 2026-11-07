"""Direct HTTP client for the Telegram Bot API with token redaction, HTML escaping, and retries."""

import html
import re
import time
from typing import Any, Dict, List, Optional

import requests

from econ_monitor.core.errors import EconMonitorError
from econ_monitor.core.logging import get_logger

logger = get_logger("econ_monitor.notifications.telegram_client")

TELEGRAM_MAX_MESSAGE_LENGTH = 4096
TOKEN_REDACTION_PATTERN = re.compile(r"bot[0-9]+:[A-Za-z0-9_-]+")


class TelegramError(EconMonitorError):
    """Base exception for Telegram Bot API communication errors."""


class TelegramUserBlockedError(TelegramError):
    """Raised when Telegram returns HTTP 403 Forbidden (bot was blocked by recipient)."""


class TelegramRateLimitError(TelegramError):
    """Raised when Telegram returns HTTP 429 and retry attempts are exhausted."""


def redact_telegram_token(text: str) -> str:
    """Redact bot token patterns from URLs, logs, or error strings."""
    return TOKEN_REDACTION_PATTERN.sub("bot<REDACTED_TELEGRAM_TOKEN>", text)


def escape_html(text: str) -> str:
    """Escape text for safe HTML parse mode in Telegram."""
    return html.escape(text, quote=False)


def split_message_chunks(text: str, max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> List[str]:
    """Split a long message cleanly into chunks within Telegram's max length."""
    if len(text) <= max_length:
        return [text]

    chunks: List[str] = []
    lines = text.split("\n")
    current_chunk = ""

    for line in lines:
        line_to_add = line if not current_chunk else "\n" + line
        if len(current_chunk) + len(line_to_add) <= max_length:
            current_chunk += line_to_add
        else:
            if current_chunk:
                chunks.append(current_chunk)
            if len(line) <= max_length:
                current_chunk = line
            else:
                # Line itself exceeds max_length; split arbitrarily
                for i in range(0, len(line), max_length):
                    chunks.append(line[i : i + max_length])
                current_chunk = ""

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


class TelegramClient:
    """Direct HTTP wrapper over Telegram Bot API without third-party frameworks."""

    def __init__(
        self,
        bot_token: str,
        base_url: str = "https://api.telegram.org",
        session: Optional[requests.Session] = None,
        timeout: float = 15.0,
        max_retries: int = 3,
    ) -> None:
        if not bot_token or not bot_token.strip():
            raise TelegramError("Telegram bot token cannot be empty.")

        self._token = bot_token.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = session or requests.Session()

    @property
    def bot_url(self) -> str:
        """Construct the base URL for bot calls."""
        return f"{self._base_url}/bot{self._token}"

    def _request(
        self,
        method: str,
        endpoint: str,
        json_payload: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Execute an HTTP request against Telegram API with bounded retries and token redaction."""
        url = f"{self.bot_url}/{endpoint}"
        t_out = timeout if timeout is not None else self._timeout
        attempt = 0

        while attempt < self._max_retries:
            attempt += 1
            try:
                resp = self._session.request(
                    method=method,
                    url=url,
                    json=json_payload,
                    timeout=t_out,
                )

                # Check for 403 Forbidden (subscriber blocked bot)
                if resp.status_code == 403:
                    safe_url = redact_telegram_token(url)
                    logger.warning(f"Telegram blocked (403) for request to {safe_url}: {resp.text}")
                    raise TelegramUserBlockedError(
                        f"Recipient has blocked the bot (HTTP 403): {resp.text}"
                    )

                # Check for 429 Rate Limit
                if resp.status_code == 429:
                    try:
                        data = resp.json()
                        retry_after = float(data.get("parameters", {}).get("retry_after", 1.0))
                    except Exception:
                        retry_after = 1.0

                    safe_url = redact_telegram_token(url)
                    logger.warning(
                        f"Telegram rate limited (429) on {safe_url}. "
                        f"Sleeping {retry_after}s (attempt {attempt}/{self._max_retries})"
                    )
                    if attempt >= self._max_retries:
                        raise TelegramRateLimitError(
                            f"Telegram rate limit retry attempts exhausted: {resp.text}"
                        )
                    time.sleep(retry_after)
                    continue

                # Server errors: retry on 5xx
                if 500 <= resp.status_code < 600:
                    safe_url = redact_telegram_token(url)
                    logger.warning(
                        f"Telegram 5xx error ({resp.status_code}) on {safe_url}. "
                        f"Attempt {attempt}/{self._max_retries}"
                    )
                    if attempt >= self._max_retries:
                        resp.raise_for_status()
                    time.sleep(0.5 * (2 ** (attempt - 1)))
                    continue

                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    desc = data.get("description", "Unknown Telegram error")
                    raise TelegramError(f"Telegram API responded with ok=false: {desc}")
                return data

            except (requests.Timeout, requests.ConnectionError) as exc:
                safe_url = redact_telegram_token(url)
                logger.warning(
                    f"Telegram network error on {safe_url}: {exc}. "
                    f"Attempt {attempt}/{self._max_retries}"
                )
                if attempt >= self._max_retries:
                    raise TelegramError(f"Network error contacting Telegram: {exc}") from exc
                time.sleep(0.5 * (2 ** (attempt - 1)))
            except TelegramError:
                raise
            except Exception as exc:
                safe_url = redact_telegram_token(url)
                err_msg = redact_telegram_token(str(exc))
                raise TelegramError(
                    f"Unexpected error communicating with Telegram ({safe_url}): {err_msg}"
                ) from exc

        raise TelegramError("Max retries exceeded communicating with Telegram API.")

    def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
    ) -> List[int]:
        """Send a message to a Telegram chat, automatically chunking if > 4096 characters.

        Returns: List of Telegram message_ids for sent chunks.
        """
        chunks = split_message_chunks(text)
        message_ids: List[int] = []

        for chunk in chunks:
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
                "disable_web_page_preview": disable_web_page_preview,
            }
            res = self._request("POST", "sendMessage", json_payload=payload)
            msg_id = res.get("result", {}).get("message_id")
            if msg_id:
                message_ids.append(int(msg_id))

        return message_ids

    def get_updates(
        self,
        offset: Optional[int] = None,
        limit: int = 100,
        timeout: int = 30,
    ) -> List[Dict[str, Any]]:
        """Long poll for incoming updates from Telegram subscribers."""
        payload: Dict[str, Any] = {"limit": limit, "timeout": timeout}
        if offset is not None:
            payload["offset"] = offset

        res = self._request("POST", "getUpdates", json_payload=payload, timeout=timeout + 10.0)
        return list(res.get("result", []))
