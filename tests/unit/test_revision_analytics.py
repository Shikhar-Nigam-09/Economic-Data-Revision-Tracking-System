"""Unit tests for revision analytics: distributions, lag buckets, bias, and filters."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from econ_monitor.core.yaml_config import load_indicators_config
from econ_monitor.db.models import EconomicSeries, Observation, ObservationVintage
from econ_monitor.db.repositories import seed_economic_series
from econ_monitor.revisions.analytics import (
    get_direction_counts,
    get_first_vs_latest_comparison,
    get_magnitude_distribution,
    get_revisions_by_lag_bucket,
    get_revisions_per_observation_frequency,
    get_series_revision_summary,
)
from econ_monitor.revisions.detector import detect_revisions


@pytest.fixture
def analytics_db(in_memory_db_session: Session) -> Session:
    """Fixture with hand-crafted observations and revisions for hand-checkable analytics."""
    config = load_indicators_config("config/indicators.yaml")
    seed_economic_series(in_memory_db_session, config)
    in_memory_db_session.commit()

    gdp = in_memory_db_session.scalars(
        select(EconomicSeries).where(EconomicSeries.code == "GDP")
    ).first()
    # initial_release_max_lag_days for GDP is 150 days

    # Obs 1: 2020-01-01. First vintage 2020-02-01 (lag 31 days <= 150 -> True initial release)
    # V1: 100.0 -> V2 (lag 60d): 102.0 (+2.0, +2.0%) -> V3 (lag 91d): 101.0 (-1.0, -0.98%)
    obs1 = Observation(
        series_id=gdp.id,
        observation_date=date(2020, 1, 1),
        first_seen_at=datetime(2020, 1, 10, tzinfo=timezone.utc),
    )
    in_memory_db_session.add(obs1)
    in_memory_db_session.flush()

    v1_1 = ObservationVintage(
        observation_id=obs1.id,
        vintage_date=date(2020, 2, 1),
        value=Decimal("100.0"),
        is_missing=False,
        retrieved_at=datetime(2020, 2, 1, tzinfo=timezone.utc),
        source="test",
    )
    v1_2 = ObservationVintage(
        observation_id=obs1.id,
        vintage_date=date(2020, 3, 1),
        value=Decimal("102.0"),
        is_missing=False,
        retrieved_at=datetime(2020, 3, 1, tzinfo=timezone.utc),
        source="test",
    )
    v1_3 = ObservationVintage(
        observation_id=obs1.id,
        vintage_date=date(2020, 4, 1),
        value=Decimal("101.0"),
        is_missing=False,
        retrieved_at=datetime(2020, 4, 1, tzinfo=timezone.utc),
        source="test",
    )

    # Obs 2: 2020-04-01. First vintage 2020-05-01 (lag 30 days <= 150 -> True initial release)
    # V1: 200.0 -> V2 (2020-06-01, lag 61d): 210.0 (+10.0, +5.0%)
    obs2 = Observation(
        series_id=gdp.id,
        observation_date=date(2020, 4, 1),
        first_seen_at=datetime(2020, 4, 10, tzinfo=timezone.utc),
    )
    in_memory_db_session.add(obs2)
    in_memory_db_session.flush()

    v2_1 = ObservationVintage(
        observation_id=obs2.id,
        vintage_date=date(2020, 5, 1),
        value=Decimal("200.0"),
        is_missing=False,
        retrieved_at=datetime(2020, 5, 1, tzinfo=timezone.utc),
        source="test",
    )
    v2_2 = ObservationVintage(
        observation_id=obs2.id,
        vintage_date=date(2020, 6, 1),
        value=Decimal("210.0"),
        is_missing=False,
        retrieved_at=datetime(2020, 6, 1, tzinfo=timezone.utc),
        source="test",
    )

    # Obs 3: 2010-01-01. First vintage 2015-01-01 (lag > 150 -> NOT a true initial release)
    # V1: 50.0 -> V2 (2015-06-01): 55.0 (+5.0, +10.0%)
    obs3 = Observation(
        series_id=gdp.id,
        observation_date=date(2010, 1, 1),
        first_seen_at=datetime(2015, 1, 1, tzinfo=timezone.utc),
    )
    in_memory_db_session.add(obs3)
    in_memory_db_session.flush()

    v3_1 = ObservationVintage(
        observation_id=obs3.id,
        vintage_date=date(2015, 1, 1),
        value=Decimal("50.0"),
        is_missing=False,
        retrieved_at=datetime(2015, 1, 1, tzinfo=timezone.utc),
        source="test",
    )
    v3_2 = ObservationVintage(
        observation_id=obs3.id,
        vintage_date=date(2015, 6, 1),
        value=Decimal("55.0"),
        is_missing=False,
        retrieved_at=datetime(2015, 6, 1, tzinfo=timezone.utc),
        source="test",
    )

    in_memory_db_session.add_all([v1_1, v1_2, v1_3, v2_1, v2_2, v3_1, v3_2])
    in_memory_db_session.commit()

    # Detect revisions
    detect_revisions(in_memory_db_session, series_codes=["GDP"])
    return in_memory_db_session


def test_true_initial_release_filter_and_summary(analytics_db: Session):
    """Verify that true_initial_release_only excludes Obs 3 and computes expected metrics."""
    # With true_initial_release_only=True: only obs1 and obs2 are included
    summary_true = get_series_revision_summary(analytics_db, "GDP", true_initial_release_only=True)
    assert summary_true.total_observations == 2
    assert summary_true.observations_revised == 2
    assert summary_true.total_revisions == 3
    assert summary_true.upward_count == 2
    assert summary_true.downward_count == 1
    assert summary_true.largest_absolute_revision == Decimal("10.0")

    # With true_initial_release_only=False: includes obs3 as well
    summary_all = get_series_revision_summary(analytics_db, "GDP", true_initial_release_only=False)
    assert summary_all.total_observations == 3
    assert summary_all.total_revisions == 4
    assert summary_all.upward_count == 3


def test_analytics_components(analytics_db: Session):
    """Verify direction counts, magnitude distribution, lag buckets, and first vs latest."""
    # 1. Direction counts
    dirs = get_direction_counts(analytics_db, "GDP", true_initial_release_only=True)
    assert dirs.upward == 2
    assert dirs.downward == 1
    assert dirs.total_revisions == 3

    # 2. Magnitude distribution
    dist = get_magnitude_distribution(
        analytics_db, "GDP", num_bins=5, true_initial_release_only=True
    )
    assert dist.total_numeric_revisions == 3
    assert len(dist.bins) == 5

    # 3. Lag buckets
    lag_buckets = get_revisions_by_lag_bucket(analytics_db, "GDP", true_initial_release_only=True)
    # Lags were 60, 91, 61 days. All 3 are in 31-90 or 91-180 days!
    bucket_map = {b.bucket_label: b.revision_count for b in lag_buckets}
    assert bucket_map["31-90 days"] == 2  # 60 and 61 days
    assert bucket_map["91-180 days"] == 1  # 91 days
    assert bucket_map["0-30 days"] == 0

    # 4. First vs Latest Comparison and Bias
    # Obs 1: first=100.0, latest=101.0 -> diff=+1.0 (+1.0%)
    # Obs 2: first=200.0, latest=210.0 -> diff=+10.0 (+5.0%)
    # Mean signed abs = (1.0 + 10.0)/2 = 5.5
    # Mean signed rel = (1.0% + 5.0%)/2 = 3.0%
    f_vs_l = get_first_vs_latest_comparison(analytics_db, "GDP", true_initial_release_only=True)
    assert f_vs_l.observations_compared == 2
    assert f_vs_l.observations_revised == 2
    assert f_vs_l.share_revised_upward_pct == 100.0
    assert f_vs_l.mean_signed_absolute_revision == Decimal("5.5")
    assert f_vs_l.mean_signed_relative_revision_pct == Decimal("3.0")

    # 5. Revisions per observation frequency
    freqs = get_revisions_per_observation_frequency(
        analytics_db, "GDP", true_initial_release_only=True
    )
    freq_map = {f.revision_count: f.observation_count for f in freqs}
    assert freq_map[1] == 1  # Obs 2 had 1 revision
    assert freq_map[2] == 1  # Obs 1 had 2 revisions
