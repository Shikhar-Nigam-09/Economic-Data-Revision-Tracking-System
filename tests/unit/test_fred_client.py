"""Unit tests for FredClient using mocked HTTP responses."""

import pytest
import responses

from econ_monitor.ingestion.fred_client import (
    FRED_BASE_URL,
    FredAuthError,
    FredClient,
    FredNotFoundError,
    FredRateLimitError,
    RateLimiter,
    redact_api_key,
)


@pytest.fixture
def fast_limiter():
    """Rate limiter with 0 interval for fast unit tests."""
    return RateLimiter(min_interval_seconds=0.0)


@pytest.fixture
def mock_client(fast_limiter):
    """FredClient instance configured with synthetic API key."""
    return FredClient(
        api_key="synthetic_secret_fred_key_12345",
        rate_limiter=fast_limiter,
        max_retries=2,
    )


def test_api_key_redaction():
    """Verify that sensitive api_key strings are redacted from URLs and errors."""
    raw = "https://api.stlouisfed.org/fred/series?api_key=my_secret_key&file_type=json"
    redacted = redact_api_key(raw)
    assert "my_secret_key" not in redacted
    assert "api_key=***" in redacted


@responses.activate
def test_get_series_metadata_success(mock_client):
    """Verify metadata retrieval for a series."""
    responses.add(
        responses.GET,
        f"{FRED_BASE_URL}/series",
        json={
            "seriess": [
                {
                    "id": "GDPC1",
                    "title": "Real Gross Domestic Product",
                    "frequency_short": "Q",
                    "units": "Billions of Chained 2017 Dollars",
                    "seasonal_adjustment": "Seasonally Adjusted Annual Rate",
                    "last_updated": "2024-03-28 07:31:02-05",
                }
            ]
        },
        status=200,
    )
    meta = mock_client.get_series_metadata("GDPC1")
    assert meta["id"] == "GDPC1"
    assert meta["frequency_short"] == "Q"


@responses.activate
def test_auth_error_raised_and_redacted(mock_client):
    """Verify 401 returns FredAuthError and redacts API key."""
    responses.add(
        responses.GET,
        f"{FRED_BASE_URL}/series",
        body="Bad API key",
        status=401,
    )
    with pytest.raises(FredAuthError) as exc_info:
        mock_client.get_series_metadata("GDPC1")

    assert "synthetic_secret_fred_key_12345" not in str(exc_info.value)
    assert "api_key=***" in str(exc_info.value)


@responses.activate
def test_not_found_error(mock_client):
    """Verify 404 returns FredNotFoundError."""
    responses.add(
        responses.GET,
        f"{FRED_BASE_URL}/series",
        status=404,
    )
    with pytest.raises(FredNotFoundError):
        mock_client.get_series_metadata("UNKNOWN_SERIES")


@responses.activate
def test_retry_on_500_and_succeed(mock_client):
    """Verify transient 500 error retries and succeeds."""
    responses.add(
        responses.GET,
        f"{FRED_BASE_URL}/series/vintagedates",
        status=500,
    )
    responses.add(
        responses.GET,
        f"{FRED_BASE_URL}/series/vintagedates",
        json={"vintage_dates": ["2020-01-01", "2020-02-01"]},
        status=200,
    )
    dates = mock_client.get_vintage_dates("GDPC1")
    assert len(dates) == 2
    assert len(responses.calls) == 2


@responses.activate
def test_rate_limit_retry_and_fail(mock_client):
    """Verify 429 retries up to max_retries and raises FredRateLimitError."""
    responses.add(
        responses.GET,
        f"{FRED_BASE_URL}/series",
        status=429,
        headers={"Retry-After": "0"},
    )
    responses.add(
        responses.GET,
        f"{FRED_BASE_URL}/series",
        status=429,
        headers={"Retry-After": "0"},
    )
    with pytest.raises(FredRateLimitError):
        mock_client.get_series_metadata("GDPC1")


@responses.activate
def test_pagination_and_count_validation(mock_client):
    """Verify that get_vintage_observations requests multiple pages and verifies counts."""
    # Page 1: returns 2 observations (offset 0)
    responses.add(
        responses.GET,
        f"{FRED_BASE_URL}/series/observations",
        json={
            "count": 3,
            "observations": [
                {"date": "2020-01-01", "realtime_start": "2020-02-01", "value": "100.0"},
                {"date": "2020-01-01", "realtime_start": "2020-03-01", "value": "102.0"},
            ],
        },
        status=200,
    )

    # Page 2: returns 1 observation (offset 2)
    responses.add(
        responses.GET,
        f"{FRED_BASE_URL}/series/observations",
        json={
            "count": 3,
            "observations": [
                {"date": "2020-04-01", "realtime_start": "2020-05-01", "value": "105.0"},
            ],
        },
        status=200,
    )

    data = mock_client.get_vintage_observations("GDPC1", page_limit=2)
    assert len(data) == 3
    assert len(responses.calls) == 2
