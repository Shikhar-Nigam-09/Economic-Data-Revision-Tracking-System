"""API router for revision analytics and summary statistics."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from econ_monitor.api.deps import get_db
from econ_monitor.revisions.analytics import (
    get_direction_counts,
    get_first_vs_latest_comparison,
    get_magnitude_distribution,
    get_revisions_by_lag_bucket,
    get_revisions_by_period,
    get_revisions_per_observation_frequency,
    get_series_revision_summary,
)
from econ_monitor.schemas.analytics import (
    DirectionCountsResponse,
    FirstVsLatestComparisonResponse,
    LagBucketListResponse,
    MagnitudeDistributionResponse,
    PeriodRevisionListResponse,
    RevisionsFrequencyListResponse,
    SeriesRevisionSummaryResponse,
)

router = APIRouter(prefix="/analytics/revisions", tags=["Revision Analytics"])


@router.get("/summary", response_model=SeriesRevisionSummaryResponse)
def get_summary(
    series: str = Query(..., description="Series code (e.g. GDP, CPI)"),
    true_initial_release_only: bool = Query(
        default=True, description="Filter to true initial releases only"
    ),
    db: Session = Depends(get_db),
) -> SeriesRevisionSummaryResponse:
    """Retrieve comprehensive revision summary statistics for an indicator."""
    res = get_series_revision_summary(
        db, series, true_initial_release_only=true_initial_release_only
    )
    return SeriesRevisionSummaryResponse(**res.__dict__)


@router.get("/by-period", response_model=PeriodRevisionListResponse)
def get_by_period(
    series: str = Query(..., description="Series code (e.g. GDP, CPI)"),
    true_initial_release_only: bool = Query(
        default=True, description="Filter to true initial releases only"
    ),
    db: Session = Depends(get_db),
) -> PeriodRevisionListResponse:
    """Retrieve revisions aggregated by observation period/year."""
    items = get_revisions_by_period(db, series, true_initial_release_only=true_initial_release_only)
    return PeriodRevisionListResponse(
        series_code=series.upper(),
        true_initial_release_only=true_initial_release_only,
        items=items,
    )


@router.get("/direction-counts", response_model=DirectionCountsResponse)
def get_direction_counts_endpoint(
    series: str = Query(..., description="Series code (e.g. GDP, CPI)"),
    true_initial_release_only: bool = Query(
        default=True, description="Filter to true initial releases only"
    ),
    db: Session = Depends(get_db),
) -> DirectionCountsResponse:
    """Retrieve revision counts grouped by direction and revision type."""
    res = get_direction_counts(db, series, true_initial_release_only=true_initial_release_only)
    return DirectionCountsResponse(**res.__dict__)


@router.get("/magnitude-distribution", response_model=MagnitudeDistributionResponse)
def get_magnitude_distribution_endpoint(
    series: str = Query(..., description="Series code (e.g. GDP, CPI)"),
    num_bins: int = Query(default=10, ge=2, le=50, description="Number of histogram bins"),
    true_initial_release_only: bool = Query(
        default=True, description="Filter to true initial releases only"
    ),
    db: Session = Depends(get_db),
) -> MagnitudeDistributionResponse:
    """Retrieve histogram distribution of absolute revision magnitudes."""
    res = get_magnitude_distribution(
        db, series, num_bins=num_bins, true_initial_release_only=true_initial_release_only
    )
    return MagnitudeDistributionResponse(**res.__dict__)


@router.get("/by-lag", response_model=LagBucketListResponse)
def get_by_lag(
    series: str = Query(..., description="Series code (e.g. GDP, CPI)"),
    true_initial_release_only: bool = Query(
        default=True, description="Filter to true initial releases only"
    ),
    db: Session = Depends(get_db),
) -> LagBucketListResponse:
    """Retrieve revision frequency grouped by lag buckets."""
    items = get_revisions_by_lag_bucket(
        db, series, true_initial_release_only=true_initial_release_only
    )
    return LagBucketListResponse(
        series_code=series.upper(),
        true_initial_release_only=true_initial_release_only,
        items=items,
    )


@router.get("/frequency", response_model=RevisionsFrequencyListResponse)
def get_frequency(
    series: str = Query(..., description="Series code (e.g. GDP, CPI)"),
    true_initial_release_only: bool = Query(
        default=True, description="Filter to true initial releases only"
    ),
    db: Session = Depends(get_db),
) -> RevisionsFrequencyListResponse:
    """Retrieve observation counts by number of revisions (0, 1, 2, ...)."""
    items = get_revisions_per_observation_frequency(
        db, series, true_initial_release_only=true_initial_release_only
    )
    return RevisionsFrequencyListResponse(
        series_code=series.upper(),
        true_initial_release_only=true_initial_release_only,
        items=items,
    )


@router.get("/first-vs-latest", response_model=FirstVsLatestComparisonResponse)
def get_first_vs_latest(
    series: str = Query(..., description="Series code (e.g. GDP, CPI)"),
    true_initial_release_only: bool = Query(
        default=True, description="Filter to true initial releases only"
    ),
    db: Session = Depends(get_db),
) -> FirstVsLatestComparisonResponse:
    """Retrieve initial release vs latest known vintage comparison and direction bias."""
    res = get_first_vs_latest_comparison(
        db, series, true_initial_release_only=true_initial_release_only
    )
    return FirstVsLatestComparisonResponse(**res.__dict__)
