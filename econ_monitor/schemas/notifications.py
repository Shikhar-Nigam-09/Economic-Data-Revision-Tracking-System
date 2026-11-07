"""Pydantic schemas for notification history and data event endpoints."""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class NotificationHistoryItem(BaseModel):
    """Audit ledger entry for an individual alert dispatch attempt."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    subscriber_id: int
    event_id: int
    channel: str
    status: str
    message_text: str
    attempts: int
    last_error: Optional[str] = None
    telegram_message_id: Optional[int] = None
    created_at: datetime
    sent_at: Optional[datetime] = None


class NotificationListResponse(BaseModel):
    """Paginated collection of alert notification history records."""

    items: List[NotificationHistoryItem]
    total: int


class NotificationSummaryResponse(BaseModel):
    """High-level metrics on alert delivery history."""

    total: int
    pending: int
    sent: int
    failed: int
    skipped: int
    last_7_days: int
    last_30_days: int


class DataEventItem(BaseModel):
    """Event ledger item triggering notifications."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    event_key: str
    event_type: str
    series_id: int
    series_code: str
    observation_id: Optional[int] = None
    vintage_date: Optional[date] = None
    payload: Dict[str, Any]
    is_backfill: bool
    occurred_on: date
    created_at: datetime


class DataEventListResponse(BaseModel):
    """Paginated collection of published data events."""

    items: List[DataEventItem]
    total: int


class DispatchResponse(BaseModel):
    """Summary of dispatch execution."""

    eligible_events: int
    matched_subscribers: int
    sent_count: int
    failed_count: int
    skipped_count: int
    retried_count: int
    dry_run: bool
