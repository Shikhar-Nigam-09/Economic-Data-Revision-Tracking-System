"""API router for triggering ingestion runs, importing snapshots, and inspecting run logs."""

import threading
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from econ_monitor.api.deps import get_db
from econ_monitor.core.logging import get_logger
from econ_monitor.db.models import (
    EconomicSeries,
    IngestionRun,
    IngestionRunSeries,
    IngestionStatus,
    IngestionTrigger,
)
from econ_monitor.db.session import get_session_factory
from econ_monitor.ingestion.service import IngestionService
from econ_monitor.pipeline import run_pipeline
from econ_monitor.schemas.ingestion import (
    IngestionRunItem,
    IngestionRunListResponse,
    IngestionRunSeriesItem,
    IngestionTriggerResponse,
)

logger = get_logger("econ_monitor.api.routers.ingestion")

router = APIRouter(prefix="/ingestion", tags=["Ingestion"])

# Process-level lock to guard background pipeline runs
_pipeline_lock = threading.Lock()


def _background_pipeline_worker(mode: str, notify: bool) -> None:
    """Worker function executed in the background."""
    if not _pipeline_lock.acquire(blocking=False):
        logger.warning("Background pipeline already acquired by another worker. Skipping.")
        return

    try:
        session_factory = get_session_factory()
        with session_factory() as db:
            logger.info(f"Starting background pipeline run (mode={mode}, notify={notify})")
            run_pipeline(
                session=db,
                mode=mode,
                notify=notify,
                trigger=IngestionTrigger.DASHBOARD,
            )
            logger.info("Background pipeline run completed successfully.")
    except Exception as exc:
        logger.error(f"Background pipeline run failed: {exc}", exc_info=True)
    finally:
        _pipeline_lock.release()


@router.post("/run", response_model=IngestionTriggerResponse, status_code=status.HTTP_202_ACCEPTED)
def trigger_ingestion_run(
    background_tasks: BackgroundTasks,
    mode: str = Query(default="api", description="Ingestion mode: api or snapshot"),
    notify: bool = Query(default=True, description="Whether to dispatch Telegram alerts"),
    db: Session = Depends(get_db),
) -> IngestionTriggerResponse:
    """Trigger the pipeline in the background.

    Returns HTTP 409 Conflict if an active ingestion run is currently in progress.
    """
    # 1. Check database for any currently running ingestion runs
    active_run = db.scalars(
        select(IngestionRun).where(IngestionRun.status == IngestionStatus.RUNNING)
    ).first()

    if active_run is not None or _pipeline_lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An ingestion run is currently in progress. Please wait for it to complete.",
        )

    # 2. Add background task
    run_uuid = str(uuid.uuid4())
    background_tasks.add_task(_background_pipeline_worker, mode=mode, notify=notify)

    return IngestionTriggerResponse(
        run_uuid=run_uuid,
        status="accepted",
        message="Pipeline execution scheduled in background.",
    )


