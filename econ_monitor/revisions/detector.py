"""Revision detector implementing window functions, classifications, and incremental boundaries."""

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, NamedTuple, Optional, Set

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from econ_monitor.core.clock import get_utc_now
from econ_monitor.core.logging import get_logger
from econ_monitor.db.models import (
    AppState,
    EconomicSeries,
    Observation,
    ObservationVintage,
    RevisionHistory,
)
from econ_monitor.revisions.events import (
    create_new_observation_event,
    create_revision_batch_event,
    is_series_event_baseline_set,
    mark_series_event_baseline_set,
)
from econ_monitor.revisions.metrics import compute_revision_metrics

logger = get_logger("econ_monitor.revisions.detector")


class SeriesDetectionCounts(NamedTuple):
    """Statistics for revision detection on a single series."""

    series_code: str
    new_observations_count: int
    revised_observations_count: int
    unchanged_observations_count: int
    missing_observations_count: int
    revisions_created_count: int
    value_revisions_count: int
    became_available_count: int
    became_missing_count: int
    upward_revisions_count: int
    downward_revisions_count: int


class DetectionResult(NamedTuple):
    """Overall outcome of a revision detection run across series."""

    series_results: Dict[str, SeriesDetectionCounts]
    total_revisions_created: int
    total_new_observations: int


def _get_series_detection_boundary(session: Session, series_code: str) -> Optional[datetime]:
    """Retrieve the boundary timestamp of the last successful detection for a series."""
    key = f"detection_boundary:{series_code.upper()}"
    state = session.get(AppState, key)
    if state and state.value and "last_detected_at" in state.value:
        try:
            return datetime.fromisoformat(state.value["last_detected_at"])
        except Exception:
            return None
    return None


def _set_series_detection_boundary(
    session: Session, series_code: str, boundary_dt: datetime
) -> None:
    """Update the boundary timestamp of the last successful detection for a series."""
    key = f"detection_boundary:{series_code.upper()}"
    state = session.get(AppState, key)
    val = {"last_detected_at": boundary_dt.isoformat(), "code": series_code.upper()}
    if state is None:
        state = AppState(key=key, value=val, updated_at=boundary_dt)
        session.add(state)
    else:
        state.value = val
        state.updated_at = boundary_dt
    session.flush()


