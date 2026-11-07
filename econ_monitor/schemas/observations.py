"""Pydantic schemas for observations and vintage history endpoints."""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict


class ObservationItem(BaseModel):
    """Observation value representing latest, first, or as-of vintage state."""

    model_config = ConfigDict(from_attributes=True)

    observation_id: int
    observation_date: date
    vintage_id: int
    vintage_date: date
    value: Optional[Decimal] = None
    is_missing: bool
    vintage_count: int


class ObservationListResponse(BaseModel):
    """Paginated collection of series observations with view metadata."""

    items: List[ObservationItem]
    total: int
    series_code: str
    view: Literal["latest", "first", "as_of"]
    as_of: Optional[date] = None


class ObservationVintageItem(BaseModel):
    """Historical immutable vintage entry for an observation."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    observation_id: int
    vintage_date: date
    value: Optional[Decimal] = None
    is_missing: bool
    source_realtime_end: Optional[date] = None
    retrieved_at: datetime
    source: str


class ObservationVintageListResponse(BaseModel):
    """Collection of vintages for a single observation."""

    items: List[ObservationVintageItem]
    total: int
    observation_id: int
