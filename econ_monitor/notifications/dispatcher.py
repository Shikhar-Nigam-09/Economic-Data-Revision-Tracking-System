"""Notification dispatcher matching data events with subscriber preferences."""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from econ_monitor.core.clock import get_utc_now
from econ_monitor.core.config import get_settings
from econ_monitor.core.errors import ConfigurationError
from econ_monitor.core.logging import get_logger
from econ_monitor.core.yaml_config import load_monitoring_config
from econ_monitor.db.models import (
    DataEvent,
    EconomicSeries,
    NotificationHistory,
    NotificationStatus,
    Subscriber,
    SubscriberPreference,
)
from econ_monitor.notifications.subscribers import deactivate_subscriber
from econ_monitor.notifications.telegram_client import (
    TelegramClient,
    TelegramError,
    TelegramUserBlockedError,
    redact_telegram_token,
)
from econ_monitor.notifications.templates import format_event_message

logger = get_logger("econ_monitor.notifications.dispatcher")

DISPATCHER_ADVISORY_LOCK_ID = 987654321


@dataclass
class DispatchResult:
    """Outcome summary of a notification dispatch execution."""

    eligible_events_found: int
    matched_subscribers: int
    sent_count: int
    failed_count: int
    skipped_count: int
    retried_count: int
    dry_run: bool


def _is_event_matching_preference(
    event: DataEvent,
    pref: SubscriberPreference,
) -> bool:
    """Check if a data event matches a subscriber's indicator preference."""
    if not pref.is_enabled:
        return False

    ev_type = event.event_type

    if ev_type == "new_observation":
        return pref.notify_new_observation

    elif ev_type == "revision_batch":
        if not pref.notify_revision:
            return False

        # If min_revision_pct threshold is configured, filter small revisions
        if pref.min_revision_pct is not None:
            largest_rel = event.payload.get("largest_relative_revision")
            if largest_rel is None:
                # If no relative revision could be calculated, do not filter out
                return True
            try:
                if abs(Decimal(str(largest_rel))) < pref.min_revision_pct:
                    return False
            except Exception:
                return True
        return True

    elif ev_type == "anomaly":
        return pref.notify_anomaly

    elif ev_type == "quality_issue":
        return pref.notify_quality_issue

    return False


