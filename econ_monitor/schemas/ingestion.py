"""Pydantic schemas for data ingestion run audit and execution endpoints."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class IngestionRunSeriesItem(BaseModel):
    """Execution breakdown for an individual indicator during a run."""

    model_config = ConfigDict(from_attributes=True)

    series_id: int
    series_code: str
    status: str
    vintages_fetched: int
    observations_new: int
    vintages_new: int
    vintages_unchanged: int
    missing_values: int
    error_message: Optional[str] = None


class IngestionRunItem(BaseModel):
    """Audit header tracking an ingestion execution."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    run_uuid: str
    mode: str
    trigger: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: str
    series_requested: int
    series_succeeded: int
    rows_fetched: int
    rows_inserted: int
    error_summary: Optional[str] = None
    series: List[IngestionRunSeriesItem] = []


class IngestionRunListResponse(BaseModel):
    """Paginated collection of ingestion runs."""

    items: List[IngestionRunItem]
    total: int


class IngestionTriggerResponse(BaseModel):
    """Response returned when initiating an ingestion or pipeline background run."""

    run_uuid: str
    status: str
    message: str
