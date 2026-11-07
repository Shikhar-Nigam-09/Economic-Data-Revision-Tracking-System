"""Unit tests for anomaly detection: feature transforms, look-ahead proof, z-score, iforest."""

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from econ_monitor.anomalies.features import compute_anomaly_features
from econ_monitor.anomalies.iforest import detect_iforest_anomalies
from econ_monitor.anomalies.service import run_anomaly_detection
from econ_monitor.anomalies.zscore import detect_zscore_anomalies
from econ_monitor.core.yaml_config import load_indicators_config
from econ_monitor.db.models import (
    AnomalyRecord,
    AppState,
    DataEvent,
    EconomicSeries,
    Observation,
    ObservationVintage,
)
from econ_monitor.db.repositories import seed_economic_series


def test_anomaly_feature_transforms():
    """Verify that pct_change and diff transforms compute accurately without error."""
    # Test diff transform (used for rate series like UNRATE)
    dates = pd.date_range("2020-01-01", periods=5, freq="MS").date
    values = [3.5, 3.7, 3.6, 4.0, 4.5]
    df_diff = pd.DataFrame(
        {
            "observation_id": range(1, 6),
            "observation_date": dates,
            "vintage_id": range(101, 106),
            "vintage_date": dates,
            "latest_value": values,
            "is_missing": [False] * 5,
        }
    )

    res_diff = compute_anomaly_features(df_diff, frequency="M", transform="diff", window=3)
    # Check transformed_change: 3.7 - 3.5 = 0.2
    assert pd.isna(res_diff.loc[0, "transformed_change"])
    assert round(res_diff.loc[1, "transformed_change"], 4) == 0.2
    assert round(res_diff.loc[2, "transformed_change"], 4) == -0.1

    # Test pct_change transform (used for level/index series like GDP, CPI)
    vals_pct = [100.0, 105.0, 102.0]
    df_pct = pd.DataFrame(
        {
            "observation_id": range(1, 4),
            "observation_date": dates[:3],
            "vintage_id": range(101, 104),
            "vintage_date": dates[:3],
            "latest_value": vals_pct,
            "is_missing": [False] * 3,
        }
    )
    res_pct = compute_anomaly_features(df_pct, frequency="M", transform="pct_change", window=2)
    # 105 / 100 - 1 = 5.0%
    assert round(res_pct.loc[1, "transformed_change"], 4) == 5.0


def test_trailing_rolling_look_ahead_prevention():
    """Mathematical proof that the trailing rolling window strictly excludes the current point."""
    # Window of 3 observations
    # changes: [c0, c1, c2, c3, c4]
    # For index 3, trailing changes should strictly be [c0, c1, c2]
    dates = pd.date_range("2020-01-01", periods=6, freq="MS").date
    # values such that diffs are:
    # 0 -> 1: diff = 10
    # 1 -> 2: diff = 20
    # 2 -> 3: diff = 30
    # 3 -> 4: diff = 1000  (Huge spike at point 4!)
    values = [100, 110, 130, 160, 1160, 1180]
    df = pd.DataFrame(
        {
            "observation_id": range(1, 7),
            "observation_date": dates,
            "vintage_id": range(101, 107),
            "vintage_date": dates,
            "latest_value": values,
            "is_missing": [False] * 6,
        }
    )

    res = compute_anomaly_features(df, frequency="M", transform="diff", window=3)

    # Point 4 diff is 1000.
    # The trailing window for point 4 MUST only see diffs for points 1, 2, 3: [10, 20, 30].
    # Mean([10, 20, 30]) = 20.0
    # Std([10, 20, 30]) = 10.0
    # Current point 4 diff (1000) MUST NOT affect rolling_mean or rolling_std at point 4!
    point_4 = res.iloc[4]
    assert point_4["transformed_change"] == 1000.0
    assert point_4["rolling_mean"] == 20.0
    assert point_4["rolling_std"] == 10.0
    # Z-score at point 4: (1000 - 20) / 10 = 98.0
    assert point_4["z_score"] == 98.0

    # For points before a full window exists (points 0, 1, 2, 3):
    # point 0: no diff
    # point 1: diff=10, 0 past diffs -> rolling_mean is NaN
    # point 2: diff=20, 1 past diff (10) -> rolling_mean is NaN (needs 3)
    # point 3: diff=30, 2 past diffs (10, 20) -> rolling_mean is NaN (needs 3)
    assert pd.isna(res.iloc[0]["rolling_mean"])
    assert pd.isna(res.iloc[1]["rolling_mean"])
    assert pd.isna(res.iloc[2]["rolling_mean"])
    assert pd.isna(res.iloc[3]["rolling_mean"])


