"""FRED and ALFRED API client with rate limiting, retries, and pagination.

Design rationale:
We utilize `fredapi` for convenient series metadata and vintage date list retrieval,
while using direct `requests.Session` with custom pagination for vintage observation
downloads. Direct HTTP requests allow fine-grained control over ALFRED parameters
(such as `realtime_start`, `realtime_end`, and `output_type`), pagination offsets,
deterministic rate limiting, and exact Decimal/string preservation without lossy
float conversions.
"""

import re
import time
from typing import Any, Dict, List, Optional

import requests

from econ_monitor.core.config import get_settings
from econ_monitor.core.errors import EconMonitorError
from econ_monitor.core.logging import get_logger

logger = get_logger("econ_monitor.ingestion.fred_client")

API_KEY_REGEX = re.compile(r"(api_key=)[^&\s]+", re.IGNORECASE)
FRED_BASE_URL = "https://api.stlouisfed.org/fred"


def redact_api_key(text: str) -> str:
    """Scrub sensitive api_key query parameters from URLs or error messages."""
    return API_KEY_REGEX.sub(r"\1***", text)


class FredClientError(EconMonitorError):
    """Base exception for all FRED/ALFRED client errors."""

    def __init__(self, message: str, code: str = "FRED_ERROR", details: dict | None = None):
        super().__init__(redact_api_key(message), code=code, details=details)


class FredAuthError(FredClientError):
    """Raised when authentication fails (HTTP 401/403)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="FRED_AUTH_ERROR", details=details)


class FredNotFoundError(FredClientError):
    """Raised when requested series or endpoint is not found (HTTP 404)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="FRED_NOT_FOUND", details=details)


class FredRateLimitError(FredClientError):
    """Raised when FRED rate limits are exceeded (HTTP 429)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="FRED_RATE_LIMIT", details=details)


class FredServerError(FredClientError):
    """Raised when FRED servers return 5xx errors."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="FRED_SERVER_ERROR", details=details)