@router.get("/runs", response_model=IngestionRunListResponse)
def list_ingestion_runs(
    status_filter: Optional[str] = Query(
        default=None, alias="status", description="Filter by status"
    ),
    mode_filter: Optional[str] = Query(default=None, alias="mode", description="Filter by mode"),
    limit: int = Query(default=100, ge=1, le=1000, description="Page limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    db: Session = Depends(get_db),
) -> IngestionRunListResponse:
    """Retrieve historical ingestion executions with series-level detail and pagination."""
    stmt = select(IngestionRun)
    if status_filter is not None:
        stmt = stmt.where(IngestionRun.status == status_filter)
    if mode_filter is not None:
        stmt = stmt.where(IngestionRun.mode == mode_filter)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count = db.scalar(count_stmt) or 0

    stmt = (
        stmt.order_by(IngestionRun.started_at.desc(), IngestionRun.id.desc())
        .offset(offset)
        .limit(limit)
    )
    runs = db.scalars(stmt).all()

    items = []
    for r in runs:
        # Load series breakdown
        s_stmt = (
            select(IngestionRunSeries, EconomicSeries.code.label("series_code"))
            .join(EconomicSeries, IngestionRunSeries.series_id == EconomicSeries.id)
            .where(IngestionRunSeries.run_id == r.id)
        )
        s_rows = db.execute(s_stmt).all()
        series_items = [
            IngestionRunSeriesItem(
                id=sr.id,
                run_id=sr.run_id,
                series_id=sr.series_id,
                series_code=s_code,
                status=sr.status,
                vintages_fetched=sr.vintages_fetched,
                observations_new=sr.observations_new,
                vintages_new=sr.vintages_new,
                vintages_unchanged=sr.vintages_unchanged,
                missing_values=sr.missing_values,
                error_message=sr.error_message,
            )
            for sr, s_code in s_rows
        ]

        items.append(
            IngestionRunItem(
                id=r.id,
                run_uuid=r.run_uuid,
                mode=r.mode,
                trigger=r.trigger,
                started_at=r.started_at,
                finished_at=r.finished_at,
                status=r.status,
                series_requested=r.series_requested,
                series_succeeded=r.series_succeeded,
                rows_fetched=r.rows_fetched,
                rows_inserted=r.rows_inserted,
                error_summary=r.error_summary,
                snapshot_manifest_sha256=r.snapshot_manifest_sha256,
                git_commit=r.git_commit,
                series=series_items,
            )
        )

    return IngestionRunListResponse(items=items, total=total_count)


@router.get("/runs/{id}", response_model=IngestionRunItem)
def get_ingestion_run(id: int, db: Session = Depends(get_db)) -> IngestionRunItem:
    """Retrieve details of a single ingestion run by ID."""
    r = db.get(IngestionRun, id)
    if r is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ingestion run with ID {id} not found.",
        )

    s_stmt = (
        select(IngestionRunSeries, EconomicSeries.code.label("series_code"))
        .join(EconomicSeries, IngestionRunSeries.series_id == EconomicSeries.id)
        .where(IngestionRunSeries.run_id == r.id)
    )
    s_rows = db.execute(s_stmt).all()
    series_items = [
        IngestionRunSeriesItem(
            id=sr.id,
            run_id=sr.run_id,
            series_id=sr.series_id,
            series_code=s_code,
            status=sr.status,
            vintages_fetched=sr.vintages_fetched,
            observations_new=sr.observations_new,
            vintages_new=sr.vintages_new,
            vintages_unchanged=sr.vintages_unchanged,
            missing_values=sr.missing_values,
            error_message=sr.error_message,
        )
        for sr, s_code in s_rows
    ]

    return IngestionRunItem(
        id=r.id,
        run_uuid=r.run_uuid,
        mode=r.mode,
        trigger=r.trigger,
        started_at=r.started_at,
        finished_at=r.finished_at,
        status=r.status,
        series_requested=r.series_requested,
        series_succeeded=r.series_succeeded,
        rows_fetched=r.rows_fetched,
        rows_inserted=r.rows_inserted,
        error_summary=r.error_summary,
        snapshot_manifest_sha256=r.snapshot_manifest_sha256,
        git_commit=r.git_commit,
        series=series_items,
    )


@router.post("/import-snapshots", response_model=IngestionRunItem)
def import_snapshots_endpoint(
    snapshots_dir: str = Query(default="data/snapshots", description="Snapshots directory"),
    db: Session = Depends(get_db),
) -> IngestionRunItem:
    """Synchronously verify and import Parquet snapshots into the database."""
    service = IngestionService()
    try:
        res = service.import_snapshots_to_database(
            session=db,
            snapshots_dir=snapshots_dir,
            trigger=IngestionTrigger.MANUAL,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Snapshot import failed: {exc}",
        )

    run_record = db.scalars(
        select(IngestionRun).where(IngestionRun.run_uuid == res.run_uuid)
    ).first()
    if run_record is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Snapshot import completed but run record could not be retrieved.",
        )

    return get_ingestion_run(id=run_record.id, db=db)