def test_zscore_detector_flagging():
    """Test that zscore detector flags observations >= threshold and skips NaNs."""
    dates = pd.date_range("2020-01-01", periods=5, freq="MS").date
    feature_df = pd.DataFrame(
        {
            "observation_id": [1, 2, 3, 4, 5],
            "observation_date": dates,
            "vintage_id": [11, 12, 13, 14, 15],
            "vintage_date": dates,
            "current_value": [10.0, 11.0, 12.0, 50.0, 13.0],
            "previous_value": [None, 10.0, 11.0, 12.0, 50.0],
            "transformed_change": [None, 1.0, 1.0, 38.0, -37.0],
            "rolling_mean": [None, None, 1.0, 1.0, 1.0],
            "rolling_std": [None, None, 0.1, 0.1, 0.1],
            "z_score": [np.nan, np.nan, 0.0, 370.0, -2.5],
        }
    )

    # Threshold 3.0: only row 3 (z=370.0) should be flagged
    flagged = detect_zscore_anomalies(feature_df, threshold=3.0, transform="diff", window=2)
    assert len(flagged) == 1
    item = flagged[0]
    assert item.observation_id == 4
    assert item.method == "zscore"
    assert item.score == Decimal("370.0")
    assert item.threshold == Decimal("3.0")
    assert item.features["transform"] == "diff"


def test_iforest_detector_skip_and_execution():
    """Test that Isolation Forest skips when rows < min_history and runs when sufficient."""
    dates = pd.date_range("2010-01-01", periods=15, freq="QS").date
    short_df = pd.DataFrame(
        {
            "observation_id": range(1, 16),
            "observation_date": dates,
            "vintage_id": range(101, 116),
            "vintage_date": dates,
            "current_value": np.linspace(100, 200, 15),
            "previous_value": np.linspace(95, 195, 15),
            "transformed_change": [2.5] * 15,
            "rolling_mean": [2.5] * 15,
            "rolling_std": [0.1] * 15,
            "z_score": [0.0] * 15,
        }
    )

    # For Q, default min_history is 40. short_df has 15 rows.
    result_skip = detect_iforest_anomalies(short_df, frequency="Q")
    assert not result_skip.ran
    assert len(result_skip.flagged) == 0
    assert "Insufficient history" in result_skip.skipped_reason

    # Now create synthetic dataset with 45 rows (>= 40) including an outlier
    dates_long = pd.date_range("2010-01-01", periods=45, freq="QS").date
    np.random.seed(42)
    changes = np.random.normal(loc=1.0, scale=0.2, size=45)
    changes[20] = 15.0  # Massive outlier

    long_df = pd.DataFrame(
        {
            "observation_id": range(1, 46),
            "observation_date": dates_long,
            "vintage_id": range(201, 246),
            "vintage_date": dates_long,
            "current_value": np.cumsum(changes) + 100,
            "previous_value": np.cumsum(changes) + 99,
            "transformed_change": changes,
            "rolling_mean": [1.0] * 45,
            "rolling_std": [0.2] * 45,
            "z_score": (changes - 1.0) / 0.2,
        }
    )

    result_run = detect_iforest_anomalies(
        long_df,
        frequency="Q",
        min_history=40,
        n_estimators=50,
        contamination=0.05,
    )
    assert result_run.ran
    assert result_run.skipped_reason is None
    assert result_run.rows_evaluated == 45
    # Outlier at index 20 should be flagged
    flagged_obs_ids = [item.observation_id for item in result_run.flagged]
    assert 21 in flagged_obs_ids  # 1-indexed row 20 is obs_id 21


