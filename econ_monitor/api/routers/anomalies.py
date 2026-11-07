"""API router for anomaly detection records, status, and manual trigger."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from econ_monitor.anomalies.service import run_anomaly_detection
from econ_monitor.api.deps import get_db
from econ_monitor.core.clock import get_utc_now
from econ_monitor.db.models import (
    AnomalyRecord,
    AnomalyStatus,
    AppState,
    EconomicSeries,
    Observation,
    ObservationVintage,
)
from econ_monitor.schemas.anomalies import (
    AnomalyItem,
    AnomalyListResponse,
    AnomalyPatchRequest,
    AnomalyRunResponse,
    AnomalyStatusResponse,
)

router = APIRouter(prefix="/anomalies", tags=["Anomalies"])


@router.get("", response_model=AnomalyListResponse)
def list_anomalies(
    series: Optional[str] = Query(default=None, description="Series code filter (e.g. GDP, CPI)"),
    method: Optional[str] = Query(
        default=None, description="Detection method: zscore or isolation_forest"
    ),
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description="Review status: flagged, reviewed, or dismissed",
    ),
    limit: int = Query(default=100, ge=1, le=1000, description="Page limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    db: Session = Depends(get_db),
) -> AnomalyListResponse:
    """List anomaly detection records with superseded vintage status and pagination."""
    # Subquery to determine max vintage_id or max vintage_date per observation
    max_vintage_subq = (
        select(
            ObservationVintage.observation_id,
            func.max(ObservationVintage.vintage_date).label("max_v_date"),
        )
        .group_by(ObservationVintage.observation_id)
        .subquery()
    )

    stmt = (
        select(
            AnomalyRecord,
            EconomicSeries.code.label("series_code"),
            Observation.observation_date,
            ObservationVintage.vintage_date,
            ObservationVintage.value.label("vintage_value"),
            max_vintage_subq.c.max_v_date,
        )
        .join(EconomicSeries, AnomalyRecord.series_id == EconomicSeries.id)
        .join(Observation, AnomalyRecord.observation_id == Observation.id)
        .join(ObservationVintage, AnomalyRecord.vintage_id == ObservationVintage.id)
        .outerjoin(
            max_vintage_subq,
            AnomalyRecord.observation_id == max_vintage_subq.c.observation_id,
        )
    )

    if series is not None:
        stmt = stmt.where(EconomicSeries.code == series.upper())
    if method is not None:
        stmt = stmt.where(AnomalyRecord.method == method)
    if status_filter is not None:
        stmt = stmt.where(AnomalyRecord.status == status_filter)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count = db.scalar(count_stmt) or 0

    stmt = (
        stmt.order_by(AnomalyRecord.detected_at.desc(), AnomalyRecord.id.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = db.execute(stmt).all()

    items = []
    for rec, s_code, obs_date, v_date, v_val, max_v_date in rows:
        is_superseded = bool(max_v_date and v_date < max_v_date)
        items.append(
            AnomalyItem(
                id=rec.id,
                series_id=rec.series_id,
                series_code=s_code,
                observation_id=rec.observation_id,
                observation_date=obs_date,
                vintage_id=rec.vintage_id,
                vintage_date=v_date,
                vintage_value=v_val,
                method=rec.method,
                score=rec.score,
                threshold=rec.threshold,
                features=rec.features,
                status=rec.status,
                reviewed_at=rec.reviewed_at,
                note=rec.note,
                detected_at=rec.detected_at,
                is_superseded_vintage=is_superseded,
            )
        )

    return AnomalyListResponse(items=items, total=total_count)


@router.get("/status", response_model=AnomalyStatusResponse)
def get_anomaly_status(db: Session = Depends(get_db)) -> AnomalyStatusResponse:
    """Retrieve the execution summary of the latest anomaly detection run."""
    state_obj = db.get(AppState, "anomaly_last_run")
    if not state_obj or not state_obj.value:
        return AnomalyStatusResponse(
            last_run_at=None,
            total_flagged=0,
            total_new_records=0,
            total_events_emitted=0,
            series={},
        )

    val = state_obj.value
    return AnomalyStatusResponse(
        last_run_at=val.get("run_at"),
        total_flagged=val.get("total_flagged", 0),
        total_new_records=val.get("total_new_records", 0),
        total_events_emitted=val.get("total_events_emitted", 0),
        series=val.get("series", {}),
    )


@router.patch("/{id}", response_model=AnomalyItem)
def update_anomaly_status(
    id: int,
    patch: AnomalyPatchRequest,
    db: Session = Depends(get_db),
) -> AnomalyItem:
    """Update review status and optional investigator note for an anomaly record."""
    rec = db.get(AnomalyRecord, id)
    if rec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Anomaly record with ID {id} not found.",
        )

    now = get_utc_now()
    status_val = patch.status.value if hasattr(patch.status, "value") else str(patch.status)
    rec.status = AnomalyStatus(status_val)
    rec.reviewed_at = now
    if patch.note is not None:
        rec.note = patch.note

    db.commit()
    db.refresh(rec)

    # Fetch context to construct full AnomalyItem
    series = db.get(EconomicSeries, rec.series_id)
    obs = db.get(Observation, rec.observation_id)
    vintage = db.get(ObservationVintage, rec.vintage_id)

    max_v_date = db.scalar(
        select(func.max(ObservationVintage.vintage_date)).where(
            ObservationVintage.observation_id == rec.observation_id
        )
    )
    is_superseded = bool(max_v_date and vintage and vintage.vintage_date < max_v_date)

    return AnomalyItem(
        id=rec.id,
        series_id=rec.series_id,
        series_code=series.code if series else "",
        observation_id=rec.observation_id,
        observation_date=obs.observation_date if obs else None,
        vintage_id=rec.vintage_id,
        vintage_date=vintage.vintage_date if vintage else None,
        vintage_value=vintage.value if vintage else None,
        method=rec.method,
        score=rec.score,
        threshold=rec.threshold,
        features=rec.features,
        status=rec.status,
        reviewed_at=rec.reviewed_at,
        note=rec.note,
        detected_at=rec.detected_at,
        is_superseded_vintage=is_superseded,
    )


@router.post("/run", response_model=AnomalyRunResponse)
def trigger_anomaly_run(
    series: Optional[str] = Query(default=None, description="Optional single series code"),
    db: Session = Depends(get_db),
) -> AnomalyRunResponse:
    """Execute anomaly detection models (z-score and Isolation Forest) synchronously."""
    res = run_anomaly_detection(db, series_code=series)
    db.commit()

    series_summary = {}
    for code, rep in res.series_reports.items():
        series_summary[code] = {
            "zscore_flagged": rep.zscore_flagged,
            "zscore_new_records": rep.zscore_new_records,
            "iforest_ran": rep.iforest_ran,
            "iforest_flagged": rep.iforest_flagged,
            "iforest_new_records": rep.iforest_new_records,
            "iforest_skipped_reason": rep.iforest_skipped_reason,
            "events_emitted": rep.events_emitted,
        }

    return AnomalyRunResponse(
        run_at=res.run_at,
        total_flagged=res.total_flagged,
        total_new_records=res.total_new_records,
        total_events_emitted=res.total_events_emitted,
        series=series_summary,
    )
