"""Ingestion orchestration service supporting Database and Snapshot sinks with failure isolation."""

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, NamedTuple, Optional, Protocol

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from econ_monitor.core.clock import get_utc_now
from econ_monitor.core.logging import get_logger
from econ_monitor.core.yaml_config import IndicatorConfig, IndicatorsRootConfig
from econ_monitor.db.models import (
    EconomicSeries,
    IngestionMode,
    IngestionRun,
    IngestionRunSeries,
    IngestionSeriesStatus,
    IngestionStatus,
    IngestionTrigger,
)
from econ_monitor.ingestion.fred_client import FredClient
from econ_monitor.ingestion.normalize import normalize_observations
from econ_monitor.ingestion.snapshots import (
    load_series_snapshot_dataframe,
    update_manifest,
    verify_manifest,
    write_series_snapshot,
)
from econ_monitor.ingestion.storage import (
    StorageCounts,
    record_rejected_rows,
    store_vintage_dataframe,
    update_series_fred_metadata,
)

logger = get_logger("econ_monitor.ingestion.service")


class SeriesIngestStats(NamedTuple):
    fred_series_id: str
    status: str  # 'success', 'skipped_no_new_data', 'failed'
    vintages_fetched: int
    observations_new: int
    vintages_new: int
    vintages_unchanged: int
    missing_values: int
    error_message: Optional[str] = None


class IngestionResult(NamedTuple):
    run_uuid: str
    mode: str
    status: str
    series_requested: int
    series_succeeded: int
    rows_fetched: int
    rows_inserted: int
    series_stats: Dict[str, SeriesIngestStats]


class IngestionSink(Protocol):
    """Protocol defining sink operations for storing ingested observations."""

    def is_new_data_available(
        self,
        indicator: IndicatorConfig,
        fred_meta: Dict[str, Any],
    ) -> bool: ...

    def persist(
        self,
        indicator: IndicatorConfig,
        fred_meta: Dict[str, Any],
        df: pd.DataFrame,
        rejected: list,
    ) -> SeriesIngestStats: ...


class DatabaseSink:
    """Sink that writes series metadata, observations, and quality logs to PostgreSQL."""

    def __init__(self, session: Session, ingestion_run_id: Optional[int], run_uuid: str):
        self.session = session
        self.ingestion_run_id = ingestion_run_id
        self.run_uuid = run_uuid

    def _get_series(self, fred_series_id: str) -> Optional[EconomicSeries]:
        stmt = select(EconomicSeries).where(EconomicSeries.fred_series_id == fred_series_id)
        return self.session.scalars(stmt).first()

    def is_new_data_available(
        self,
        indicator: IndicatorConfig,
        fred_meta: Dict[str, Any],
    ) -> bool:
        series = self._get_series(indicator.fred_series_id)
        if series is None or series.fred_last_updated is None:
            return True

        last_updated_str = fred_meta.get("last_updated")
        if not last_updated_str:
            return True

        try:
            fred_dt = datetime.fromisoformat(last_updated_str.strip())
            # Normalize fred_dt to UTC
            if fred_dt.tzinfo is not None:
                fred_dt_utc = fred_dt.astimezone(timezone.utc)
            else:
                fred_dt_utc = fred_dt.replace(tzinfo=timezone.utc)

            series_dt = series.fred_last_updated
            if series_dt.tzinfo is not None:
                series_dt_utc = series_dt.astimezone(timezone.utc)
            else:
                series_dt_utc = series_dt.replace(tzinfo=timezone.utc)

            return fred_dt_utc > series_dt_utc
        except Exception as exc:
            logger.warning(f"Error checking last_updated timestamp: {exc}")
            return True

    def persist(
        self,
        indicator: IndicatorConfig,
        fred_meta: Dict[str, Any],
        df: pd.DataFrame,
        rejected: list,
    ) -> SeriesIngestStats:
        series = self._get_series(indicator.fred_series_id)
        if series is None:
            raise ValueError(
                f"Series with FRED ID '{indicator.fred_series_id}' not found in database."
            )

        # Update metadata
        update_series_fred_metadata(self.session, series, fred_meta)

        # Record rejected rows
        record_rejected_rows(
            self.session,
            run_uuid=self.run_uuid,
            series_id=series.id,
            rejected_rows=rejected,
            ingestion_run_id=self.ingestion_run_id,
        )

        # Store observations and vintages
        counts: StorageCounts = store_vintage_dataframe(
            self.session,
            series=series,
            df=df,
            ingestion_run_id=self.ingestion_run_id,
        )

        return SeriesIngestStats(
            fred_series_id=indicator.fred_series_id,
            status=IngestionSeriesStatus.SUCCESS,
            vintages_fetched=len(df),
            observations_new=counts.new_observations,
            vintages_new=counts.new_vintages,
            vintages_unchanged=counts.skipped_vintages,
            missing_values=counts.missing_values,
        )