def detect_revisions_for_series(
    session: Session,
    series: EconomicSeries,
    now: Optional[datetime] = None,
) -> SeriesDetectionCounts:
    """Execute revision detection for a single series incrementally and atomically."""
    if now is None:
        now = get_utc_now()

    # Determine baseline status for events
    is_baseline_set = is_series_event_baseline_set(session, series.code)
    is_backfill = not is_baseline_set

    # 1. Classify New Observations based on detection boundary
    last_boundary = _get_series_detection_boundary(session, series.code)

    obs_query = select(Observation).where(Observation.series_id == series.id)
    if last_boundary is not None:
        obs_query = obs_query.where(Observation.first_seen_at > last_boundary)

    new_observations: List[Observation] = list(session.scalars(obs_query).all())
    new_obs_count = len(new_observations)

    # For each newly identified observation, emit new_observation event
    for obs in new_observations:
        # Get its first vintage
        first_vintage = session.scalars(
            select(ObservationVintage)
            .where(ObservationVintage.observation_id == obs.id)
            .order_by(ObservationVintage.vintage_date.asc())
        ).first()

        if first_vintage:
            create_new_observation_event(
                session=session,
                series=series,
                observation_id=obs.id,
                observation_date=obs.observation_date,
                initial_vintage_date=first_vintage.vintage_date,
                initial_value=float(first_vintage.value)
                if first_vintage.value is not None
                else None,
                is_backfill=is_backfill,
                now=now,
            )

    # 2. Window query over all vintages for this series:
    # Use LAG() window functions partitioned by observation_id ordered by vintage_date
    lag_vintage_id = (
        func.lag(ObservationVintage.id)
        .over(
            partition_by=ObservationVintage.observation_id,
            order_by=ObservationVintage.vintage_date.asc(),
        )
        .label("prev_vintage_id")
    )

    lag_vintage_date = (
        func.lag(ObservationVintage.vintage_date)
        .over(
            partition_by=ObservationVintage.observation_id,
            order_by=ObservationVintage.vintage_date.asc(),
        )
        .label("prev_vintage_date")
    )

    lag_value = (
        func.lag(ObservationVintage.value)
        .over(
            partition_by=ObservationVintage.observation_id,
            order_by=ObservationVintage.vintage_date.asc(),
        )
        .label("prev_value")
    )

    lag_is_missing = (
        func.lag(ObservationVintage.is_missing)
        .over(
            partition_by=ObservationVintage.observation_id,
            order_by=ObservationVintage.vintage_date.asc(),
        )
        .label("prev_is_missing")
    )

    # CTE representing all consecutive pairs
    vintage_pairs_cte = (
        select(
            ObservationVintage.id.label("curr_vintage_id"),
            ObservationVintage.observation_id.label("obs_id"),
            Observation.observation_date.label("obs_date"),
            ObservationVintage.vintage_date.label("curr_vintage_date"),
            ObservationVintage.value.label("curr_value"),
            ObservationVintage.is_missing.label("curr_is_missing"),
            lag_vintage_id,
            lag_vintage_date,
            lag_value,
            lag_is_missing,
        )
        .join(Observation, ObservationVintage.observation_id == Observation.id)
        .where(Observation.series_id == series.id)
        .cte("vintage_pairs")
    )

    # Process only vintages that do not yet have a revision row
    existing_to_vintage_ids_stmt = select(RevisionHistory.to_vintage_id).where(
        RevisionHistory.series_id == series.id
    )
    existing_to_vintage_ids: Set[int] = set(session.scalars(existing_to_vintage_ids_stmt).all())

    # Existing max revision_number per observation to continue numbering correctly
    max_rev_stmt = (
        select(
            RevisionHistory.observation_id,
            func.max(RevisionHistory.revision_number).label("max_rev_num"),
        )
        .where(RevisionHistory.series_id == series.id)
        .group_by(RevisionHistory.observation_id)
    )
    obs_max_rev_number: Dict[int, int] = dict(session.execute(max_rev_stmt).all())

    # Query all candidate pairs from CTE where prev_vintage_id IS NOT NULL
    # (A first vintage has prev_vintage_id IS NULL and is never a revision)
    pairs_stmt = (
        select(
            vintage_pairs_cte.c.curr_vintage_id,
            vintage_pairs_cte.c.obs_id,
            vintage_pairs_cte.c.obs_date,
            vintage_pairs_cte.c.curr_vintage_date,
            vintage_pairs_cte.c.curr_value,
            vintage_pairs_cte.c.curr_is_missing,
            vintage_pairs_cte.c.prev_vintage_id,
            vintage_pairs_cte.c.prev_vintage_date,
            vintage_pairs_cte.c.prev_value,
            vintage_pairs_cte.c.prev_is_missing,
        )
        .where(vintage_pairs_cte.c.prev_vintage_id.is_not(None))
        .order_by(vintage_pairs_cte.c.obs_id, vintage_pairs_cte.c.curr_vintage_date.asc())
    )

    pairs_rows = session.execute(pairs_stmt).all()

    # Track revisions to insert and events by vintage_date
    new_revisions_to_insert: List[RevisionHistory] = []
    revisions_by_vintage_date: Dict[date, List[RevisionHistory]] = defaultdict(list)

    # Counters
    revisions_created_count = 0
    val_rev_count = 0
    became_avail_count = 0
    became_miss_count = 0
    up_count = 0
    down_count = 0

    revised_observations: Set[int] = set()
    observations_with_multiple_vintages: Set[int] = set()

    for row in pairs_rows:
        obs_id = row.obs_id
        observations_with_multiple_vintages.add(obs_id)

        curr_id = row.curr_vintage_id
        curr_val = Decimal(str(row.curr_value)) if row.curr_value is not None else None
        prev_val = Decimal(str(row.prev_value)) if row.prev_value is not None else None
        curr_missing = bool(row.curr_is_missing)
        prev_missing = bool(row.prev_is_missing)

        # Check for change using IS DISTINCT FROM semantics:
        # A change occurs if missing status changed, or (both not missing and values differ)
        is_changed = (curr_missing != prev_missing) or (
            not curr_missing and not prev_missing and curr_val != prev_val
        )

        if not is_changed:
            # Consecutive identical vintages do not form a revision
            continue

        revised_observations.add(obs_id)

        # If this to_vintage_id was already recorded, skip inserting again
        if curr_id in existing_to_vintage_ids:
            continue

        # Increment revision number for this observation
        current_rev_num = obs_max_rev_number.get(obs_id, 0) + 1
        obs_max_rev_number[obs_id] = current_rev_num

        # Compute metrics
        metrics = compute_revision_metrics(prev_val, curr_val)
        lag_days = (row.curr_vintage_date - row.obs_date).days

        rev_record = RevisionHistory(
            observation_id=obs_id,
            series_id=series.id,
            from_vintage_id=row.prev_vintage_id,
            to_vintage_id=curr_id,
            revision_number=current_rev_num,
            observation_date=row.obs_date,
            revision_date=row.curr_vintage_date,
            previous_value=prev_val,
            new_value=curr_val,
            absolute_revision=metrics.absolute_revision,
            relative_revision_pct=metrics.relative_revision_pct,
            direction=metrics.direction,
            revision_type=metrics.revision_type,
            revision_lag_days=lag_days,
            detected_at=now,
        )

        new_revisions_to_insert.append(rev_record)
        revisions_by_vintage_date[row.curr_vintage_date].append(rev_record)
        existing_to_vintage_ids.add(curr_id)

        revisions_created_count += 1
        if metrics.revision_type == "value_revision":
            val_rev_count += 1
            if metrics.direction == "up":
                up_count += 1
            elif metrics.direction == "down":
                down_count += 1
        elif metrics.revision_type == "became_available":
            became_avail_count += 1
        elif metrics.revision_type == "became_missing":
            became_miss_count += 1

    # Insert new revisions
    if new_revisions_to_insert:
        session.add_all(new_revisions_to_insert)
        session.flush()

    # 3. Create revision_batch events grouped by vintage_date
    for v_date, rev_list in revisions_by_vintage_date.items():
        create_revision_batch_event(
            session=session,
            series=series,
            vintage_date=v_date,
            revisions=rev_list,
            is_backfill=is_backfill,
            now=now,
        )

    # 4. Count total observations and classifications for the series
    total_obs_for_series = session.scalars(
        select(func.count(Observation.id)).where(Observation.series_id == series.id)
    ).one()

    # Missing observations: latest vintage is missing
    # We query observations whose latest vintage has is_missing = True
    subq_latest_vintage = (
        select(
            ObservationVintage.observation_id,
            func.max(ObservationVintage.vintage_date).label("max_v_date"),
        )
        .join(Observation, ObservationVintage.observation_id == Observation.id)
        .where(Observation.series_id == series.id)
        .group_by(ObservationVintage.observation_id)
        .subquery()
    )

    missing_obs_count = session.scalars(
        select(func.count(ObservationVintage.id))
        .join(
            subq_latest_vintage,
            (ObservationVintage.observation_id == subq_latest_vintage.c.observation_id)
            & (ObservationVintage.vintage_date == subq_latest_vintage.c.max_v_date),
        )
        .where(ObservationVintage.is_missing.is_(True))
    ).one()

    revised_obs_count = len(revised_observations)
    unchanged_obs_count = max(0, total_obs_for_series - revised_obs_count)

    # 5. Mark baseline and advance detection boundary
    if is_backfill:
        mark_series_event_baseline_set(session, series.code, timestamp=now)

    _set_series_detection_boundary(session, series.code, boundary_dt=now)

    return SeriesDetectionCounts(
        series_code=series.code,
        new_observations_count=new_obs_count,
        revised_observations_count=revised_obs_count,
        unchanged_observations_count=unchanged_obs_count,
        missing_observations_count=missing_obs_count,
        revisions_created_count=revisions_created_count,
        value_revisions_count=val_rev_count,
        became_available_count=became_avail_count,
        became_missing_count=became_miss_count,
        upward_revisions_count=up_count,
        downward_revisions_count=down_count,
    )


