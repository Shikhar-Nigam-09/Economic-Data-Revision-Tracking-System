"""API router for Economic Series endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from econ_monitor.api.deps import get_db
from econ_monitor.core.errors import SeriesNotFoundError
from econ_monitor.db.repositories import get_all_series, get_series_by_code
from econ_monitor.schemas.series import SeriesListResponse, SeriesResponse

router = APIRouter(prefix="/series", tags=["Economic Series"])


@router.get("", response_model=SeriesListResponse)
def list_series(
    active_only: bool = Query(default=False, description="Filter for active series only"),
    limit: int = Query(default=100, ge=1, le=1000, description="Maximum number of items to return"),
    offset: int = Query(default=0, ge=0, description="Offset index for pagination"),
    db: Session = Depends(get_db),
) -> SeriesListResponse:
    """Retrieve tracked economic indicators."""
    all_items = get_all_series(db, active_only=active_only)
    paginated = all_items[offset : offset + limit]
    return SeriesListResponse(
        items=[SeriesResponse.model_validate(item) for item in paginated],
        total=len(all_items),
    )


@router.get("/{code}", response_model=SeriesResponse)
def get_series(
    code: str,
    db: Session = Depends(get_db),
) -> SeriesResponse:
    """Retrieve details of a specific economic indicator by code."""
    series = get_series_by_code(db, code)
    if series is None:
        raise SeriesNotFoundError(code)
    return SeriesResponse.model_validate(series)


@router.post("/sync-config", response_model=SeriesListResponse)
def sync_series_config(
    db: Session = Depends(get_db),
) -> SeriesListResponse:
    """Sync and seed economic indicator definitions from indicators.yaml."""
    from econ_monitor.core.yaml_config import load_indicators_config
    from econ_monitor.db.repositories import seed_economic_series

    config = load_indicators_config()
    seed_economic_series(db, config)
    db.commit()

    all_items = get_all_series(db, active_only=False)
    return SeriesListResponse(
        items=[SeriesResponse.model_validate(item) for item in all_items],
        total=len(all_items),
    )
