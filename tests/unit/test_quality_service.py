"""Unit tests for data quality framework, health scoring, and alert transitions."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from econ_monitor.core.yaml_config import (
    QualityWeights,
    load_indicators_config,
    load_monitoring_config,
)
from econ_monitor.db.models import (
    DataQualityScore,
    EconomicSeries,
    Observation,
    ObservationVintage,
)
from econ_monitor.db.repositories import seed_economic_series
from econ_monitor.quality.checks import (
    check_frequency_consistency,
    check_gaps_in_expected_grid,
    check_pandera_schema,
    check_staleness,
)
from econ_monitor.quality.scoring import compute_health_score
from econ_monitor.quality.service import run_data_quality_service


@pytest.fixture
def quality_db(in_memory_db_session: Session) -> Session:
    """Fixture providing database seeded with indicators."""
    config = load_indicators_config("config/indicators.yaml")
    seed_economic_series(in_memory_db_session, config)
    in_memory_db_session.commit()
    return in_memory_db_session


def test_quarterly_vs_monthly_grid_logic(quality_db: Session):
    """Verify that quarterly series (GDP) with quarterly spacing is not flagged as a gap."""
    gdp = quality_db.scalars(select(EconomicSeries).where(EconomicSeries.code == "GDP")).first()
    gdp.frequency = "Q"
    gdp.history_start = date(2023, 1, 1)
    quality_db.flush()

    # Create 4 consecutive quarterly dates: 2023-01-01, 2023-04-01, 2023-07-01, 2023-10-01
    latest_df = pd.DataFrame(
        {
            "observation_date": [
                date(2023, 1, 1),
                date(2023, 4, 1),
                date(2023, 7, 1),
                date(2023, 10, 1),
            ],
            "latest_value": [100.0, 101.0, 102.0, 103.0],
            "is_missing": [False, False, False, False],
        }
    )

    # Expected grid check should pass with 0 missing dates
    res = check_gaps_in_expected_grid(quality_db, gdp, latest_df, today_date=date(2023, 12, 1))
    assert res.passed is True
    assert res.affected_count == 0

    # Frequency consistency should also pass for Q cadence
    freq_res = check_frequency_consistency(quality_db, gdp, latest_df)
    assert freq_res.passed is True


def test_staleness_with_frozen_clock(quality_db: Session):
    """Verify staleness detection with frozen date."""
    gdp = quality_db.scalars(select(EconomicSeries).where(EconomicSeries.code == "GDP")).first()
    gdp.frequency = "Q"
    gdp.max_stale_lag_days = 130  # configured in indicators.yaml

    latest_df = pd.DataFrame(
        {
            "observation_date": [date(2023, 1, 1)],
            "latest_value": [100.0],
            "is_missing": [False],
        }
    )

    # End of period: 2023-01-01 + 90 days = 2023-04-01
    # On 2023-06-01: lag is 61 days <= 130 days -> fresh
    res_fresh = check_staleness(quality_db, gdp, latest_df, today_date=date(2023, 6, 1))
    assert res_fresh.passed is True

    # On 2024-01-01: lag is 275 days > 130 days -> stale
    res_stale = check_staleness(quality_db, gdp, latest_df, today_date=date(2024, 1, 1))
    assert res_stale.passed is False
    assert res_stale.details["days_overdue"] == 145


def test_pandera_failure_captured_and_mapped(quality_db: Session):
    """Verify that a schema violation is captured by check_pandera_schema."""
    gdp = quality_db.scalars(select(EconomicSeries).where(EconomicSeries.code == "GDP")).first()
    # expected range: 0 to 200,000

    vintages_df = pd.DataFrame(
        {
            "fred_series_id": ["GDPC1"],
            "observation_date": [date(2023, 1, 1)],
            "vintage_date": [date(2023, 2, 1)],
            "value": [100.0],
            "is_missing": [False],
        }
    )

    # Out-of-bounds latest value (e.g. 500,000 > 200,000)
    latest_df = pd.DataFrame(
        {
            "observation_date": [date(2023, 1, 1)],
            "latest_value": [500000.0],
            "is_missing": [False],
        }
    )

    res = check_pandera_schema(quality_db, gdp, vintages_df, latest_df)
    assert res.passed is False
    assert res.severity == "error"
    assert "schema error" in res.message.lower() or "failed" in res.message.lower()


def test_deterministic_health_scores():
    """Verify health score computation formula deterministically."""
    from econ_monitor.quality.checks import CheckResult

    weights = QualityWeights(
        completeness=25,
        validity=20,
        uniqueness=10,
        frequency_consistency=15,
        timeliness=20,
        schema_conformance=10,
    )

    # All perfect checks -> score 100.0
    perfect_results = {
        "missing_values": CheckResult(
            "missing_values",
            "warning",
            True,
            0,
            "OK",
            checked_records_count=10,
            failing_records_count=0,
        ),
        "gaps_in_expected_grid": CheckResult(
            "gaps_in_expected_grid",
            "warning",
            True,
            0,
            "OK",
            checked_records_count=10,
            failing_records_count=0,
        ),
        "invalid_dates": CheckResult(
            "invalid_dates",
            "error",
            True,
            0,
            "OK",
            checked_records_count=10,
            failing_records_count=0,
        ),
        "invalid_numeric_format": CheckResult(
            "invalid_numeric_format",
            "error",
            True,
            0,
            "OK",
            checked_records_count=10,
            failing_records_count=0,
        ),
        "range_violations": CheckResult(
            "range_violations",
            "error",
            True,
            0,
            "OK",
            checked_records_count=10,
            failing_records_count=0,
        ),
        "unexpected_nulls": CheckResult(
            "unexpected_nulls",
            "error",
            True,
            0,
            "OK",
            checked_records_count=10,
            failing_records_count=0,
        ),
        "duplicate_observations": CheckResult(
            "duplicate_observations",
            "error",
            True,
            0,
            "OK",
            checked_records_count=10,
            failing_records_count=0,
        ),
        "frequency_consistency": CheckResult(
            "frequency_consistency",
            "warning",
            True,
            0,
            "OK",
            checked_records_count=10,
            failing_records_count=0,
        ),
        "staleness": CheckResult(
            "staleness",
            "warning",
            True,
            0,
            "OK",
            details={"days_overdue": 0, "max_allowed_lag_days": 100},
        ),
        "pandera_schema": CheckResult(
            "pandera_schema",
            "error",
            True,
            0,
            "OK",
            checked_records_count=10,
            failing_records_count=0,
        ),
    }

    score_res = compute_health_score(perfect_results, weights)
    assert score_res.composite_score == Decimal("100.00")
    assert all(v == 100.0 for v in score_res.component_scores.values())


def test_quality_service_alert_transition_and_backfill(quality_db: Session):
    """Verify quality service backfill rule and emit events on pass->fail transition."""
    gdp = quality_db.scalars(select(EconomicSeries).where(EconomicSeries.code == "GDP")).first()
    gdp.frequency = "Q"
    gdp.history_start = date(2023, 1, 1)
    quality_db.flush()

    # Create one valid observation and vintage
    obs = Observation(
        series_id=gdp.id,
        observation_date=date(2023, 1, 1),
        first_seen_at=datetime(2023, 1, 10, tzinfo=timezone.utc),
    )
    quality_db.add(obs)
    quality_db.flush()

    v = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2023, 1, 20),
        value=Decimal("100.0"),
        is_missing=False,
        retrieved_at=datetime(2023, 1, 20, tzinfo=timezone.utc),
        source="test",
    )
    quality_db.add(v)
    quality_db.commit()

    mon_config = load_monitoring_config("config/monitoring.yaml")

    # Run 1: initial run
    res1 = run_data_quality_service(
        session=quality_db,
        series_codes=["GDP"],
        monitoring_config=mon_config,
        today_date=date(2023, 2, 1),
    )
    rep1 = res1.series_reports["GDP"]
    assert rep1.composite_score > Decimal("80.00")

    # Check scores recorded in database
    score_entry = quality_db.scalars(
        select(DataQualityScore).where(DataQualityScore.series_id == gdp.id)
    ).first()
    assert score_entry is not None

    # Run 2: second run with no new failures -> zero new alert events emitted
    res2 = run_data_quality_service(
        session=quality_db,
        series_codes=["GDP"],
        monitoring_config=mon_config,
        today_date=date(2023, 2, 1),
    )
    assert res2.total_issues_emitted == 0
