"""Unit tests for revision detector, classifications, numbering, and immutability."""

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from econ_monitor.core.yaml_config import load_indicators_config
from econ_monitor.db.models import (
    AppState,
    DataEvent,
    EconomicSeries,
    Observation,
    ObservationVintage,
    RevisionHistory,
)
from econ_monitor.db.repositories import seed_economic_series
from econ_monitor.revisions.detector import detect_revisions


@pytest.fixture
def seeded_db(in_memory_db_session: Session) -> Session:
    """Fixture providing an in-memory database with seeded economic series."""
    config = load_indicators_config("config/indicators.yaml")
    seed_economic_series(in_memory_db_session, config)
    in_memory_db_session.commit()
    return in_memory_db_session


def test_first_vintage_never_revision_and_numbering(seeded_db: Session):
    """Synthetic test: first vintage is never a revision; revision numbers increment."""
    gdp = seeded_db.scalars(select(EconomicSeries).where(EconomicSeries.code == "GDP")).first()

    obs = Observation(
        series_id=gdp.id,
        observation_date=date(2023, 1, 1),
        first_seen_at=datetime(2023, 1, 10, tzinfo=timezone.utc),
    )
    seeded_db.add(obs)
    seeded_db.flush()

    # Vintage 1: initial publication -> never a revision
    v1 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2023, 1, 25),
        value=Decimal("100.0"),
        is_missing=False,
        retrieved_at=datetime(2023, 1, 25, tzinfo=timezone.utc),
        source="test",
    )
    # Vintage 2: first revision -> revision_number = 1
    v2 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2023, 2, 25),
        value=Decimal("102.0"),
        is_missing=False,
        retrieved_at=datetime(2023, 2, 25, tzinfo=timezone.utc),
        source="test",
    )
    # Vintage 3: second revision -> revision_number = 2
    v3 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2023, 3, 25),
        value=Decimal("101.5"),
        is_missing=False,
        retrieved_at=datetime(2023, 3, 25, tzinfo=timezone.utc),
        source="test",
    )
    seeded_db.add_all([v1, v2, v3])
    seeded_db.commit()

    res = detect_revisions(seeded_db, series_codes=["GDP"])
    assert res.total_revisions_created == 2

    revs = list(
        seeded_db.scalars(
            select(RevisionHistory)
            .where(RevisionHistory.observation_id == obs.id)
            .order_by(RevisionHistory.revision_number.asc())
        ).all()
    )
    assert len(revs) == 2
    assert revs[0].revision_number == 1
    assert revs[0].previous_value == Decimal("100.0")
    assert revs[0].new_value == Decimal("102.0")
    assert revs[0].direction == "up"

    assert revs[1].revision_number == 2
    assert revs[1].previous_value == Decimal("102.0")
    assert revs[1].new_value == Decimal("101.5")
    assert revs[1].direction == "down"


def test_consecutive_identical_vintages_and_idempotency(seeded_db: Session):
    """Synthetic test: consecutive identical vintages are ignored; rerun creates 0 new rows."""
    gdp = seeded_db.scalars(select(EconomicSeries).where(EconomicSeries.code == "GDP")).first()

    obs = Observation(
        series_id=gdp.id,
        observation_date=date(2023, 4, 1),
        first_seen_at=datetime(2023, 4, 10, tzinfo=timezone.utc),
    )
    seeded_db.add(obs)
    seeded_db.flush()

    v1 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2023, 4, 25),
        value=Decimal("100.0"),
        is_missing=False,
        retrieved_at=datetime(2023, 4, 25, tzinfo=timezone.utc),
        source="test",
    )
    # v2 has identical value 100.0 -> no revision
    v2 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2023, 5, 25),
        value=Decimal("100.0"),
        is_missing=False,
        retrieved_at=datetime(2023, 5, 25, tzinfo=timezone.utc),
        source="test",
    )
    seeded_db.add_all([v1, v2])
    seeded_db.commit()

    res1 = detect_revisions(seeded_db, series_codes=["GDP"])
    assert res1.total_revisions_created == 0

    # Idempotent rerun
    res2 = detect_revisions(seeded_db, series_codes=["GDP"])
    assert res2.total_revisions_created == 0
    assert res2.total_new_observations == 0


def test_missing_value_transitions(seeded_db: Session):
    """Synthetic test: transitions between value and None are missing transitions."""
    unrate = seeded_db.scalars(
        select(EconomicSeries).where(EconomicSeries.code == "UNRATE")
    ).first()

    obs = Observation(
        series_id=unrate.id,
        observation_date=date(2023, 1, 1),
        first_seen_at=datetime(2023, 1, 5, tzinfo=timezone.utc),
    )
    seeded_db.add(obs)
    seeded_db.flush()

    # V1: missing -> V2: became_available -> V3: became_missing
    v1 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2023, 1, 10),
        value=None,
        is_missing=True,
        retrieved_at=datetime(2023, 1, 10, tzinfo=timezone.utc),
        source="test",
    )
    v2 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2023, 2, 10),
        value=Decimal("3.8"),
        is_missing=False,
        retrieved_at=datetime(2023, 2, 10, tzinfo=timezone.utc),
        source="test",
    )
    v3 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2023, 3, 10),
        value=None,
        is_missing=True,
        retrieved_at=datetime(2023, 3, 10, tzinfo=timezone.utc),
        source="test",
    )
    seeded_db.add_all([v1, v2, v3])
    seeded_db.commit()

    res = detect_revisions(seeded_db, series_codes=["UNRATE"])
    counts = res.series_results["UNRATE"]
    assert counts.became_available_count == 1
    assert counts.became_missing_count == 1


