"""Pydantic schemas for data quality logs and scores endpoints."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class QualityScoreItem(BaseModel):
    """Data quality health score with component breakdowns."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    run_uuid: str
    series_id: int
    series_code: str
    score: Decimal
    component_scores: Dict[str, Any]
    computed_at: datetime


class QualityScoresResponse(BaseModel):
    """Collection of data quality health scores."""

    items: List[QualityScoreItem]
    total: int


class QualityLogItem(BaseModel):
    """Detailed audit log entry for an individual check execution."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    run_uuid: str
    series_id: int
    series_code: str
    check_name: str
    severity: str
    passed: bool
    affected_count: int
    message: str
    details: Optional[Dict[str, Any]] = None
    checked_at: datetime


class QualityLogsResponse(BaseModel):
    """Paginated collection of data quality log entries."""

    items: List[QualityLogItem]
    total: int


class QualityRunResponse(BaseModel):
    """Execution outcome of running the quality check suite."""

    run_uuid: str
    total_checks_evaluated: int
    total_issues_emitted: int
    series_reports: Dict[str, Any]
