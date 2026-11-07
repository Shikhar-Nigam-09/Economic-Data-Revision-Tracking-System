"""Pydantic schemas for data revision history endpoints."""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class RevisionItem(BaseModel):
    """Detailed record of an individual historical observation revision."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    observation_id: int
    series_id: int
    series_code: str
    observation_date: date
    revision_date: date
    from_vintage_id: int
    to_vintage_id: int
    revision_number: int
    previous_value: Optional[Decimal] = None
    new_value: Optional[Decimal] = None
    absolute_revision: Optional[Decimal] = None
    relative_revision_pct: Optional[Decimal] = None
    direction: Optional[str] = None
    revision_type: str
    revision_lag_days: int
    detected_at: datetime


class RevisionListResponse(BaseModel):
    """Paginated collection of data revisions."""

    items: List[RevisionItem]
    total: int