def test_anomaly_service_end_to_end_and_idempotence(in_memory_db_session: Session):
    """Verify anomaly detection service on SQLite in-memory DB: execution, idempotence, events."""
    config = load_indicators_config("config/indicators.yaml")
    seed_economic_series(in_memory_db_session, config)
    in_memory_db_session.commit()

    gdp = in_memory_db_session.scalars(
        select(EconomicSeries).where(EconomicSeries.code == "GDP")
    ).first()
    gdp.frequency = "Q"
    in_memory_db_session.flush()

    # Create 15 quarterly observations for GDP with a spike
    # Window for Q is 8, so observations 9+ have valid trailing statistics
    now = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
    dates = pd.date_range("2020-01-01", periods=15, freq="QS").date

    for i, d in enumerate(dates):
        obs = Observation(
            series_id=gdp.id,
            observation_date=d,
            first_seen_at=now,
        )
        in_memory_db_session.add(obs)
        in_memory_db_session.flush()

        # Regular growth +2.0%, but observation 10 has a +200% spike
        val = Decimal("100.0") + Decimal(str(i * 2))
        if i == 10:
            val = Decimal("500.0")  # Extreme jump

        vint = ObservationVintage(
            observation_id=obs.id,
            vintage_date=d,
            value=val,
            is_missing=False,
            retrieved_at=now,
            source="test",
        )
        in_memory_db_session.add(vint)

    in_memory_db_session.commit()

    # 1. First execution
    res1 = run_anomaly_detection(in_memory_db_session, series_code="GDP", now=now)
    assert "GDP" in res1.series_reports
    rep1 = res1.series_reports["GDP"]
    assert rep1.zscore_ran
    assert rep1.zscore_flagged > 0
    assert rep1.zscore_new_records > 0
    # Isolation forest skipped for 15 rows < 40 required for Q
    assert not rep1.iforest_ran
    assert "Insufficient history" in rep1.iforest_skipped_reason

    # Verify AnomalyRecord in DB
    records1 = list(
        in_memory_db_session.scalars(
            select(AnomalyRecord).where(AnomalyRecord.series_id == gdp.id)
        ).all()
    )
    assert len(records1) == rep1.zscore_new_records

    # Verify DataEvent emitted
    events1 = list(
        in_memory_db_session.scalars(
            select(DataEvent).where(
                DataEvent.series_id == gdp.id, DataEvent.event_type == "anomaly"
            )
        ).all()
    )
    assert len(events1) > 0
    assert events1[0].payload["method"] == "zscore"

    # Verify AppState recorded
    state = in_memory_db_session.get(AppState, "anomaly_last_run")
    assert state is not None
    assert "GDP" in state.value["series"]

    # 2. Second execution (IDEMPOTENCE CHECK)
    # Must NOT insert duplicate AnomalyRecords or duplicate DataEvents
    res2 = run_anomaly_detection(in_memory_db_session, series_code="GDP", now=now)
    rep2 = res2.series_reports["GDP"]
    assert rep2.zscore_flagged == rep1.zscore_flagged
    assert rep2.zscore_new_records == 0  # 0 new records inserted!
    assert rep2.events_emitted == 0  # 0 new events emitted!

    records2 = list(
        in_memory_db_session.scalars(
            select(AnomalyRecord).where(AnomalyRecord.series_id == gdp.id)
        ).all()
    )
    assert len(records2) == len(records1)
