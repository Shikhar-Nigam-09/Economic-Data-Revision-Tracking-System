"""Pydantic schemas for anomaly detection and status endpoints."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AnomalyItem(BaseModel):
    """Detailed record of a flagged anomaly observation."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    series_id: int
    series_code: str
    observation_id: int
    observation_date: date
    vintage_id: int
    vintage_date: date
    method: str
    score: Decimal
    threshold: Optional[Decimal] = None
    features: Dict[str, Any]
    status: str
    reviewed_at: Optional[datetime] = None
    note: Optional[str] = None
    detected_at: datetime
    is_superseded_vintage: bool = False


class AnomalyListResponse(BaseModel):
    """Paginated collection of flagged anomalies."""

    items: List[AnomalyItem]
    total: int


class AnomalyStatusResponse(BaseModel):
    """Status summary of the last anomaly detection execution."""

    last_run_at: Optional[datetime] = None
    summary: Dict[str, Any] = Field(default_factory=dict)


class AnomalyPatchRequest(BaseModel):
    """Review action request updating status and optional audit note."""

    status: Literal["flagged", "reviewed", "dismissed"]
    note: Optional[str] = Field(default=None, max_length=1000)


class AnomalyRunResponse(BaseModel):
    """Result of triggering an anomaly detection run."""

    run_at: datetime
    total_flagged: int
    total_new_records: int
    total_events_emitted: int
    series_reports: Dict[str, Any]