def test_vintage_immutability(seeded_db: Session):
    """Synthetic test: vintage row count and checksum are identical after detection."""
    gdp = seeded_db.scalars(select(EconomicSeries).where(EconomicSeries.code == "GDP")).first()

    obs = Observation(
        series_id=gdp.id,
        observation_date=date(2022, 1, 1),
        first_seen_at=datetime(2022, 1, 10, tzinfo=timezone.utc),
    )
    seeded_db.add(obs)
    seeded_db.flush()

    v1 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2022, 1, 20),
        value=Decimal("150.0"),
        is_missing=False,
        retrieved_at=datetime(2022, 1, 20, tzinfo=timezone.utc),
        source="test",
    )
    v2 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2022, 2, 20),
        value=Decimal("152.0"),
        is_missing=False,
        retrieved_at=datetime(2022, 2, 20, tzinfo=timezone.utc),
        source="test",
    )
    seeded_db.add_all([v1, v2])
    seeded_db.commit()

    # Pre-detection state
    vintages_pre = seeded_db.scalars(select(ObservationVintage)).all()
    count_pre = len(vintages_pre)
    checksum_pre = hashlib.sha256(
        "".join(f"{v.id}:{v.vintage_date}:{v.value}" for v in vintages_pre).encode("utf-8")
    ).hexdigest()

    detect_revisions(seeded_db, series_codes=["GDP"])

    # Post-detection state
    vintages_post = seeded_db.scalars(select(ObservationVintage)).all()
    count_post = len(vintages_post)
    checksum_post = hashlib.sha256(
        "".join(f"{v.id}:{v.vintage_date}:{v.value}" for v in vintages_post).encode("utf-8")
    ).hexdigest()

    assert count_pre == count_post
    assert checksum_pre == checksum_post


def test_baseline_and_event_generation(seeded_db: Session):
    """Synthetic test: first run marks baseline; second run creates is_backfill=False."""
    cpi = seeded_db.scalars(select(EconomicSeries).where(EconomicSeries.code == "CPI")).first()

    obs = Observation(
        series_id=cpi.id,
        observation_date=date(2023, 5, 1),
        first_seen_at=datetime(2023, 5, 10, tzinfo=timezone.utc),
    )
    seeded_db.add(obs)
    seeded_db.flush()

    v1 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2023, 5, 20),
        value=Decimal("250.0"),
        is_missing=False,
        retrieved_at=datetime(2023, 5, 20, tzinfo=timezone.utc),
        source="test",
    )
    v2 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2023, 6, 20),
        value=Decimal("252.0"),
        is_missing=False,
        retrieved_at=datetime(2023, 6, 20, tzinfo=timezone.utc),
        source="test",
    )
    seeded_db.add_all([v1, v2])
    seeded_db.commit()

    # Run 1: establishes baseline at 2023-05-20 -> events must be backfill
    t1 = datetime(2023, 5, 20, 12, 0, tzinfo=timezone.utc)
    detect_revisions(seeded_db, series_codes=["CPI"], now=t1)
    events_run1 = list(
        seeded_db.scalars(select(DataEvent).where(DataEvent.series_id == cpi.id)).all()
    )
    assert len(events_run1) > 0
    assert all(e.is_backfill is True for e in events_run1)

    # Check baseline in app_state
    baseline_state = seeded_db.get(AppState, "event_baseline:CPI")
    assert baseline_state is not None

    # Now add a new observation and vintage after baseline (first_seen_at > t1)
    t2 = datetime(2023, 6, 25, 12, 0, tzinfo=timezone.utc)
    obs2 = Observation(
        series_id=cpi.id,
        observation_date=date(2023, 6, 1),
        first_seen_at=t2,
    )
    seeded_db.add(obs2)
    seeded_db.flush()

    v3 = ObservationVintage(
        observation_id=obs2.id,
        vintage_date=date(2023, 6, 25),
        value=Decimal("255.0"),
        is_missing=False,
        retrieved_at=t2,
        source="test",
    )
    seeded_db.add(v3)
    seeded_db.commit()

    # Run 2: events created after baseline must have is_backfill = False
    detect_revisions(seeded_db, series_codes=["CPI"], now=t2)
    new_obs_event = seeded_db.scalars(
        select(DataEvent).where(DataEvent.event_key == "new_observation:CPI:2023-06-01")
    ).first()
    assert new_obs_event is not None
    assert new_obs_event.is_backfill is False
