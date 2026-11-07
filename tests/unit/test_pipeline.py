"""Unit tests for multi-stage pipeline orchestration."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from sqlalchemy import select
from sqlalchemy.orm import Session

from econ_monitor.core.yaml_config import load_indicators_config
from econ_monitor.db.models import EconomicSeries, Observation, ObservationVintage
from econ_monitor.db.repositories import seed_economic_series
from econ_monitor.pipeline import run_pipeline


def test_pipeline_stop_on_ingestion_failure(in_memory_db_session: Session):
    """Test that pipeline stops execution immediately if ingestion fails."""
    # Pointing to non-existent snapshot dir causes ingestion to fail
    res = run_pipeline(
        session=in_memory_db_session,
        mode="snapshot",
        snapshots_dir="non_existent_directory_xyz_123",
    )
    assert not res.success
    assert len(res.steps) == 1
    assert res.steps[0].step_name == "ingestion_snapshot"
    assert res.steps[0].status == "failed"


def test_pipeline_execution_with_notify_disabled(in_memory_db_session: Session):
    """Test full sequential execution of pipeline with notify=False."""
    config = load_indicators_config("config/indicators.yaml")
    seed_economic_series(in_memory_db_session, config)
    in_memory_db_session.commit()

    # Seed observations for an indicator
    gdp = in_memory_db_session.scalars(
        select(EconomicSeries).where(EconomicSeries.code == "GDP")
    ).first()
    gdp.frequency = "Q"
    now = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)

    obs = Observation(
        series_id=gdp.id,
        observation_date=now.date(),
        first_seen_at=now,
    )
    in_memory_db_session.add(obs)
    in_memory_db_session.flush()

    vint = ObservationVintage(
        observation_id=obs.id,
        vintage_date=now.date(),
        value=Decimal("22000.0"),
        is_missing=False,
        retrieved_at=now,
        source="test",
    )
    in_memory_db_session.add(vint)
    in_memory_db_session.commit()

    # Mock ingestion step to simulate successful sync without external network/snapshots
    mock_ingest = MagicMock()
    mock_ingest.status = "success"
    mock_ingest.series_requested = 1
    mock_ingest.series_succeeded = 1
    mock_ingest.rows_fetched = 1
    mock_ingest.rows_inserted = 1

    with patch(
        "econ_monitor.ingestion.service.IngestionService.import_snapshots_to_database",
        return_value=mock_ingest,
    ):
        res = run_pipeline(
            session=in_memory_db_session,
            mode="snapshot",
            notify=False,
            now=now,
        )

        assert res.success
        assert len(res.steps) == 5

        step_names = [s.step_name for s in res.steps]
        assert step_names == [
            "ingestion_snapshot",
            "detect_revisions",
            "quality_checks",
            "anomaly_detection",
            "notification_dispatch",
        ]

        # Verify notification step was skipped as requested
        notify_step = res.steps[4]
        assert notify_step.status == "skipped"
        assert "Disabled" in notify_step.details["reason"]
