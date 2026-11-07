"""Database persistence layer for series metadata, observations, and historical vintages."""

from datetime import datetime, timezone
from typing import Any, Dict, List, NamedTuple, Optional, Set, Tuple

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from econ_monitor.core.clock import get_utc_now
from econ_monitor.core.logging import get_logger
from econ_monitor.db.models import (
    DataQualityLog,
    EconomicSeries,
    Observation,
    ObservationVintage,
)
from econ_monitor.ingestion.normalize import RejectedRow

logger = get_logger("econ_monitor.ingestion.storage")


class StorageCounts(NamedTuple):
    """Statistics returned after storing a vintage dataframe."""

    new_observations: int
    new_vintages: int
    skipped_vintages: int
    missing_values: int


def update_series_fred_metadata(
    session: Session,
    series: EconomicSeries,
    fred_meta: Dict[str, Any],
) -> bool:
    """Update economic_series columns from live FRED metadata.

    Updates: title, frequency, units, seasonal_adjustment, fred_last_updated,
    and change_unit_label if not previously configured.
    """
    changed = False

    title = fred_meta.get("title")
    if title and series.title != title:
        series.title = title
        changed = True

    frequency = fred_meta.get("frequency_short")
    if frequency and series.frequency != frequency:
        series.frequency = frequency
        changed = True

    units = fred_meta.get("units")
    if units and series.units != units:
        series.units = units
        changed = True

    seasonal = fred_meta.get("seasonal_adjustment")
    if seasonal and series.seasonal_adjustment != seasonal:
        series.seasonal_adjustment = seasonal
        changed = True

    last_updated_str = fred_meta.get("last_updated")
    if last_updated_str:
        try:
            # Parse FRED timestamp: e.g. "2024-03-28 07:31:02-05"
            clean_ts = last_updated_str.strip()
            parsed_dt = datetime.fromisoformat(clean_ts)
            if parsed_dt.tzinfo is not None:
                parsed_dt = parsed_dt.astimezone(timezone.utc)
            else:
                parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)

            if series.fred_last_updated != parsed_dt:
                series.fred_last_updated = parsed_dt
                changed = True
        except (ValueError, TypeError) as exc:
            logger.warning(f"Could not parse FRED last_updated '{last_updated_str}': {exc}")

    # Set default change_unit_label from units if not set in config
    if not series.change_unit_label and series.units:
        series.change_unit_label = series.units
        changed = True

    if changed:
        series.updated_at = get_utc_now()
        session.flush()

    return changed


def record_rejected_rows(
    session: Session,
    run_uuid: str,
    series_id: int,
    rejected_rows: List[RejectedRow],
    ingestion_run_id: Optional[int] = None,
) -> int:
    """Record rejected rows into data_quality_logs."""
    if not rejected_rows:
        return 0

    now = get_utc_now()
    logs: List[DataQualityLog] = []

    for item in rejected_rows:
        log = DataQualityLog(
            run_uuid=run_uuid,
            series_id=series_id,
            ingestion_run_id=ingestion_run_id,
            check_name=item.check_name,
            severity="error",
            passed=False,
            affected_count=1,
            message=item.reason,
            details={"raw_data": str(item.raw_data)},
            checked_at=now,
        )
        logs.append(log)

    session.add_all(logs)
    session.flush()
    return len(logs)


def store_vintage_dataframe(
    session: Session,
    series: EconomicSeries,
    df: pd.DataFrame,
    ingestion_run_id: Optional[int] = None,
) -> StorageCounts:
    """Persist normalized observation and vintage records into PostgreSQL.

    Guarantees:
    - Never updates or deletes existing observation_vintages rows.
    - Idempotent: inserting an already existing vintage is safely ignored.
    - Accurately reports new observations, new vintages, and skipped vintages.
    """
    if df.empty:
        return StorageCounts(
            new_observations=0,
            new_vintages=0,
            skipped_vintages=0,
            missing_values=0,
        )

    now = get_utc_now()

    # 1. Fetch existing observations for this series
    stmt_obs = select(Observation.observation_date, Observation.id).where(
        Observation.series_id == series.id
    )
    existing_obs_map: Dict[Any, int] = dict(session.execute(stmt_obs).all())

    # Determine unique observation dates in incoming dataframe
    unique_dates = df["observation_date"].drop_duplicates().tolist()
    new_obs_count = 0

    new_obs_records: List[Observation] = []
    for o_date in unique_dates:
        if o_date not in existing_obs_map:
            new_obs = Observation(
                series_id=series.id,
                observation_date=o_date,
                first_seen_at=now,
                ingestion_run_id=ingestion_run_id,
            )
            new_obs_records.append(new_obs)

    if new_obs_records:
        session.add_all(new_obs_records)
        session.flush()
        new_obs_count = len(new_obs_records)
        # Refresh observation map with newly inserted IDs
        for obs in new_obs_records:
            existing_obs_map[obs.observation_date] = obs.id

    # 2. Fetch existing (observation_id, vintage_date) pairs to determine inserts vs skips
    target_obs_ids = list(existing_obs_map.values())
    stmt_vintages = select(
        ObservationVintage.observation_id, ObservationVintage.vintage_date
    ).where(ObservationVintage.observation_id.in_(target_obs_ids))
    existing_vintages: Set[Tuple[int, Any]] = set(session.execute(stmt_vintages).all())

    new_vintage_records: List[ObservationVintage] = []
    skipped_count = 0
    missing_count = 0

    for row in df.itertuples(index=False):
        obs_id = existing_obs_map[row.observation_date]
        v_key = (obs_id, row.vintage_date)

        if v_key in existing_vintages:
            skipped_count += 1
            continue

        if row.is_missing:
            missing_count += 1

        v_record = ObservationVintage(
            observation_id=obs_id,
            vintage_date=row.vintage_date,
            source_realtime_end=row.source_realtime_end,
            value=row.value,
            is_missing=row.is_missing,
            ingestion_run_id=ingestion_run_id,
            retrieved_at=row.retrieved_at,
            source=row.source,
        )
        new_vintage_records.append(v_record)
        existing_vintages.add(v_key)

    if new_vintage_records:
        session.add_all(new_vintage_records)
        session.flush()

    return StorageCounts(
        new_observations=new_obs_count,
        new_vintages=len(new_vintage_records),
        skipped_vintages=skipped_count,
        missing_values=missing_count,
    )
