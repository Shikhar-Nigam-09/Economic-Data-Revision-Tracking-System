"""Subscriber management for Telegram recipients and notification preferences."""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from econ_monitor.core.clock import get_utc_now
from econ_monitor.core.errors import SeriesNotFoundError
from econ_monitor.core.logging import get_logger
from econ_monitor.core.yaml_config import load_monitoring_config
from econ_monitor.db.models import EconomicSeries, Subscriber, SubscriberPreference

logger = get_logger("econ_monitor.notifications.subscribers")


def get_or_create_subscriber(
    session: Session,
    telegram_chat_id: int,
    telegram_username: Optional[str] = None,
    display_name: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[Subscriber, bool]:
    """Retrieve existing subscriber or register a new one with default preferences.

    Returns: (subscriber, created)
    """
    if now is None:
        now = get_utc_now()

    sub = session.scalars(
        select(Subscriber).where(Subscriber.telegram_chat_id == telegram_chat_id)
    ).first()

    if sub is not None:
        was_inactive = not sub.is_active
        sub.is_active = True
        sub.deactivated_at = None
        if telegram_username:
            sub.telegram_username = telegram_username
        if display_name:
            sub.display_name = display_name
        sub.updated_at = now
        session.flush()
        if was_inactive:
            logger.info(f"Re-activated subscriber chat_id={telegram_chat_id}")
        return sub, False

    # Create new subscriber
    sub = Subscriber(
        telegram_chat_id=telegram_chat_id,
        telegram_username=telegram_username,
        display_name=display_name,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(sub)
    session.flush()

    # Seed default preferences across all active economic indicators
    monitoring_cfg = load_monitoring_config()
    defaults = monitoring_cfg.notifications.defaults

    active_series = session.scalars(
        select(EconomicSeries).where(EconomicSeries.is_active == True)  # noqa: E712
    ).all()

    for series in active_series:
        pref = SubscriberPreference(
            subscriber_id=sub.id,
            series_id=series.id,
            is_enabled=True,
            notify_new_observation=defaults.notify_new_observation,
            notify_revision=defaults.notify_revision,
            notify_anomaly=defaults.notify_anomaly,
            notify_quality_issue=defaults.notify_quality_issue,
            min_revision_pct=None,
        )
        session.add(pref)

    session.flush()
    logger.info(
        f"Registered new subscriber chat_id={telegram_chat_id} "
        f"with {len(active_series)} indicator preferences."
    )
    return sub, True


def deactivate_subscriber(
    session: Session,
    telegram_chat_id: int,
    now: Optional[datetime] = None,
) -> bool:
    """Deactivate a subscriber when stopped or when bot is blocked (403)."""
    if now is None:
        now = get_utc_now()

    sub = session.scalars(
        select(Subscriber).where(Subscriber.telegram_chat_id == telegram_chat_id)
    ).first()

    if sub is None or not sub.is_active:
        return False

    sub.is_active = False
    sub.deactivated_at = now
    sub.updated_at = now
    session.flush()
    logger.info(f"Deactivated subscriber chat_id={telegram_chat_id}")
    return True


def get_subscriber(session: Session, telegram_chat_id: int) -> Optional[Subscriber]:
    """Retrieve subscriber by Telegram chat ID."""
    return session.scalars(
        select(Subscriber).where(Subscriber.telegram_chat_id == telegram_chat_id)
    ).first()


def list_subscribers(session: Session, active_only: bool = True) -> List[Subscriber]:
    """List subscribers ordered by registration date."""
    stmt = select(Subscriber)
    if active_only:
        stmt = stmt.where(Subscriber.is_active == True)  # noqa: E712
    stmt = stmt.order_by(Subscriber.created_at.asc())
    return list(session.scalars(stmt).all())


def update_subscriber_preference(
    session: Session,
    subscriber_id: int,
    series_code: str,
    is_enabled: Optional[bool] = None,
    notify_new_observation: Optional[bool] = None,
    notify_revision: Optional[bool] = None,
    notify_anomaly: Optional[bool] = None,
    notify_quality_issue: Optional[bool] = None,
    min_revision_pct: Optional[Decimal] = None,
) -> List[SubscriberPreference]:
    """Update notification preferences for a specific indicator or 'all' indicators."""
    monitoring_cfg = load_monitoring_config()
    defaults = monitoring_cfg.notifications.defaults

    if series_code.lower() == "all":
        series_list = list(
            session.scalars(
                select(EconomicSeries).where(EconomicSeries.is_active == True)  # noqa: E712
            ).all()
        )
    else:
        series_obj = session.scalars(
            select(EconomicSeries).where(EconomicSeries.code == series_code.upper())
        ).first()
        if series_obj is None:
            raise SeriesNotFoundError(series_code)
        series_list = [series_obj]

    updated: List[SubscriberPreference] = []
    for s in series_list:
        pref = session.scalars(
            select(SubscriberPreference).where(
                SubscriberPreference.subscriber_id == subscriber_id,
                SubscriberPreference.series_id == s.id,
            )
        ).first()

        if pref is None:
            pref = SubscriberPreference(
                subscriber_id=subscriber_id,
                series_id=s.id,
                is_enabled=True,
                notify_new_observation=defaults.notify_new_observation,
                notify_revision=defaults.notify_revision,
                notify_anomaly=defaults.notify_anomaly,
                notify_quality_issue=defaults.notify_quality_issue,
            )
            session.add(pref)

        if is_enabled is not None:
            pref.is_enabled = is_enabled
        if notify_new_observation is not None:
            pref.notify_new_observation = notify_new_observation
        if notify_revision is not None:
            pref.notify_revision = notify_revision
        if notify_anomaly is not None:
            pref.notify_anomaly = notify_anomaly
        if notify_quality_issue is not None:
            pref.notify_quality_issue = notify_quality_issue
        if min_revision_pct is not None:
            pref.min_revision_pct = min_revision_pct

        session.flush()
        updated.append(pref)

    return updated
