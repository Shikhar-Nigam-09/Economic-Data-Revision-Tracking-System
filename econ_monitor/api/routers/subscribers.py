"""API router for managing subscribers, preferences, and test messaging."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from econ_monitor.api.deps import get_db
from econ_monitor.core.clock import get_utc_now
from econ_monitor.core.config import get_settings
from econ_monitor.core.errors import SeriesNotFoundError
from econ_monitor.db.models import EconomicSeries, Subscriber, SubscriberPreference
from econ_monitor.notifications.subscribers import (
    deactivate_subscriber,
    get_or_create_subscriber,
    update_subscriber_preference,
)
from econ_monitor.notifications.telegram_client import TelegramClient
from econ_monitor.schemas.subscribers import (
    PreferenceListResponse,
    PreferenceUpdateRequest,
    SubscriberCreateRequest,
    SubscriberItem,
    SubscriberListResponse,
    SubscriberPatchRequest,
    SubscriberPreferenceItem,
    TestMessageResponse,
)

router = APIRouter(prefix="/subscribers", tags=["Subscribers"])


@router.get("", response_model=SubscriberListResponse)
def list_all_subscribers(
    is_active: Optional[bool] = Query(default=None, description="Filter by active status"),
    limit: int = Query(default=100, ge=1, le=1000, description="Page limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    db: Session = Depends(get_db),
) -> SubscriberListResponse:
    """List subscribers with optional active filter and pagination."""
    stmt = select(Subscriber)
    if is_active is not None:
        stmt = stmt.where(Subscriber.is_active == is_active)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count = db.scalar(count_stmt) or 0

    stmt = stmt.order_by(Subscriber.created_at.asc()).offset(offset).limit(limit)
    subscribers = db.scalars(stmt).all()

    return SubscriberListResponse(
        items=[SubscriberItem.model_validate(s) for s in subscribers],
        total=total_count,
    )


@router.post("", response_model=SubscriberItem, status_code=status.HTTP_201_CREATED)
def create_subscriber(
    req: SubscriberCreateRequest,
    db: Session = Depends(get_db),
) -> SubscriberItem:
    """Register a new Telegram subscriber or reactivate an existing one."""
    sub, created = get_or_create_subscriber(
        session=db,
        telegram_chat_id=req.telegram_chat_id,
        telegram_username=req.telegram_username,
        display_name=req.display_name,
    )
    db.commit()
    db.refresh(sub)
    return SubscriberItem.model_validate(sub)


@router.get("/{id}", response_model=SubscriberItem)
def get_subscriber_by_id(id: int, db: Session = Depends(get_db)) -> SubscriberItem:
    """Retrieve subscriber details by subscriber ID."""
    sub = db.get(Subscriber, id)
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscriber with ID {id} not found.",
        )
    return SubscriberItem.model_validate(sub)


@router.patch("/{id}", response_model=SubscriberItem)
def update_subscriber_by_id(
    id: int,
    patch: SubscriberPatchRequest,
    db: Session = Depends(get_db),
) -> SubscriberItem:
    """Update subscriber active status, username, or display name."""
    sub = db.get(Subscriber, id)
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscriber with ID {id} not found.",
        )

    now = get_utc_now()
    if patch.is_active is not None:
        sub.is_active = patch.is_active
        if not patch.is_active:
            sub.deactivated_at = now
        else:
            sub.deactivated_at = None
    if patch.telegram_username is not None:
        sub.telegram_username = patch.telegram_username
    if patch.display_name is not None:
        sub.display_name = patch.display_name

    sub.updated_at = now
    db.commit()
    db.refresh(sub)
    return SubscriberItem.model_validate(sub)


@router.delete("/{id}", response_model=SubscriberItem)
def deactivate_subscriber_by_id(id: int, db: Session = Depends(get_db)) -> SubscriberItem:
    """Deactivate a subscriber (soft delete setting is_active=False and deactivated_at)."""
    sub = db.get(Subscriber, id)
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscriber with ID {id} not found.",
        )

    deactivate_subscriber(db, telegram_chat_id=sub.telegram_chat_id)
    db.commit()
    db.refresh(sub)
    return SubscriberItem.model_validate(sub)


@router.get("/{id}/preferences", response_model=PreferenceListResponse)
def get_subscriber_preferences(
    id: int,
    db: Session = Depends(get_db),
) -> PreferenceListResponse:
    """Retrieve all indicator notification preferences for a subscriber."""
    sub = db.get(Subscriber, id)
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscriber with ID {id} not found.",
        )

    stmt = (
        select(SubscriberPreference, EconomicSeries.code.label("series_code"))
        .join(EconomicSeries, SubscriberPreference.series_id == EconomicSeries.id)
        .where(SubscriberPreference.subscriber_id == id)
        .order_by(EconomicSeries.code.asc())
    )
    rows = db.execute(stmt).all()

    items = [
        SubscriberPreferenceItem(
            id=p.id,
            subscriber_id=p.subscriber_id,
            series_id=p.series_id,
            series_code=s_code,
            is_enabled=p.is_enabled,
            notify_new_observation=p.notify_new_observation,
            notify_revision=p.notify_revision,
            notify_anomaly=p.notify_anomaly,
            notify_quality_issue=p.notify_quality_issue,
            min_revision_pct=p.min_revision_pct,
        )
        for p, s_code in rows
    ]

    return PreferenceListResponse(items=items, subscriber_id=id)


@router.put("/{id}/preferences", response_model=PreferenceListResponse)
def update_subscriber_preferences(
    id: int,
    req: PreferenceUpdateRequest,
    db: Session = Depends(get_db),
) -> PreferenceListResponse:
    """Update notification preferences for a specific indicator or 'all' indicators."""
    sub = db.get(Subscriber, id)
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscriber with ID {id} not found.",
        )

    try:
        update_subscriber_preference(
            session=db,
            subscriber_id=id,
            series_code=req.series_code,
            is_enabled=req.is_enabled,
            notify_new_observation=req.notify_new_observation,
            notify_revision=req.notify_revision,
            notify_anomaly=req.notify_anomaly,
            notify_quality_issue=req.notify_quality_issue,
            min_revision_pct=req.min_revision_pct,
        )
        db.commit()
    except SeriesNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Series {exc.series_code} not found.",
        )

    return get_subscriber_preferences(id=id, db=db)


@router.post("/{id}/test-message", response_model=TestMessageResponse)
def send_test_message(
    id: int,
    db: Session = Depends(get_db),
) -> TestMessageResponse:
    """Send a test notification message to a subscriber via Telegram."""
    sub = db.get(Subscriber, id)
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscriber with ID {id} not found.",
        )

    settings = get_settings()
    if not settings.TELEGRAM_BOT_TOKEN:
        return TestMessageResponse(
            success=False,
            message="TELEGRAM_BOT_TOKEN is not configured in environment.",
            subscriber_id=id,
        )

    try:
        client = TelegramClient(bot_token=settings.TELEGRAM_BOT_TOKEN)
        text = (
            f"🔔 <b>Economic Monitor Test Message</b>\n\n"
            f"Hello <b>{sub.display_name or sub.telegram_username or 'Subscriber'}</b>!\n"
            f"This is a test notification confirming your Telegram alert channel is active."
        )
        msg_ids = client.send_message(chat_id=sub.telegram_chat_id, text=text)
        msg_id_str = str(msg_ids[0]) if msg_ids else "unknown"
        return TestMessageResponse(
            success=True,
            message=f"Test message delivered successfully (message_id: {msg_id_str}).",
            subscriber_id=id,
            telegram_message_id=msg_ids[0] if msg_ids else None,
        )
    except Exception as exc:
        return TestMessageResponse(
            success=False,
            message=f"Failed to send test message: {exc}",
            subscriber_id=id,
        )
