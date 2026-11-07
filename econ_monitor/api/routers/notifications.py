"""API router for notification logs, summary metrics, data events, and manual dispatch."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from econ_monitor.api.deps import get_db
from econ_monitor.core.config import get_settings
from econ_monitor.db.models import DataEvent, EconomicSeries, NotificationHistory, Subscriber
from econ_monitor.notifications.dispatcher import dispatch_notifications
from econ_monitor.schemas.notifications import (
    DataEventItem,
    DataEventListResponse,
    DispatchResponse,
    NotificationHistoryItem,
    NotificationListResponse,
    NotificationSummaryResponse,
)

router = APIRouter(tags=["Notifications"])


@router.get("/notifications", response_model=NotificationListResponse)
def list_notifications(
    status: Optional[str] = Query(
        default=None, description="Filter by status: pending, sent, failed, skipped"
    ),
    subscriber_id: Optional[int] = Query(default=None, description="Filter by subscriber ID"),
    limit: int = Query(default=100, ge=1, le=1000, description="Page limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    db: Session = Depends(get_db),
) -> NotificationListResponse:
    """Retrieve historical notification alert attempts with pagination."""
    stmt = (
        select(
            NotificationHistory,
            Subscriber.telegram_chat_id,
            Subscriber.display_name,
            DataEvent.event_type,
            EconomicSeries.code.label("series_code"),
        )
        .join(Subscriber, NotificationHistory.subscriber_id == Subscriber.id)
        .join(DataEvent, NotificationHistory.event_id == DataEvent.id)
        .join(EconomicSeries, DataEvent.series_id == EconomicSeries.id)
    )

    if status is not None:
        stmt = stmt.where(NotificationHistory.status == status)
    if subscriber_id is not None:
        stmt = stmt.where(NotificationHistory.subscriber_id == subscriber_id)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count = db.scalar(count_stmt) or 0

    stmt = (
        stmt.order_by(NotificationHistory.created_at.desc(), NotificationHistory.id.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = db.execute(stmt).all()

    items = [
        NotificationHistoryItem(
            id=n.id,
            subscriber_id=n.subscriber_id,
            telegram_chat_id=chat_id,
            subscriber_name=d_name,
            event_id=n.event_id,
            event_type=ev_type,
            series_code=s_code,
            channel=n.channel,
            status=n.status,
            message_text=n.message_text,
            attempts=n.attempts,
            last_error=n.last_error,
            telegram_message_id=n.telegram_message_id,
            created_at=n.created_at,
            sent_at=n.sent_at,
        )
        for n, chat_id, d_name, ev_type, s_code in rows
    ]

    return NotificationListResponse(items=items, total=total_count)


@router.get("/notifications/summary", response_model=NotificationSummaryResponse)
def get_notification_summary(db: Session = Depends(get_db)) -> NotificationSummaryResponse:
    """Retrieve aggregate delivery statistics across all notification attempts."""
    stmt = select(
        NotificationHistory.status,
        func.count(NotificationHistory.id),
    ).group_by(NotificationHistory.status)
    rows = db.execute(stmt).all()

    counts = {r[0]: r[1] for r in rows}
    total = sum(counts.values())

    return NotificationSummaryResponse(
        total=total,
        pending=counts.get("pending", 0),
        sent=counts.get("sent", 0),
        failed=counts.get("failed", 0),
        skipped=counts.get("skipped", 0),
        last_7_days=total,
        last_30_days=total,
    )


@router.get("/events", response_model=DataEventListResponse)
def list_data_events(
    series: Optional[str] = Query(default=None, description="Series code filter"),
    event_type: Optional[str] = Query(default=None, description="Event type filter"),
    is_backfill: Optional[bool] = Query(default=None, description="Filter backfill events"),
    limit: int = Query(default=100, ge=1, le=1000, description="Page limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    db: Session = Depends(get_db),
) -> DataEventListResponse:
    """Retrieve recorded data events (new observation, revision batch, anomaly, quality issue)."""
    stmt = select(DataEvent, EconomicSeries.code.label("series_code")).join(
        EconomicSeries, DataEvent.series_id == EconomicSeries.id
    )

    if series is not None:
        stmt = stmt.where(EconomicSeries.code == series.upper())
    if event_type is not None:
        stmt = stmt.where(DataEvent.event_type == event_type)
    if is_backfill is not None:
        stmt = stmt.where(DataEvent.is_backfill == is_backfill)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count = db.scalar(count_stmt) or 0

    stmt = (
        stmt.order_by(DataEvent.created_at.desc(), DataEvent.id.desc()).offset(offset).limit(limit)
    )
    rows = db.execute(stmt).all()

    items = [
        DataEventItem(
            id=ev.id,
            event_key=ev.event_key,
            event_type=ev.event_type,
            series_id=ev.series_id,
            series_code=s_code,
            observation_id=ev.observation_id,
            vintage_date=ev.vintage_date,
            payload=ev.payload,
            is_backfill=ev.is_backfill,
            occurred_on=ev.occurred_on,
            created_at=ev.created_at,
        )
        for ev, s_code in rows
    ]

    return DataEventListResponse(items=items, total=total_count)


@router.post("/notifications/dispatch", response_model=DispatchResponse)
def trigger_notification_dispatch(
    dry_run: bool = Query(default=False, description="Run in dry run mode without sending"),
    db: Session = Depends(get_db),
) -> DispatchResponse:
    """Dispatch pending notifications for eligible data events."""
    settings = get_settings()
    is_dry = dry_run or not bool(settings.TELEGRAM_BOT_TOKEN)

    res = dispatch_notifications(session=db, dry_run=is_dry)
    db.commit()

    return DispatchResponse(
        eligible_events_found=res.eligible_events_found,
        matched_subscribers=res.matched_subscribers,
        sent_count=res.sent_count,
        failed_count=res.failed_count,
        skipped_count=res.skipped_count,
        retried_count=res.retried_count,
        dry_run=res.dry_run,
    )