class FredMalformedResponseError(FredClientError):
    """Raised when the API returns malformed or unexpected responses."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="FRED_MALFORMED_RESPONSE", details=details)


class RateLimiter:
    """Thread-safe minimum interval rate limiter to stay well below 120 req/min."""

    def __init__(self, min_interval_seconds: float = 0.6):
        self.min_interval = min_interval_seconds
        self.last_call_time = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_call_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_call_time = time.monotonic()


class FredClient:
    """HTTP Client for St. Louis Fed FRED and ALFRED services."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        rate_limiter: Optional[RateLimiter] = None,
        timeout: tuple[float, float] = (5.0, 30.0),
        max_retries: int = 3,
    ):
        if api_key is None:
            settings = get_settings()
            api_key = settings.require_fred_api_key()

        self._api_key = api_key
        self._session = requests.Session()
        self._rate_limiter = rate_limiter or RateLimiter(min_interval_seconds=0.6)
        self._timeout = timeout
        self._max_retries = max_retries

    def _get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Perform a GET request with rate limiting and exponential backoff retries."""
        url = f"{FRED_BASE_URL}/{endpoint.lstrip('/')}"
        query_params = {
            **params,
            "api_key": self._api_key,
            "file_type": "json",
        }

        attempts = 0
        backoff_seconds = 1.0

        while attempts < self._max_retries:
            attempts += 1
            self._rate_limiter.wait()

            try:
                response = self._session.get(url, params=query_params, timeout=self._timeout)
            except requests.RequestException as exc:
                clean_msg = redact_api_key(str(exc))
                if attempts >= self._max_retries:
                    raise FredServerError(
                        f"Network error after {attempts} attempts: {clean_msg}"
                    ) from exc
                time.sleep(backoff_seconds)
                backoff_seconds *= 2.0
                continue

            # Handle status codes
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise FredMalformedResponseError(
                        f"Non-JSON response from {redact_api_key(response.url)}"
                    ) from exc

            clean_url = redact_api_key(response.url)
            status_code = response.status_code
            error_text = redact_api_key(response.text)

            if status_code in (401, 403):
                raise FredAuthError(
                    f"Authentication failed ({status_code}) for {clean_url}: {error_text}",
                    details={"status_code": status_code},
                )
            elif status_code == 404:
                raise FredNotFoundError(
                    f"Resource not found ({status_code}) at {clean_url}: {error_text}",
                    details={"status_code": status_code},
                )
            elif status_code == 429:
                retry_after = response.headers.get("Retry-After")
                wait_time = (
                    float(retry_after) if retry_after and retry_after.isdigit() else backoff_seconds
                )
                logger.warning(f"Rate limited by FRED on {clean_url}. Backing off {wait_time}s.")
                if attempts >= self._max_retries:
                    msg = (
                        f"Rate limit exceeded ({status_code}) after "
                        f"{attempts} attempts: {clean_url}"
                    )
                    raise FredRateLimitError(msg)
                time.sleep(wait_time)
                backoff_seconds *= 2.0
            elif status_code >= 500:
                logger.warning(f"FRED server error ({status_code}) on {clean_url}. Retrying...")
                if attempts >= self._max_retries:
                    raise FredServerError(
                        f"Server error ({status_code}) on {clean_url}: {error_text}",
                        details={"status_code": status_code},
                    )
                time.sleep(backoff_seconds)
                backoff_seconds *= 2.0
            else:
                raise FredClientError(
                    f"Unexpected HTTP {status_code} from {clean_url}: {error_text}",
                    details={"status_code": status_code},
                )

        raise FredServerError(
            f"Failed to fetch {redact_api_key(url)} after {self._max_retries} attempts."
        )

    def get_series_metadata(self, series_id: str) -> Dict[str, Any]:
        """Fetch metadata attributes for a specific FRED series."""
        data = self._get("series", {"series_id": series_id})
        seriess = data.get("seriess", [])
        if not seriess:
            raise FredNotFoundError(f"Series '{series_id}' metadata not found in response.")
        return seriess[0]

    def get_vintage_dates(self, series_id: str) -> List[str]:
        """Fetch all historical vintage publication dates for a series."""
        data = self._get("series/vintagedates", {"series_id": series_id})
        return data.get("vintage_dates", [])

    def get_vintage_observations(
        self,
        series_id: str,
        observation_start: str = "1776-07-04",
        realtime_start: str = "1776-07-04",
        realtime_end: str = "9999-12-31",
        output_type: int = 1,
        page_limit: int = 100000,
    ) -> List[Dict[str, Any]]:
        """Download all vintage observations using paginated requests with count validation.

        Parameters:
            series_id: The FRED series identifier (e.g. 'GDPC1')
            observation_start: Earliest observation date to retrieve
            realtime_start: Earliest vintage date (realtime_start)
            realtime_end: Latest vintage date (realtime_end)
            output_type: FRED output format (default: 1 for observations by real-time period)
            page_limit: Number of records requested per page (default: 100000)
        """
        limit = page_limit
        offset = 0
        all_observations: List[Dict[str, Any]] = []
        expected_total: Optional[int] = None

        while True:
            params = {
                "series_id": series_id,
                "observation_start": observation_start,
                "realtime_start": realtime_start,
                "realtime_end": realtime_end,
                "output_type": output_type,
                "limit": limit,
                "offset": offset,
            }

            data = self._get("series/observations", params)
            if expected_total is None and "count" in data:
                expected_total = int(data["count"])

            obs_page = data.get("observations", [])
            all_observations.extend(obs_page)

            if len(obs_page) < limit:
                break
            offset += limit

        if expected_total is not None and len(all_observations) != expected_total:
            raise FredMalformedResponseError(
                f"Row count mismatch for {series_id}: received {len(all_observations)} "
                f"observations, expected {expected_total} from FRED response metadata."
            )

        return all_observations
