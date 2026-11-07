"""End-to-end pipeline orchestrating ingestion, revisions, quality, anomalies, and alerting."""

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from econ_monitor.anomalies.service import AnomalyServiceResult, run_anomaly_detection
from econ_monitor.core.clock import get_utc_now
from econ_monitor.core.logging import get_logger
from econ_monitor.core.yaml_config import load_indicators_config
from econ_monitor.db.models import IngestionMode, IngestionTrigger
from econ_monitor.ingestion.fred_client import FredClient
from econ_monitor.ingestion.service import DatabaseSink, IngestionService
from econ_monitor.notifications.dispatcher import DispatchResult, dispatch_notifications
from econ_monitor.quality.service import QualityServiceResult, run_data_quality_service
from econ_monitor.revisions.detector import DetectionResult, detect_revisions

logger = get_logger("econ_monitor.pipeline")


@dataclass
class PipelineStepResult:
    """Outcome of an individual execution stage in the pipeline."""

    step_name: str
    status: str  # 'success', 'partial', 'failed', 'skipped'
    duration_seconds: float
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class PipelineResult:
    """Consolidated outcome of the entire pipeline run."""

    started_at: datetime
    finished_at: datetime
    mode: str
    success: bool
    steps: List[PipelineStepResult]


def run_pipeline(
    session: Session,
    mode: str = "api",  # 'api' or 'snapshot'
    notify: bool = True,
    series_codes: Optional[List[str]] = None,
    dry_run_notify: bool = False,
    trigger: str = IngestionTrigger.CLI,
    snapshots_dir: str = "data/snapshots",
    now: Optional[datetime] = None,
) -> PipelineResult:
    """Execute the multi-stage economic monitoring pipeline sequentially.

    Stops immediately if an unrecoverable failure occurs in any stage.
    """
    if now is None:
        now = get_utc_now()

    started_at = now
    steps: List[PipelineStepResult] = []
    overall_success = True

    logger.info(f"=== Starting Pipeline Run (mode={mode}, notify={notify}, trigger={trigger}) ===")

    # --------------------------------------------------------------------------
    # Step 1: Ingestion
    # --------------------------------------------------------------------------
    t0 = time.perf_counter()
    try:
        if mode == "snapshot":
            ingest_service = IngestionService()
            ingest_result = ingest_service.import_snapshots_to_database(
                session=session,
                snapshots_dir=snapshots_dir,
                trigger=trigger,
            )
            step_status = "success" if ingest_result.status in ("success", "partial") else "failed"
            steps.append(
                PipelineStepResult(
                    step_name="ingestion_snapshot",
                    status=step_status,
                    duration_seconds=round(time.perf_counter() - t0, 3),
                    details={
                        "series_requested": ingest_result.series_requested,
                        "series_succeeded": ingest_result.series_succeeded,
                        "rows_fetched": ingest_result.rows_fetched,
                        "rows_inserted": ingest_result.rows_inserted,
                    },
                )
            )
            if step_status == "failed":
                overall_success = False

        elif mode == "api":
            client = FredClient()
            ingest_service = IngestionService(fred_client=client)
            sink = DatabaseSink(session=session)
            config = load_indicators_config()

            ingest_result = ingest_service.run_ingestion(
                indicators_config=config,
                sink=sink,
                selected_series_codes=series_codes,
                mode=IngestionMode.API,
                trigger=trigger,
            )
            step_status = "success" if ingest_result.status in ("success", "partial") else "failed"
            steps.append(
                PipelineStepResult(
                    step_name="ingestion_api",
                    status=step_status,
                    duration_seconds=round(time.perf_counter() - t0, 3),
                    details={
                        "series_requested": ingest_result.series_requested,
                        "series_succeeded": ingest_result.series_succeeded,
                        "rows_fetched": ingest_result.rows_fetched,
                        "rows_inserted": ingest_result.rows_inserted,
                    },
                )
            )
            if step_status == "failed":
                overall_success = False

        else:
            raise ValueError(f"Unknown pipeline mode '{mode}'. Expected 'api' or 'snapshot'.")

    except Exception as exc:
        steps.append(
            PipelineStepResult(
                step_name=f"ingestion_{mode}",
                status="failed",
                duration_seconds=round(time.perf_counter() - t0, 3),
                error=str(exc),
            )
        )
        logger.error(f"Pipeline stopped: Ingestion failed: {exc}")
        return PipelineResult(
            started_at=started_at,
            finished_at=get_utc_now(),
            mode=mode,
            success=False,
            steps=steps,
        )

    if not overall_success:
        logger.error("Pipeline stopped: Ingestion step reported failure.")
        return PipelineResult(
            started_at=started_at,
            finished_at=get_utc_now(),
            mode=mode,
            success=False,
            steps=steps,
        )

    # --------------------------------------------------------------------------
    # Step 2: Revision Detection
    # --------------------------------------------------------------------------
    t0 = time.perf_counter()
    try:
        rev_res: DetectionResult = detect_revisions(
            session=session,
            series_codes=series_codes,
            now=now,
        )
        steps.append(
            PipelineStepResult(
                step_name="detect_revisions",
                status="success",
                duration_seconds=round(time.perf_counter() - t0, 3),
                details={
                    "total_revisions_created": rev_res.total_revisions_created,
                    "total_new_observations": rev_res.total_new_observations,
                    "series_evaluated": len(rev_res.series_results),
                },
            )
        )
    except Exception as exc:
        steps.append(
            PipelineStepResult(
                step_name="detect_revisions",
                status="failed",
                duration_seconds=round(time.perf_counter() - t0, 3),
                error=str(exc),
            )
        )
        logger.error(f"Pipeline stopped: Revision detection failed: {exc}")
        return PipelineResult(
            started_at=started_at,
            finished_at=get_utc_now(),
            mode=mode,
            success=False,
            steps=steps,
        )

    # --------------------------------------------------------------------------
    # Step 3: Data Quality Evaluation
    # --------------------------------------------------------------------------
    t0 = time.perf_counter()
    try:
        qual_res: QualityServiceResult = run_data_quality_service(
            session=session,
            series_codes=series_codes,
            now=now,
        )
        steps.append(
            PipelineStepResult(
                step_name="quality_checks",
                status="success",
                duration_seconds=round(time.perf_counter() - t0, 3),
                details={
                    "run_uuid": qual_res.run_uuid,
                    "total_checks_evaluated": qual_res.total_checks_evaluated,
                    "total_issues_emitted": qual_res.total_issues_emitted,
                    "series_evaluated": len(qual_res.series_reports),
                },
            )
        )
    except Exception as exc:
        steps.append(
            PipelineStepResult(
                step_name="quality_checks",
                status="failed",
                duration_seconds=round(time.perf_counter() - t0, 3),
                error=str(exc),
            )
        )
        logger.error(f"Pipeline stopped: Quality checks failed: {exc}")
        return PipelineResult(
            started_at=started_at,
            finished_at=get_utc_now(),
            mode=mode,
            success=False,
            steps=steps,
        )

    # --------------------------------------------------------------------------
    # Step 4: Anomaly Detection
    # --------------------------------------------------------------------------
    t0 = time.perf_counter()
    try:
        target_code = series_codes[0] if series_codes and len(series_codes) == 1 else None
        anom_res: AnomalyServiceResult = run_anomaly_detection(
            session=session,
            series_code=target_code,
            now=now,
        )
        steps.append(
            PipelineStepResult(
                step_name="anomaly_detection",
                status="success",
                duration_seconds=round(time.perf_counter() - t0, 3),
                details={
                    "total_flagged": anom_res.total_flagged,
                    "total_new_records": anom_res.total_new_records,
                    "total_events_emitted": anom_res.total_events_emitted,
                    "series_evaluated": len(anom_res.series_reports),
                },
            )
        )
    except Exception as exc:
        steps.append(
            PipelineStepResult(
                step_name="anomaly_detection",
                status="failed",
                duration_seconds=round(time.perf_counter() - t0, 3),
                error=str(exc),
            )
        )
        logger.error(f"Pipeline stopped: Anomaly detection failed: {exc}")
        return PipelineResult(
            started_at=started_at,
            finished_at=get_utc_now(),
            mode=mode,
            success=False,
            steps=steps,
        )

    # --------------------------------------------------------------------------
    # Step 5: Notification Dispatch
    # --------------------------------------------------------------------------
    if notify:
        t0 = time.perf_counter()
        try:
            disp_res: DispatchResult = dispatch_notifications(
                session=session,
                dry_run=dry_run_notify,
                now=now,
            )
            steps.append(
                PipelineStepResult(
                    step_name="notification_dispatch",
                    status="success",
                    duration_seconds=round(time.perf_counter() - t0, 3),
                    details={
                        "eligible_events": disp_res.eligible_events_found,
                        "matched_subscribers": disp_res.matched_subscribers,
                        "sent_count": disp_res.sent_count,
                        "failed_count": disp_res.failed_count,
                        "skipped_count": disp_res.skipped_count,
                        "retried_count": disp_res.retried_count,
                        "dry_run": disp_res.dry_run,
                    },
                )
            )
        except Exception as exc:
            steps.append(
                PipelineStepResult(
                    step_name="notification_dispatch",
                    status="failed",
                    duration_seconds=round(time.perf_counter() - t0, 3),
                    error=str(exc),
                )
            )
            overall_success = False
            logger.error(f"Notification dispatch failed: {exc}")
    else:
        steps.append(
            PipelineStepResult(
                step_name="notification_dispatch",
                status="skipped",
                duration_seconds=0.0,
                details={"reason": "Disabled via parameter (--no-notify)"},
            )
        )

    session.commit()
    finished_at = get_utc_now()
    logger.info(f"=== Pipeline Run Completed (Success={overall_success}) ===")

    return PipelineResult(
        started_at=started_at,
        finished_at=finished_at,
        mode=mode,
        success=overall_success,
        steps=steps,
    )