class SnapshotSink:
    """Sink that writes observations and manifests to Parquet files without a database."""

    def __init__(self, snapshots_dir: str = "data/snapshots"):
        self.snapshots_dir = snapshots_dir

    def is_new_data_available(
        self,
        indicator: IndicatorConfig,
        fred_meta: Dict[str, Any],
    ) -> bool:
        # Check metadata.json inside snapshot directory
        from pathlib import Path

        meta_file = Path(self.snapshots_dir) / indicator.fred_series_id / "metadata.json"
        if not meta_file.exists():
            return True

        try:
            import json

            with open(meta_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            return saved.get("last_updated") != fred_meta.get("last_updated")
        except Exception:
            return True

    def persist(
        self,
        indicator: IndicatorConfig,
        fred_meta: Dict[str, Any],
        df: pd.DataFrame,
        rejected: list,
    ) -> SeriesIngestStats:
        res = write_series_snapshot(
            base_dir=self.snapshots_dir,
            fred_series_id=indicator.fred_series_id,
            df=df,
            metadata=fred_meta,
        )
        return SeriesIngestStats(
            fred_series_id=indicator.fred_series_id,
            status=IngestionSeriesStatus.SUCCESS,
            vintages_fetched=len(df),
            observations_new=res.rows_added,
            vintages_new=res.rows_added,
            vintages_unchanged=res.rows_total - res.rows_added,
            missing_values=int(df["is_missing"].sum()) if not df.empty else 0,
        )


class IngestionService:
    """Orchestrator for collecting, normalizing, and storing macroeconomic series."""

    def __init__(self, fred_client: Optional[FredClient] = None):
        self.fred_client = fred_client

    def run_ingestion(
        self,
        indicators_config: IndicatorsRootConfig,
        sink: IngestionSink,
        selected_series_codes: Optional[List[str]] = None,
        force_full: bool = False,
        mode: str = IngestionMode.API,
        trigger: str = IngestionTrigger.MANUAL,
        session: Optional[Session] = None,
    ) -> IngestionResult:
        """Run the ingestion pipeline with failure isolation across series."""
        run_uuid = str(uuid.uuid4())
        started_at = get_utc_now()
        stats_map: Dict[str, SeriesIngestStats] = {}

        # Target active indicators or specific subset
        target_indicators = [
            ind
            for ind in indicators_config.indicators
            if ind.active
            and (
                not selected_series_codes
                or ind.code.upper() in [c.upper() for c in selected_series_codes]
            )
        ]

        # 1. Initialize IngestionRun in DB if session provided
        ingestion_run_record: Optional[IngestionRun] = None
        if session is not None and mode == IngestionMode.API:
            ingestion_run_record = IngestionRun(
                run_uuid=run_uuid,
                mode=mode,
                trigger=trigger,
                started_at=started_at,
                status=IngestionStatus.RUNNING,
                series_requested=len(target_indicators),
                series_succeeded=0,
                rows_fetched=0,
                rows_inserted=0,
            )
            session.add(ingestion_run_record)
            session.flush()

        rows_fetched_total = 0
        rows_inserted_total = 0
        succeeded_count = 0
        failed_count = 0

        # 2. Process each series with isolated error handling
        for indicator in target_indicators:
            try:
                # Fetch metadata
                if self.fred_client is None:
                    raise ValueError("FredClient must be configured to fetch data.")

                meta = self.fred_client.get_series_metadata(indicator.fred_series_id)

                # Incremental skip check
                if not force_full and not sink.is_new_data_available(indicator, meta):
                    logger.info(
                        f"Skipping {indicator.code} ({indicator.fred_series_id}): no new data."
                    )
                    stat = SeriesIngestStats(
                        fred_series_id=indicator.fred_series_id,
                        status=IngestionSeriesStatus.SKIPPED_NO_NEW_DATA,
                        vintages_fetched=0,
                        observations_new=0,
                        vintages_new=0,
                        vintages_unchanged=0,
                        missing_values=0,
                    )
                    stats_map[indicator.code] = stat
                    succeeded_count += 1
                    continue

                # Fetch vintage observations
                raw_obs = self.fred_client.get_vintage_observations(
                    series_id=indicator.fred_series_id,
                    observation_start=str(indicator.history_start),
                    realtime_start="1776-07-04",
                    realtime_end="9999-12-31",
                )

                # Normalize rows
                norm_res = normalize_observations(
                    raw_observations=raw_obs,
                    fred_series_id=indicator.fred_series_id,
                    history_start=indicator.history_start,
                    source="ALFRED",
                    retrieved_at=started_at,
                )

                # Persist to sink
                stat = sink.persist(
                    indicator=indicator,
                    fred_meta=meta,
                    df=norm_res.df,
                    rejected=norm_res.rejected,
                )
                stats_map[indicator.code] = stat
                rows_fetched_total += stat.vintages_fetched
                rows_inserted_total += stat.vintages_new
                succeeded_count += 1

                # Record series run in DB if active session
                if session is not None and ingestion_run_record is not None:
                    stmt_s = select(EconomicSeries.id).where(
                        EconomicSeries.fred_series_id == indicator.fred_series_id
                    )
                    s_id = session.scalars(stmt_s).first()
                    if s_id:
                        s_run = IngestionRunSeries(
                            run_id=ingestion_run_record.id,
                            series_id=s_id,
                            status=stat.status,
                            vintages_fetched=stat.vintages_fetched,
                            observations_new=stat.observations_new,
                            vintages_new=stat.vintages_new,
                            vintages_unchanged=stat.vintages_unchanged,
                            missing_values=stat.missing_values,
                        )
                        session.add(s_run)
                        session.flush()

            except Exception as exc:
                logger.error(
                    f"Failed to ingest series {indicator.code} ({indicator.fred_series_id}): {exc}"
                )
                failed_count += 1
                stat = SeriesIngestStats(
                    fred_series_id=indicator.fred_series_id,
                    status=IngestionSeriesStatus.FAILED,
                    vintages_fetched=0,
                    observations_new=0,
                    vintages_new=0,
                    vintages_unchanged=0,
                    missing_values=0,
                    error_message=str(exc),
                )
                stats_map[indicator.code] = stat

                if session is not None and ingestion_run_record is not None:
                    stmt_s = select(EconomicSeries.id).where(
                        EconomicSeries.fred_series_id == indicator.fred_series_id
                    )
                    s_id = session.scalars(stmt_s).first()
                    if s_id:
                        s_run = IngestionRunSeries(
                            run_id=ingestion_run_record.id,
                            series_id=s_id,
                            status=IngestionSeriesStatus.FAILED,
                            vintages_fetched=0,
                            observations_new=0,
                            vintages_new=0,
                            vintages_unchanged=0,
                            missing_values=0,
                            error_message=str(exc),
                        )
                        session.add(s_run)
                        session.flush()

        # Update manifest if snapshot mode
        if isinstance(sink, SnapshotSink):
            update_manifest(sink.snapshots_dir)

        # 3. Determine overall run status
        if failed_count == 0:
            final_status = IngestionStatus.SUCCESS
        elif succeeded_count > 0:
            final_status = IngestionStatus.PARTIAL
        else:
            final_status = IngestionStatus.FAILED

        # Update IngestionRun record
        if session is not None and ingestion_run_record is not None:
            ingestion_run_record.finished_at = get_utc_now()
            ingestion_run_record.status = final_status
            ingestion_run_record.series_succeeded = succeeded_count
            ingestion_run_record.rows_fetched = rows_fetched_total
            ingestion_run_record.rows_inserted = rows_inserted_total
            if failed_count > 0:
                err_msgs = [
                    f"{c}: {s.error_message}" for c, s in stats_map.items() if s.error_message
                ]
                ingestion_run_record.error_summary = " | ".join(err_msgs)
            session.commit()

        return IngestionResult(
            run_uuid=run_uuid,
            mode=mode,
            status=final_status,
            series_requested=len(target_indicators),
            series_succeeded=succeeded_count,
            rows_fetched=rows_fetched_total,
            rows_inserted=rows_inserted_total,
            series_stats=stats_map,
        )

    def import_snapshots_to_database(
        self,
        session: Session,
        snapshots_dir: str = "data/snapshots",
        trigger: str = IngestionTrigger.MANUAL,
    ) -> IngestionResult:
        """Verify snapshots and import into PostgreSQL using store_vintage_dataframe."""
        run_uuid = str(uuid.uuid4())
        started_at = get_utc_now()

        # 1. Verify manifest and checksums
        manifest = verify_manifest(snapshots_dir)
        manifest_hash = hashlib.sha256(json_dumps_sorted(manifest)).hexdigest()

        series_entries = manifest.get("series", {})
        ingestion_run = IngestionRun(
            run_uuid=run_uuid,
            mode=IngestionMode.SNAPSHOT,
            trigger=trigger,
            started_at=started_at,
            status=IngestionStatus.RUNNING,
            series_requested=len(series_entries),
            series_succeeded=0,
            rows_fetched=0,
            rows_inserted=0,
            snapshot_manifest_sha256=manifest_hash,
            git_commit=manifest.get("git_commit"),
        )
        session.add(ingestion_run)
        session.flush()

        stats_map: Dict[str, SeriesIngestStats] = {}
        rows_fetched_total = 0
        rows_inserted_total = 0
        succeeded_count = 0
        failed_count = 0

        for fred_id in series_entries.keys():
            try:
                stmt = select(EconomicSeries).where(EconomicSeries.fred_series_id == fred_id)
                series = session.scalars(stmt).first()
                if not series:
                    logger.warning(
                        f"Series '{fred_id}' in snapshot manifest not found in database; skipping."
                    )
                    continue

                df = load_series_snapshot_dataframe(snapshots_dir, fred_id)
                counts = store_vintage_dataframe(
                    session, series, df, ingestion_run_id=ingestion_run.id
                )

                stat = SeriesIngestStats(
                    fred_series_id=fred_id,
                    status=IngestionSeriesStatus.SUCCESS,
                    vintages_fetched=len(df),
                    observations_new=counts.new_observations,
                    vintages_new=counts.new_vintages,
                    vintages_unchanged=counts.skipped_vintages,
                    missing_values=counts.missing_values,
                )
                stats_map[series.code] = stat
                rows_fetched_total += len(df)
                rows_inserted_total += counts.new_vintages
                succeeded_count += 1

                # Record series run breakdown
                s_run = IngestionRunSeries(
                    run_id=ingestion_run.id,
                    series_id=series.id,
                    status=IngestionSeriesStatus.SUCCESS,
                    vintages_fetched=len(df),
                    observations_new=counts.new_observations,
                    vintages_new=counts.new_vintages,
                    vintages_unchanged=counts.skipped_vintages,
                    missing_values=counts.missing_values,
                )
                session.add(s_run)
                session.flush()

            except Exception as exc:
                logger.error(f"Failed to import snapshot for {fred_id}: {exc}")
                failed_count += 1
                stat = SeriesIngestStats(
                    fred_series_id=fred_id,
                    status=IngestionSeriesStatus.FAILED,
                    vintages_fetched=0,
                    observations_new=0,
                    vintages_new=0,
                    vintages_unchanged=0,
                    missing_values=0,
                    error_message=str(exc),
                )
                stats_map[fred_id] = stat

        final_status = (
            IngestionStatus.SUCCESS
            if failed_count == 0
            else (IngestionStatus.PARTIAL if succeeded_count > 0 else IngestionStatus.FAILED)
        )

        ingestion_run.finished_at = get_utc_now()
        ingestion_run.status = final_status
        ingestion_run.series_succeeded = succeeded_count
        ingestion_run.rows_fetched = rows_fetched_total
        ingestion_run.rows_inserted = rows_inserted_total
        session.commit()

        return IngestionResult(
            run_uuid=run_uuid,
            mode=IngestionMode.SNAPSHOT,
            status=final_status,
            series_requested=len(series_entries),
            series_succeeded=succeeded_count,
            rows_fetched=rows_fetched_total,
            rows_inserted=rows_inserted_total,
            series_stats=stats_map,
        )


def json_dumps_sorted(data: Any) -> bytes:
    """Helper to dump deterministic sorted JSON bytes for hashing."""
    import json

    return json.dumps(data, sort_keys=True).encode("utf-8")
