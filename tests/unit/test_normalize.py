"""Unit tests for observation normalization, missing value handling, and duplicate collapse."""

from datetime import date
from decimal import Decimal

from econ_monitor.ingestion.normalize import normalize_observations


def test_missing_dot_handling():
    """Verify that '.' values are mapped to None with is_missing=True."""
    raw = [
        {"date": "2020-01-01", "realtime_start": "2020-02-01", "value": "."},
        {"date": "2020-04-01", "realtime_start": "2020-05-01", "value": " 123.45 "},
    ]
    res = normalize_observations(raw, "TEST_SERIES", history_start=date(2000, 1, 1))
    assert len(res.df) == 2
    assert len(res.rejected) == 0

    row_missing = res.df.iloc[0]
    assert bool(row_missing["is_missing"]) is True

    row_valid = res.df.iloc[1]
    assert row_valid["value"] == Decimal("123.45")
    assert bool(row_valid["is_missing"]) is False


def test_invalid_dates_and_numerics_recorded_in_rejected():
    """Verify that invalid dates and unparseable values are captured in rejected list."""
    raw = [
        {"date": "not-a-date", "realtime_start": "2020-02-01", "value": "100.0"},
        {"date": "2020-01-01", "realtime_start": "bad-vintage-date", "value": "100.0"},
        {"date": "2020-05-01", "realtime_start": "2020-01-01", "value": "100.0"},  # vintage < obs
        {"date": "2020-01-01", "realtime_start": "2020-02-01", "value": "corrupt_number_xyz"},
    ]
    res = normalize_observations(raw, "TEST_SERIES", history_start=date(2000, 1, 1))
    assert len(res.df) == 0
    assert len(res.rejected) == 4

    reasons = [r.check_name for r in res.rejected]
    assert reasons.count("invalid_dates") == 3
    assert reasons.count("invalid_numeric_format") == 1


def test_collapse_consecutive_identical_values():
    """Verify that consecutive identical vintages for the same observation are collapsed."""
    raw = [
        {"date": "2020-01-01", "realtime_start": "2020-02-01", "value": "100.0"},
        {"date": "2020-01-01", "realtime_start": "2020-03-01", "value": "100.0"},  # identical skip
        {"date": "2020-01-01", "realtime_start": "2020-04-01", "value": "102.5"},  # revision keep
        {"date": "2020-01-01", "realtime_start": "2020-05-01", "value": "102.5"},  # identical skip
    ]
    res = normalize_observations(raw, "TEST_SERIES", history_start=date(2000, 1, 1))
    assert len(res.df) == 2
    assert res.df.iloc[0]["vintage_date"] == date(2020, 2, 1)
    assert res.df.iloc[0]["value"] == Decimal("100.0")
    assert res.df.iloc[1]["vintage_date"] == date(2020, 4, 1)
    assert res.df.iloc[1]["value"] == Decimal("102.5")


def test_history_start_filtering():
    """Verify that records prior to history_start are excluded without error."""
    raw = [
        {"date": "1985-01-01", "realtime_start": "1985-02-01", "value": "50.0"},
        {"date": "1995-01-01", "realtime_start": "1995-02-01", "value": "80.0"},
    ]
    res = normalize_observations(raw, "TEST_SERIES", history_start=date(1990, 1, 1))
    assert len(res.df) == 1
    assert res.df.iloc[0]["observation_date"] == date(1995, 1, 1)
