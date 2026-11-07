"""Pydantic schemas for revision analytics endpoints."""

from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class SeriesRevisionSummaryResponse(BaseModel):
    """Aggregate revision metrics for an economic series."""

    model_config = ConfigDict(from_attributes=True)

    series_code: str
    series_title: str
    change_unit_label: str
    true_initial_release_only: bool
    total_observations: int
    observations_revised: int
    total_revisions: int
    upward_count: int
    downward_count: int
    became_available_count: int
    became_missing_count: int
    mean_absolute_revision: Optional[Decimal] = None
    median_absolute_revision: Optional[Decimal] = None
    mean_relative_revision_pct: Optional[Decimal] = None
    median_relative_revision_pct: Optional[Decimal] = None
    largest_absolute_revision: Optional[Decimal] = None
    largest_relative_revision_pct: Optional[Decimal] = None
    average_revisions_per_observation: float


class DirectionCountsResponse(BaseModel):
    """Counts of revisions categorized by direction and type."""

    model_config = ConfigDict(from_attributes=True)

    series_code: str
    upward: int
    downward: int
    became_available: int
    became_missing: int
    total_revisions: int


class HistogramBinItem(BaseModel):
    bin_start: float
    bin_end: float
    count: int


class MagnitudeDistributionResponse(BaseModel):
    """Distribution of absolute revisions across magnitude buckets."""

    model_config = ConfigDict(from_attributes=True)

    series_code: str
    unit_label: str
    bins: List[HistogramBinItem]
    total_numeric_revisions: int


class PeriodRevisionMetricItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period: str
    mean_relative_revision_pct: Optional[Decimal] = None
    mean_absolute_revision: Optional[Decimal] = None
    revision_count: int


class PeriodRevisionListResponse(BaseModel):
    series_code: str
    items: List[PeriodRevisionMetricItem]


class LagBucketItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bucket_label: str
    revision_count: int
    percentage_of_total: float


class LagBucketListResponse(BaseModel):
    series_code: str
    items: List[LagBucketItem]


class RevisionsFrequencyItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    revision_count: int
    observation_count: int
    percentage_of_total: float


class RevisionsFrequencyListResponse(BaseModel):
    series_code: str
    items: List[RevisionsFrequencyItem]


class FirstVsLatestComparisonResponse(BaseModel):
    """Comparison metrics between initial release and latest vintage."""

    model_config = ConfigDict(from_attributes=True)

    series_code: str
    unit_label: str
    true_initial_release_only: bool
    observations_compared: int
    observations_revised: int
    share_revised_upward_pct: float
    share_revised_downward_pct: float
    share_unchanged_pct: float
    mean_signed_absolute_revision: Optional[Decimal] = None
    mean_signed_relative_revision_pct: Optional[Decimal] = None
    median_signed_relative_revision_pct: Optional[Decimal] = None
