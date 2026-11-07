"""Unit tests for configuration loading and validation."""

import pytest
from pydantic import ValidationError

from econ_monitor.core.config import Settings
from econ_monitor.core.errors import ConfigurationError
from econ_monitor.core.yaml_config import (
    IndicatorConfig,
    IndicatorsRootConfig,
    QualityWeights,
    load_indicators_config,
    load_monitoring_config,
)


def test_load_indicators_config_valid():
    """Verify that default indicators.yaml loads and validates successfully."""
    cfg = load_indicators_config("config/indicators.yaml")
    assert len(cfg.indicators) >= 5
    codes = [i.code for i in cfg.indicators]
    assert "GDP" in codes
    assert "CPI" in codes
    assert "UNRATE" in codes
    assert "INDPRO" in codes
    assert "RETAIL_SALES" in codes


def test_indicators_config_duplicate_code():
    """Verify rejection of duplicate indicator codes."""
    raw = [
        {
            "code": "GDP",
            "fred_series_id": "GDPC1",
            "display_name": "GDP 1",
            "category": "National",
            "active": True,
            "anomaly_transform": "pct_change",
            "expected_min": 0,
            "expected_max": 100,
            "max_stale_lag_days": 90,
            "initial_release_max_lag_days": 90,
            "history_start": "1990-01-01",
        },
        {
            "code": "GDP",
            "fred_series_id": "OTHER",
            "display_name": "GDP 2",
            "category": "National",
            "active": True,
            "anomaly_transform": "pct_change",
            "expected_min": 0,
            "expected_max": 100,
            "max_stale_lag_days": 90,
            "initial_release_max_lag_days": 90,
            "history_start": "1990-01-01",
        },
    ]
    with pytest.raises(ValidationError, match="Duplicate indicator codes"):
        IndicatorsRootConfig(indicators=raw)


def test_indicators_config_duplicate_fred_id():
    """Verify rejection of duplicate FRED series IDs."""
    raw = [
        {
            "code": "GDP1",
            "fred_series_id": "SAME_ID",
            "display_name": "GDP 1",
            "category": "National",
            "active": True,
            "anomaly_transform": "pct_change",
            "expected_min": 0,
            "expected_max": 100,
            "max_stale_lag_days": 90,
            "initial_release_max_lag_days": 90,
            "history_start": "1990-01-01",
        },
        {
            "code": "GDP2",
            "fred_series_id": "SAME_ID",
            "display_name": "GDP 2",
            "category": "National",
            "active": True,
            "anomaly_transform": "pct_change",
            "expected_min": 0,
            "expected_max": 100,
            "max_stale_lag_days": 90,
            "initial_release_max_lag_days": 90,
            "history_start": "1990-01-01",
        },
    ]
    with pytest.raises(ValidationError, match="Duplicate FRED series IDs"):
        IndicatorsRootConfig(indicators=raw)


def test_indicator_invalid_range():
    """Verify rejection when expected_min >= expected_max."""
    with pytest.raises(ValidationError, match="expected_min"):
        IndicatorConfig(
            code="TEST",
            fred_series_id="TEST_ID",
            display_name="Test",
            category="Test",
            active=True,
            anomaly_transform="diff",
            expected_min=100.0,
            expected_max=50.0,
            max_stale_lag_days=30,
            initial_release_max_lag_days=30,
            history_start="1990-01-01",
        )


def test_load_monitoring_config_valid():
    """Verify that default monitoring.yaml loads and validates successfully."""
    cfg = load_monitoring_config("config/monitoring.yaml")
    weights = cfg.quality.weights
    total = (
        weights.completeness
        + weights.validity
        + weights.uniqueness
        + weights.frequency_consistency
        + weights.timeliness
        + weights.schema_conformance
    )
    assert total == 100
    assert cfg.anomalies.zscore.threshold == 3.0


def test_monitoring_weights_invalid_sum():
    """Verify rejection when quality weights do not sum to 100."""
    with pytest.raises(ValidationError, match="must sum to exactly 100"):
        QualityWeights(
            completeness=20,
            validity=20,
            uniqueness=10,
            frequency_consistency=10,
            timeliness=10,
            schema_conformance=10,  # Sum = 80
        )


def test_settings_missing_database_url():
    """Verify require_database_url raises clear ConfigurationError when not set."""
    settings = Settings(DATABASE_URL=None)
    with pytest.raises(ConfigurationError, match="DATABASE_URL is not configured"):
        settings.require_database_url()
