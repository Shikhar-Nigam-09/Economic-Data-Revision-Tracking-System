"""Unit tests for IngestionService, DatabaseSink, failure isolation, and idempotency."""

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from econ_monitor.core.yaml_config import load_indicators_config
from econ_monitor.db.models import (
    EconomicSeries,
    IngestionMode,
    IngestionStatus,
    Observation,
    ObservationVintage,
)
from econ_monitor.db.repositories import seed_economic_series
from econ_monitor.ingestion.fred_client import FredClient
from econ_monitor.ingestion.service import (
    DatabaseSink,
    IngestionService,
    SnapshotSink,
)


@pytest.fixture
def mock_fred_client():
    """Mock FredClient delivering synthetic observation data."""
    client = MagicMock(spec=FredClient)

    def fake_get_metadata(series_id: str):
        return {
            "id": series_id,
            "title": f"Synthetic {series_id}",
            "frequency_short": "Q" if series_id == "GDPC1" else "M",
            "units": "Index",
            "seasonal_adjustment": "SA",
            "last_updated": "2024-03-28 07:31:02-05",
        }

    def fake_get_observations(series_id: str, **kwargs):
        return [
            {"date": "2020-01-01", "realtime_start": "2020-02-01", "value": "100.0"},
            {"date": "2020-01-01", "realtime_start": "2020-03-01", "value": "102.5"},
            {"date": "2020-04-01", "realtime_start": "2020-05-01", "value": "105.0"},
        ]

    client.get_series_metadata.side_effect = fake_get_metadata
    client.get_vintage_observations.side_effect = fake_get_observations
    return client


def test_database_sink_and_idempotency(in_memory_db_session: Session, mock_fred_client):
    """Verify that a first ingest inserts rows and a second ingest inserts zero new rows."""
    config = load_indicators_config("config/indicators.yaml")
    seed_economic_series(in_memory_db_session, config)
    in_memory_db_session.commit()

    service = IngestionService(fred_client=mock_fred_client)
    sink = DatabaseSink(
        session=in_memory_db_session,
        ingestion_run_id=None,
        run_uuid="test_run_1",
    )

    # First ingestion run
    res1 = service.run_ingestion(
        indicators_config=config,
        sink=sink,
        selected_series_codes=["GDP"],
        force_full=True,
        mode=IngestionMode.API,
        session=in_memory_db_session,
    )
    assert res1.status == IngestionStatus.SUCCESS
    assert res1.rows_inserted == 3

    # Check observations and vintages exist
    obs = in_memory_db_session.scalars(select(Observation)).all()
    vints = in_memory_db_session.scalars(select(ObservationVintage)).all()
    assert len(obs) == 2  # 2020-01-01 and 2020-04-01
    assert len(vints) == 3

    # Second ingestion run: idempotency test
    sink2 = DatabaseSink(
        session=in_memory_db_session,
        ingestion_run_id=None,
        run_uuid="test_run_2",
    )
    res2 = service.run_ingestion(
        indicators_config=config,
        sink=sink2,
        selected_series_codes=["GDP"],
        force_full=True,
        mode=IngestionMode.API,
        session=in_memory_db_session,
    )
    assert res2.status == IngestionStatus.SUCCESS
    assert res2.rows_inserted == 0  # Zero new vintages inserted!


def test_failure_isolation(in_memory_db_session: Session, mock_fred_client):
    """Verify that one series failing leaves others committed and sets status to partial."""
    config = load_indicators_config("config/indicators.yaml")
    seed_economic_series(in_memory_db_session, config)
    in_memory_db_session.commit()

    # Make CPI fail while GDP succeeds
    def side_effect_meta(series_id: str):
        if series_id == "CPIAUCSL":
            raise ValueError("Simulated network timeout for CPI")
        return {
            "id": series_id,
            "title": f"Synthetic {series_id}",
            "frequency_short": "M",
            "units": "Index",
            "seasonal_adjustment": "SA",
            "last_updated": "2024-03-28 07:31:02-05",
        }

    mock_fred_client.get_series_metadata.side_effect = side_effect_meta

    service = IngestionService(fred_client=mock_fred_client)
    sink = DatabaseSink(
        session=in_memory_db_session,
        ingestion_run_id=None,
        run_uuid="test_isolation",
    )

    res = service.run_ingestion(
        indicators_config=config,
        sink=sink,
        selected_series_codes=["GDP", "CPI"],
        force_full=True,
        mode=IngestionMode.API,
        session=in_memory_db_session,
    )

    assert res.status == IngestionStatus.PARTIAL
    assert res.series_stats["GDP"].status == "success"
    assert res.series_stats["CPI"].status == "failed"
    assert "Simulated network timeout" in res.series_stats["CPI"].error_message


def test_incremental_skip(in_memory_db_session: Session, mock_fred_client):
    """Verify that a series is skipped with status skipped_no_new_data when unchanged."""
    config = load_indicators_config("config/indicators.yaml")
    seed_economic_series(in_memory_db_session, config)
    in_memory_db_session.commit()

    service = IngestionService(fred_client=mock_fred_client)
    sink = DatabaseSink(
        session=in_memory_db_session,
        ingestion_run_id=None,
        run_uuid="test_skip_1",
    )

    # First run establishes last_updated timestamp in economic_series
    service.run_ingestion(
        indicators_config=config,
        sink=sink,
        selected_series_codes=["GDP"],
        force_full=True,
        session=in_memory_db_session,
    )

    # Second run without force_full should detect unchanged last_updated and skip
    sink2 = DatabaseSink(
        session=in_memory_db_session,
        ingestion_run_id=None,
        run_uuid="test_skip_2",
    )
    res2 = service.run_ingestion(
        indicators_config=config,
        sink=sink2,
        selected_series_codes=["GDP"],
        force_full=False,
        session=in_memory_db_session,
    )
    assert res2.series_stats["GDP"].status == "skipped_no_new_data"
    assert res2.rows_fetched == 0


def test_snapshot_round_trip(in_memory_db_session: Session, mock_fred_client, tmp_path: Path):
    """Verify end-to-end: snapshot write, manifest verify, and import into database."""
    config = load_indicators_config("config/indicators.yaml")
    seed_economic_series(in_memory_db_session, config)
    in_memory_db_session.commit()

    service = IngestionService(fred_client=mock_fred_client)
    snapshot_sink = SnapshotSink(snapshots_dir=str(tmp_path))

    # 1. Snapshot generation
    res_snap = service.run_ingestion(
        indicators_config=config,
        sink=snapshot_sink,
        selected_series_codes=["GDP"],
        force_full=True,
        mode=IngestionMode.SNAPSHOT,
    )
    assert res_snap.status == IngestionStatus.SUCCESS

    # 2. Import snapshots into database
    res_import = service.import_snapshots_to_database(
        session=in_memory_db_session,
        snapshots_dir=str(tmp_path),
    )
    assert res_import.status == IngestionStatus.SUCCESS
    assert res_import.rows_inserted == 3

    # Check imported rows in DB
    gdp_series = in_memory_db_session.scalars(
        select(EconomicSeries).where(EconomicSeries.code == "GDP")
    ).first()
    assert gdp_series is not None
    vints = in_memory_db_session.scalars(select(ObservationVintage)).all()
    assert len(vints) == 3
    assert vints[0].value == Decimal("100.0")
