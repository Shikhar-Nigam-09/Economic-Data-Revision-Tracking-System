"""API router for revision history and single revision lookup."""

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from econ_monitor.api.deps import get_db
from econ_monitor.db.models import EconomicSeries, RevisionHistory
from econ_monitor.schemas.revisions import RevisionItem, RevisionListResponse

router = APIRouter(tags=["Revisions"])


@router.get("/revisions", response_model=RevisionListResponse)
def list_revisions(
    series: Optional[str] = Query(default=None, description="Series code filter (e.g. CPI)"),
    start_date: Optional[date] = Query(
        default=None, description="Revision date start filter (inclusive)"
    ),
    end_date: Optional[date] = Query(
        default=None, description="Revision date end filter (inclusive)"
    ),
    direction: Optional[str] = Query(
        default=None, description="Revision direction filter: up, down, or None"
    ),
    type: Optional[str] = Query(
        default=None,
        description="Revision type filter: value_revision, became_available, became_missing",
    ),
    min_abs_pct: Optional[Decimal] = Query(
        default=None, ge=0, description="Minimum absolute relative revision percentage"
    ),
    limit: int = Query(default=100, ge=1, le=1000, description="Page limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    db: Session = Depends(get_db),
) -> RevisionListResponse:
    """Retrieve revision events with filtering and pagination."""
    stmt = select(RevisionHistory, EconomicSeries.code.label("series_code")).join(
        EconomicSeries, RevisionHistory.series_id == EconomicSeries.id
    )

    if series is not None:
        stmt = stmt.where(EconomicSeries.code == series.upper())
    if start_date is not None:
        stmt = stmt.where(RevisionHistory.revision_date >= start_date)
    if end_date is not None:
        stmt = stmt.where(RevisionHistory.revision_date <= end_date)
    if direction is not None:
        stmt = stmt.where(RevisionHistory.direction == direction)
    if type is not None:
        stmt = stmt.where(RevisionHistory.revision_type == type)
    if min_abs_pct is not None:
        stmt = stmt.where(func.abs(RevisionHistory.relative_revision_pct) >= min_abs_pct)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count = db.scalar(count_stmt) or 0

    stmt = (
        stmt.order_by(RevisionHistory.revision_date.desc(), RevisionHistory.id.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = db.execute(stmt).all()

    items = []
    for rev, s_code in rows:
        item = RevisionItem(
            id=rev.id,
            observation_id=rev.observation_id,
            series_id=rev.series_id,
            series_code=s_code,
            from_vintage_id=rev.from_vintage_id,
            to_vintage_id=rev.to_vintage_id,
            revision_number=rev.revision_number,
            observation_date=rev.observation_date,
            revision_date=rev.revision_date,
            previous_value=rev.previous_value,
            new_value=rev.new_value,
            absolute_revision=rev.absolute_revision,
            relative_revision_pct=rev.relative_revision_pct,
            direction=rev.direction,
            revision_type=rev.revision_type,
            revision_lag_days=rev.revision_lag_days,
            detected_at=rev.detected_at,
        )
        items.append(item)

    return RevisionListResponse(items=items, total=total_count)


@router.get("/revisions/{id}", response_model=RevisionItem)
def get_revision(id: int, db: Session = Depends(get_db)) -> RevisionItem:
    """Retrieve details of a single revision event by ID."""
    stmt = (
        select(RevisionHistory, EconomicSeries.code.label("series_code"))
        .join(EconomicSeries, RevisionHistory.series_id == EconomicSeries.id)
        .where(RevisionHistory.id == id)
    )
    result = db.execute(stmt).first()
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Revision with ID {id} not found.",
        )

    rev, s_code = result
    return RevisionItem(
        id=rev.id,
        observation_id=rev.observation_id,
        series_id=rev.series_id,
        series_code=s_code,
        from_vintage_id=rev.from_vintage_id,
        to_vintage_id=rev.to_vintage_id,
        revision_number=rev.revision_number,
        observation_date=rev.observation_date,
        revision_date=rev.revision_date,
        previous_value=rev.previous_value,
        new_value=rev.new_value,
        absolute_revision=rev.absolute_revision,
        relative_revision_pct=rev.relative_revision_pct,
        direction=rev.direction,
        revision_type=rev.revision_type,
        revision_lag_days=rev.revision_lag_days,
        detected_at=rev.detected_at,
    )
