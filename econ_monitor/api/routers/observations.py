"""API router for observation time series and historical vintages."""

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from econ_monitor.api.deps import get_db
from econ_monitor.core.errors import SeriesNotFoundError
from econ_monitor.db.models import EconomicSeries, Observation, ObservationVintage
from econ_monitor.schemas.observations import (
    ObservationItem,
    ObservationListResponse,
    ObservationVintageItem,
    ObservationVintageListResponse,
)

router = APIRouter(tags=["Observations"])


@router.get("/series/{code}/observations", response_model=ObservationListResponse)
def get_series_observations(
    code: str,
    start: Optional[date] = Query(default=None, description="Start observation date (inclusive)"),
    end: Optional[date] = Query(default=None, description="End observation date (inclusive)"),
    view: Literal["latest", "first", "as_of"] = Query(
        default="latest", description="Observation vintage view mode: latest, first, or as_of"
    ),
    as_of: Optional[date] = Query(
        default=None, description="Vintage cutoff date (required if view='as_of')"
    ),
    limit: int = Query(default=100, ge=1, le=1000, description="Page limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    db: Session = Depends(get_db),
) -> ObservationListResponse:
    """Retrieve observations for an indicator under latest, first release, or as-of views."""
    series = db.scalars(select(EconomicSeries).where(EconomicSeries.code == code.upper())).first()
    if series is None:
        raise SeriesNotFoundError(code)

    if view == "as_of" and as_of is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The 'as_of' query parameter is required when view='as_of'.",
        )

    # 1. Target vintage date subquery based on view
    if view == "as_of":
        assert as_of is not None
        target_vint_subq = (
            select(
                ObservationVintage.observation_id,
                func.max(ObservationVintage.vintage_date).label("target_v_date"),
                func.count(ObservationVintage.id).label("v_count"),
            )
            .join(Observation, ObservationVintage.observation_id == Observation.id)
            .where(
                Observation.series_id == series.id,
                ObservationVintage.vintage_date <= as_of,
            )
            .group_by(ObservationVintage.observation_id)
            .subquery()
        )
    elif view == "first":
        target_vint_subq = (
            select(
                ObservationVintage.observation_id,
                func.min(ObservationVintage.vintage_date).label("target_v_date"),
                func.count(ObservationVintage.id).label("v_count"),
            )
            .join(Observation, ObservationVintage.observation_id == Observation.id)
            .where(Observation.series_id == series.id)
            .group_by(ObservationVintage.observation_id)
            .subquery()
        )
    else:  # view == "latest"
        target_vint_subq = (
            select(
                ObservationVintage.observation_id,
                func.max(ObservationVintage.vintage_date).label("target_v_date"),
                func.count(ObservationVintage.id).label("v_count"),
            )
            .join(Observation, ObservationVintage.observation_id == Observation.id)
            .where(Observation.series_id == series.id)
            .group_by(ObservationVintage.observation_id)
            .subquery()
        )

    # 2. Main query joining Observation, target vintage, and its value
    stmt = (
        select(
            Observation.id.label("observation_id"),
            Observation.observation_date,
            ObservationVintage.id.label("vintage_id"),
            ObservationVintage.vintage_date,
            ObservationVintage.value,
            ObservationVintage.is_missing,
            target_vint_subq.c.v_count.label("vintage_count"),
        )
        .join(Observation, ObservationVintage.observation_id == Observation.id)
        .join(
            target_vint_subq,
            (ObservationVintage.observation_id == target_vint_subq.c.observation_id)
            & (ObservationVintage.vintage_date == target_vint_subq.c.target_v_date),
        )
        .where(Observation.series_id == series.id)
    )

    if start is not None:
        stmt = stmt.where(Observation.observation_date >= start)
    if end is not None:
        stmt = stmt.where(Observation.observation_date <= end)

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count = db.scalar(count_stmt) or 0

    # Paginate
    stmt = stmt.order_by(Observation.observation_date.asc()).offset(offset).limit(limit)
    rows = db.execute(stmt).all()

    items = [
        ObservationItem(
            observation_id=r.observation_id,
            observation_date=r.observation_date,
            vintage_id=r.vintage_id,
            vintage_date=r.vintage_date,
            value=r.value,
            is_missing=r.is_missing,
            vintage_count=r.vintage_count,
        )
        for r in rows
    ]

    return ObservationListResponse(
        items=items,
        total=total_count,
        series_code=series.code,
        view=view,
        as_of=as_of if view == "as_of" else None,
    )


@router.get("/observations/{id}/vintages", response_model=ObservationVintageListResponse)
def get_observation_vintages(
    id: int,
    limit: int = Query(default=100, ge=1, le=1000, description="Page limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    db: Session = Depends(get_db),
) -> ObservationVintageListResponse:
    """Retrieve the full historical revision chain of vintages for an observation."""
    obs = db.get(Observation, id)
    if obs is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Observation with ID {id} not found.",
        )

    count_stmt = select(func.count(ObservationVintage.id)).where(
        ObservationVintage.observation_id == id
    )
    total_count = db.scalar(count_stmt) or 0

    vintages_stmt = (
        select(ObservationVintage)
        .where(ObservationVintage.observation_id == id)
        .order_by(ObservationVintage.vintage_date.asc())
        .offset(offset)
        .limit(limit)
    )
    vintages = db.scalars(vintages_stmt).all()

    return ObservationVintageListResponse(
        items=[ObservationVintageItem.model_validate(v) for v in vintages],
        total=total_count,
        observation_id=id,
    )
