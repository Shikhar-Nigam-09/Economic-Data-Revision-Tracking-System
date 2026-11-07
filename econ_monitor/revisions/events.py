"""Revision and data quality events generator for notification alerts."""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from econ_monitor.core.clock import get_utc_now
from econ_monitor.core.logging import get_logger
from econ_monitor.db.models import (
    AppState,
    DataEvent,
    DataEventType,
    EconomicSeries,
    RevisionHistory,
)

logger = get_logger("econ_monitor.revisions.events")


def is_series_event_baseline_set(session: Session, series_code: str) -> bool:
    """Check if the event baseline for a series has been established."""
    key = f"event_baseline:{series_code.upper()}"
    state = session.get(AppState, key)
    return state is not None


def mark_series_event_baseline_set(
    session: Session,
    series_code: str,
    timestamp: Optional[datetime] = None,
) -> None:
    """Record that the event baseline for a series is set."""
    if timestamp is None:
        timestamp = get_utc_now()
    key = f"event_baseline:{series_code.upper()}"
    state = session.get(AppState, key)
    if state is None:
        state = AppState(
            key=key,
            value={"established_at": timestamp.isoformat(), "code": series_code.upper()},
            updated_at=timestamp,
        )
        session.add(state)
    else:
        state.value = {"established_at": timestamp.isoformat(), "code": series_code.upper()}
        state.updated_at = timestamp
    session.flush()


def create_new_observation_event(
    session: Session,
    series: EconomicSeries,
    observation_id: int,
    observation_date: date,
    initial_vintage_date: date,
    initial_value: Optional[float],
    is_backfill: bool,
    now: Optional[datetime] = None,
) -> Optional[DataEvent]:
    """Create a new_observation data_event if not already present.

    event_key: new_observation:{code}:{observation_date}
    """
    if now is None:
        now = get_utc_now()

    event_key = f"new_observation:{series.code}:{observation_date.isoformat()}"
    existing = session.scalars(select(DataEvent).where(DataEvent.event_key == event_key)).first()
    if existing is not None:
        return None

    payload = {
        "series_code": series.code,
        "fred_series_id": series.fred_series_id,
        "title": series.title,
        "observation_date": observation_date.isoformat(),
        "initial_vintage_date": initial_vintage_date.isoformat(),
        "initial_value": str(initial_value) if initial_value is not None else None,
        "units": series.units,
        "change_unit_label": series.change_unit_label,
    }

    event = DataEvent(
        event_key=event_key,
        event_type=DataEventType.NEW_OBSERVATION,
        series_id=series.id,
        observation_id=observation_id,
        vintage_date=initial_vintage_date,
        payload=payload,
        is_backfill=is_backfill,
        occurred_on=initial_vintage_date,
        created_at=now,
    )
    session.add(event)
    session.flush()
    return event


