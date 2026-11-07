"""Pydantic schemas for economic series responses."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class SeriesResponse(BaseModel):
    """Detailed response schema for an individual economic series."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    fred_series_id: str
    title: str
    frequency: str
    units: str
    seasonal_adjustment: str
    change_unit_label: str | None = None
    anomaly_transform: str
    expected_min: Decimal
    expected_max: Decimal
    max_stale_lag_days: int
    initial_release_max_lag_days: int
    history_start: date
    is_active: bool
    fred_last_updated: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SeriesListResponse(BaseModel):
    """Response schema for listing economic series."""

    items: list[SeriesResponse]
    total: int
