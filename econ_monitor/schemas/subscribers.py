"""Pydantic schemas for subscriber management and preferences endpoints."""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class SubscriberPreferenceItem(BaseModel):
    """Notification filter settings for a subscriber on an indicator."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    subscriber_id: int
    series_id: int
    series_code: str
    is_enabled: bool
    notify_new_observation: bool
    notify_revision: bool
    notify_anomaly: bool
    notify_quality_issue: bool
    min_revision_pct: Optional[Decimal] = None


class SubscriberItem(BaseModel):
    """Registered Telegram subscriber recipient with active preferences."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    telegram_chat_id: int
    telegram_username: Optional[str] = None
    display_name: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    deactivated_at: Optional[datetime] = None
    preferences: List[SubscriberPreferenceItem] = Field(default_factory=list)


class SubscriberListResponse(BaseModel):
    """List of registered subscribers."""

    items: List[SubscriberItem]
    total: int


class SubscriberCreateRequest(BaseModel):
    """Registration request payload for a new subscriber."""

    telegram_chat_id: int
    telegram_username: Optional[str] = None
    display_name: Optional[str] = None


class SubscriberPatchRequest(BaseModel):
    """Update payload for modifying subscriber details."""

    is_active: Optional[bool] = None
    telegram_username: Optional[str] = None
    display_name: Optional[str] = None


class PreferenceUpdateRequest(BaseModel):
    """Preference modification for a single indicator or all indicators."""

    series_code: str  # specific code or "all"
    is_enabled: Optional[bool] = None
    notify_new_observation: Optional[bool] = None
    notify_revision: Optional[bool] = None
    notify_anomaly: Optional[bool] = None
    notify_quality_issue: Optional[bool] = None
    min_revision_pct: Optional[Decimal] = None


class PreferenceListResponse(BaseModel):
    """List of preferences for a subscriber."""

    subscriber_id: int
    items: List[SubscriberPreferenceItem]


class TestMessageResponse(BaseModel):
    """Outcome of attempting to send a test notification to a subscriber."""

    success: bool
    telegram_message_id: Optional[int] = None
    message: str
    error: Optional[str] = None