def detect_revisions(
    session: Session,
    series_codes: Optional[List[str]] = None,
    now: Optional[datetime] = None,
) -> DetectionResult:
    """Run revision detection across selected (or all active) series.

    Uses per-series transactions and boundaries.
    """
    if now is None:
        now = get_utc_now()

    query = select(EconomicSeries).where(EconomicSeries.is_active.is_(True))
    if series_codes:
        upper_codes = [c.upper() for c in series_codes]
        query = query.where(EconomicSeries.code.in_(upper_codes))

    series_list = list(session.scalars(query).all())
    series_results: Dict[str, SeriesDetectionCounts] = {}

    total_revs = 0
    total_new_obs = 0

    for series in series_list:
        try:
            # Per-series transaction / savepoint
            with session.begin_nested():
                counts = detect_revisions_for_series(session, series, now=now)
            session.commit()
            series_results[series.code] = counts
            total_revs += counts.revisions_created_count
            total_new_obs += counts.new_observations_count
            logger.info(
                f"Completed revision detection for {series.code}: "
                f"{counts.revisions_created_count} revisions created, "
                f"{counts.new_observations_count} new observations."
            )
        except Exception as exc:
            session.rollback()
            logger.error(f"Failed revision detection for series {series.code}: {exc}")
            raise

    return DetectionResult(
        series_results=series_results,
        total_revisions_created=total_revs,
        total_new_observations=total_new_obs,
    )