def dispatch_notifications(
    session: Session,
    telegram_client: Optional[TelegramClient] = None,
    dry_run: bool = False,
    now: Optional[datetime] = None,
) -> DispatchResult:
    """Select eligible events, match subscriber preferences, and dispatch alerts."""
    if now is None:
        now = get_utc_now()

    settings = get_settings()
    monitoring_cfg = load_monitoring_config()
    max_retries = monitoring_cfg.notifications.max_retry_attempts
    pause_sec = monitoring_cfg.notifications.rate_limit_pause_seconds
    max_age_days = settings.NOTIFY_MAX_EVENT_AGE_DAYS

    # Check Telegram client initialization if not dry run
    client = telegram_client
    if not dry_run and client is None:
        try:
            token = settings.require_telegram_bot_token()
            client = TelegramClient(bot_token=token)
        except ConfigurationError as exc:
            logger.error(f"Cannot dispatch notifications: {exc}")
            raise

    # 1. Acquire PostgreSQL advisory lock to prevent concurrent dispatch runs
    is_postgres = session.bind is not None and session.bind.dialect.name == "postgresql"
    got_lock = True
    if is_postgres:
        res = session.execute(
            text(f"SELECT pg_try_advisory_lock({DISPATCHER_ADVISORY_LOCK_ID})")
        ).scalar()
        if not res:
            logger.warning("Another notification dispatcher is currently executing. Skipping.")
            return DispatchResult(0, 0, 0, 0, 0, 0, dry_run=dry_run)

    try:
        # 2. Query eligible events: young enough, not backfill, from active series
        cutoff_date = (now - timedelta(days=max_age_days)).date()
        events_stmt = (
            select(DataEvent)
            .join(EconomicSeries, DataEvent.series_id == EconomicSeries.id)
            .where(
                DataEvent.is_backfill == False,  # noqa: E712
                DataEvent.occurred_on >= cutoff_date,
                EconomicSeries.is_active == True,  # noqa: E712
            )
            .order_by(DataEvent.created_at.asc())
        )
        eligible_events = list(session.scalars(events_stmt).all())

        eligible_events_found = len(eligible_events)
        matched_subscribers = 0
        sent_count = 0
        failed_count = 0
        skipped_count = 0
        retried_count = 0

        # Query all active subscribers with their preferences pre-loaded
        subscribers = list(
            session.scalars(
                select(Subscriber).where(Subscriber.is_active == True)  # noqa: E712
            ).all()
        )

        for event in eligible_events:
            for sub in subscribers:
                # Find preference for this series
                pref = next((p for p in sub.preferences if p.series_id == event.series_id), None)
                if not pref or not _is_event_matching_preference(event, pref):
                    continue

                matched_subscribers += 1

                # Check or initialize notification history ledger
                existing_notif = session.scalars(
                    select(NotificationHistory).where(
                        NotificationHistory.subscriber_id == sub.id,
                        NotificationHistory.event_id == event.id,
                        NotificationHistory.channel == "telegram",
                    )
                ).first()

                if existing_notif is not None:
                    if existing_notif.status in (
                        NotificationStatus.SENT,
                        NotificationStatus.SKIPPED,
                    ):
                        skipped_count += 1
                        continue
                    if existing_notif.status == NotificationStatus.FAILED:
                        if existing_notif.attempts >= max_retries:
                            skipped_count += 1
                            continue
                        # Eligible for retry
                        retried_count += 1
                        notif = existing_notif
                    else:  # pending
                        notif = existing_notif
                else:
                    msg_text = format_event_message(event.event_type, event.payload)
                    notif = NotificationHistory(
                        subscriber_id=sub.id,
                        event_id=event.id,
                        channel="telegram",
                        status=NotificationStatus.PENDING,
                        message_text=msg_text,
                        attempts=0,
                        created_at=now,
                    )
                    session.add(notif)
                    session.flush()

                if dry_run:
                    logger.info(
                        f"[DRY-RUN] Alert for chat_id={sub.telegram_chat_id}:\n{notif.message_text}"
                    )
                    sent_count += 1
                    continue

                # Live Telegram dispatch
                assert client is not None
                try:
                    msg_ids = client.send_message(
                        chat_id=sub.telegram_chat_id,
                        text=notif.message_text,
                        parse_mode="HTML",
                    )
                    notif.status = NotificationStatus.SENT
                    notif.sent_at = get_utc_now()
                    notif.telegram_message_id = msg_ids[0] if msg_ids else None
                    notif.attempts += 1
                    notif.last_error = None
                    session.flush()
                    sent_count += 1

                    # Polite rate limit pause between recipient pushes
                    if pause_sec > 0:
                        time.sleep(pause_sec)

                except TelegramUserBlockedError as exc:
                    notif.status = NotificationStatus.FAILED
                    notif.last_error = redact_telegram_token(str(exc))
                    notif.attempts += 1
                    session.flush()
                    failed_count += 1
                    # Auto-deactivate blocked subscriber
                    deactivate_subscriber(session, sub.telegram_chat_id, now=get_utc_now())

                except (TelegramError, Exception) as exc:
                    notif.status = NotificationStatus.FAILED
                    notif.last_error = redact_telegram_token(str(exc))
                    notif.attempts += 1
                    session.flush()
                    failed_count += 1
                    err_msg = redact_telegram_token(str(exc))
                    cid = sub.telegram_chat_id
                    logger.error(f"Failed delivering notification to chat_id={cid}: {err_msg}")

        return DispatchResult(
            eligible_events_found=eligible_events_found,
            matched_subscribers=matched_subscribers,
            sent_count=sent_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            retried_count=retried_count,
            dry_run=dry_run,
        )

    finally:
        if is_postgres and got_lock:
            session.execute(text(f"SELECT pg_advisory_unlock({DISPATCHER_ADVISORY_LOCK_ID})"))