def create_revision_batch_event(
    session: Session,
    series: EconomicSeries,
    vintage_date: date,
    revisions: List[RevisionHistory],
    is_backfill: bool,
    now: Optional[datetime] = None,
) -> Optional[DataEvent]:
    """Create a revision_batch data_event per A6.8.

    event_key: revision_batch:{code}:{vintage_date}
    Groups all revisions for a series on a single vintage date.
    Payload: counts of up/down, largest absolute and relative revision, top 5 revisions.
    """
    if not revisions:
        return None

    if now is None:
        now = get_utc_now()

    event_key = f"revision_batch:{series.code}:{vintage_date.isoformat()}"
    existing = session.scalars(select(DataEvent).where(DataEvent.event_key == event_key)).first()
    if existing is not None:
        return None

    up_count = sum(1 for r in revisions if r.direction == "up")
    down_count = sum(1 for r in revisions if r.direction == "down")
    became_avail_count = sum(1 for r in revisions if r.revision_type == "became_available")
    became_missing_count = sum(1 for r in revisions if r.revision_type == "became_missing")

    # Find largest absolute revision
    numeric_revisions = [r for r in revisions if r.absolute_revision is not None]
    if numeric_revisions:
        largest_abs_rev_obj = max(numeric_revisions, key=lambda r: abs(r.absolute_revision))
        largest_abs = {
            "observation_date": largest_abs_rev_obj.observation_date.isoformat(),
            "previous_value": str(largest_abs_rev_obj.previous_value),
            "new_value": str(largest_abs_rev_obj.new_value),
            "absolute_revision": str(largest_abs_rev_obj.absolute_revision),
        }
    else:
        largest_abs = None

    # Find largest relative revision
    rel_revisions = [r for r in revisions if r.relative_revision_pct is not None]
    if rel_revisions:
        largest_rel_rev_obj = max(rel_revisions, key=lambda r: abs(r.relative_revision_pct))
        largest_rel = {
            "observation_date": largest_rel_rev_obj.observation_date.isoformat(),
            "previous_value": str(largest_rel_rev_obj.previous_value),
            "new_value": str(largest_rel_rev_obj.new_value),
            "relative_revision_pct": str(largest_rel_rev_obj.relative_revision_pct),
        }
    else:
        largest_rel = None

    # Top 5 revisions by absolute magnitude (or chronological if no numeric revisions)
    if numeric_revisions:
        top_sorted = sorted(
            numeric_revisions, key=lambda r: abs(r.absolute_revision), reverse=True
        )[:5]
    else:
        top_sorted = revisions[:5]

    top_5_list = [
        {
            "observation_date": r.observation_date.isoformat(),
            "previous_value": str(r.previous_value) if r.previous_value is not None else None,
            "new_value": str(r.new_value) if r.new_value is not None else None,
            "absolute_revision": str(r.absolute_revision)
            if r.absolute_revision is not None
            else None,
            "relative_revision_pct": (
                str(r.relative_revision_pct) if r.relative_revision_pct is not None else None
            ),
            "direction": r.direction,
            "revision_type": r.revision_type,
            "revision_lag_days": r.revision_lag_days,
        }
        for r in top_sorted
    ]

    payload: Dict[str, Any] = {
        "series_code": series.code,
        "vintage_date": vintage_date.isoformat(),
        "total_revisions": len(revisions),
        "up_revisions_count": up_count,
        "down_revisions_count": down_count,
        "became_available_count": became_avail_count,
        "became_missing_count": became_missing_count,
        "largest_absolute_revision": largest_abs,
        "largest_relative_revision": largest_rel,
        "top_5_revisions": top_5_list,
        "units": series.units,
        "change_unit_label": series.change_unit_label,
    }

    event = DataEvent(
        event_key=event_key,
        event_type=DataEventType.REVISION_BATCH,
        series_id=series.id,
        observation_id=None,
        vintage_date=vintage_date,
        payload=payload,
        is_backfill=is_backfill,
        occurred_on=vintage_date,
        created_at=now,
    )
    session.add(event)
    session.flush()
    return event


def create_quality_issue_event(
    session: Session,
    series: EconomicSeries,
    check_name: str,
    run_uuid: str,
    message: str,
    details: Optional[Dict[str, Any]],
    is_backfill: bool,
    now: Optional[datetime] = None,
) -> Optional[DataEvent]:
    """Create a quality_issue data_event per A6.8.

    event_key: quality_issue:{code}:{check_name}:{run_uuid}
    Emitted only when a check transitions from passing to failing.
    """
    if now is None:
        now = get_utc_now()

    event_key = f"quality_issue:{series.code}:{check_name}:{run_uuid}"
    existing = session.scalars(select(DataEvent).where(DataEvent.event_key == event_key)).first()
    if existing is not None:
        return None

    payload = {
        "series_code": series.code,
        "check_name": check_name,
        "run_uuid": run_uuid,
        "message": message,
        "details": details,
        "checked_at": now.isoformat(),
    }

    event = DataEvent(
        event_key=event_key,
        event_type=DataEventType.QUALITY_ISSUE,
        series_id=series.id,
        observation_id=None,
        vintage_date=None,
        payload=payload,
        is_backfill=is_backfill,
        occurred_on=now.date(),
        created_at=now,
    )
    session.add(event)
    session.flush()
    return event
