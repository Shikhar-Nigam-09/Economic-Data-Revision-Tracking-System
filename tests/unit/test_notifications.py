"""Unit tests for subscriber management, Telegram client, templates, and dispatcher."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import responses
from sqlalchemy import select
from sqlalchemy.orm import Session

from econ_monitor.core.yaml_config import load_indicators_config
from econ_monitor.db.models import (
    DataEvent,
    DataEventType,
    EconomicSeries,
    NotificationHistory,
    NotificationStatus,
)
from econ_monitor.db.repositories import seed_economic_series
from econ_monitor.notifications.bot_commands import handle_update
from econ_monitor.notifications.dispatcher import dispatch_notifications
from econ_monitor.notifications.subscribers import (
    deactivate_subscriber,
    get_or_create_subscriber,
    get_subscriber,
    update_subscriber_preference,
)
from econ_monitor.notifications.telegram_client import (
    TelegramClient,
    TelegramUserBlockedError,
    escape_html,
    redact_telegram_token,
    split_message_chunks,
)
from econ_monitor.notifications.templates import (
    format_anomaly_message,
    format_new_observation_message,
    format_revision_batch_message,
)


def test_telegram_token_redaction_and_formatting():
    """Verify that bot tokens are redacted from logs and messages, and HTML is escaped."""
    raw_url = "https://api.telegram.org/bot123456789:ABCdef-GHI_jkl123/sendMessage"
    redacted = redact_telegram_token(raw_url)
    assert "123456789:ABCdef" not in redacted
    assert "bot<REDACTED_TELEGRAM_TOKEN>" in redacted

    raw_html = "<script>alert('xss & bad')</script>"
    safe_html = escape_html(raw_html)
    assert "<script>" not in safe_html
    assert "&lt;script&gt;" in safe_html
    assert "&amp;" in safe_html

    # Test chunking
    short_text = "Hello world"
    assert split_message_chunks(short_text, max_length=50) == ["Hello world"]

    long_text = "\n".join([f"Line {i}" for i in range(100)])
    chunks = split_message_chunks(long_text, max_length=100)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 100


@responses.activate
def test_telegram_client_http_success_and_errors():
    """Verify TelegramClient HTTP calls, 429 rate limit backoff, and 403 user blocked error."""
    token = "test_bot_token_123"
    client = TelegramClient(bot_token=token)
    send_url = f"https://api.telegram.org/bot{token}/sendMessage"

    # 1. Successful message send
    responses.add(
        responses.POST,
        send_url,
        json={"ok": True, "result": {"message_id": 999}},
        status=200,
    )
    msg_ids = client.send_message(chat_id=12345, text="<b>Test</b>")
    assert msg_ids == [999]

    # 2. 429 Rate Limit retry
    responses.reset()
    responses.add(
        responses.POST,
        send_url,
        json={"ok": False, "parameters": {"retry_after": 0.01}},
        status=429,
    )
    responses.add(
        responses.POST,
        send_url,
        json={"ok": True, "result": {"message_id": 1000}},
        status=200,
    )
    msg_ids_retry = client.send_message(chat_id=12345, text="Retry test")
    assert msg_ids_retry == [1000]

    # 3. 403 User Blocked error
    responses.reset()
    responses.add(
        responses.POST,
        send_url,
        json={"ok": False, "description": "Forbidden: bot was blocked by the user"},
        status=403,
    )
    with pytest.raises(TelegramUserBlockedError):
        client.send_message(chat_id=12345, text="Block test")


def test_subscriber_registration_and_preferences(in_memory_db_session: Session):
    """Verify subscriber registration with defaults, reactivation, and preference updating."""
    config = load_indicators_config("config/indicators.yaml")
    seed_economic_series(in_memory_db_session, config)
    in_memory_db_session.commit()

    chat_id = 987654

    # 1. Register new subscriber
    sub, created = get_or_create_subscriber(
        session=in_memory_db_session,
        telegram_chat_id=chat_id,
        telegram_username="econ_trader",
        display_name="Trader Joe",
    )
    assert created
    assert sub.is_active
    assert sub.telegram_username == "econ_trader"

    # Check that default preferences were seeded for all active indicators
    prefs = sub.preferences
    assert len(prefs) >= 5
    for p in prefs:
        assert p.is_enabled
        assert p.notify_revision  # Default true
        assert p.notify_anomaly  # Default true
        assert not p.notify_new_observation  # Default false

    # 2. Update preference for single indicator (GDP)
    update_subscriber_preference(
        session=in_memory_db_session,
        subscriber_id=sub.id,
        series_code="GDP",
        notify_new_observation=True,
        min_revision_pct=Decimal("0.5"),
    )
    gdp_pref = next(p for p in sub.preferences if p.series.code == "GDP")
    assert gdp_pref.notify_new_observation
    assert gdp_pref.min_revision_pct == Decimal("0.5")

    # 3. Deactivate subscriber
    deactivated = deactivate_subscriber(in_memory_db_session, chat_id)
    assert deactivated
    assert not sub.is_active
    assert sub.deactivated_at is not None

    # 4. Reactivate subscriber
    sub2, created2 = get_or_create_subscriber(
        session=in_memory_db_session,
        telegram_chat_id=chat_id,
    )
    assert not created2
    assert sub2.is_active
    assert sub2.deactivated_at is None


def test_bot_commands_interactive(in_memory_db_session: Session):
    """Test pure bot command dispatching for /start, /help, /subscribe, /preferences, /stop."""
    config = load_indicators_config("config/indicators.yaml")
    seed_economic_series(in_memory_db_session, config)
    in_memory_db_session.commit()

    chat_id = 555001

    # /start
    upd_start = {
        "update_id": 1,
        "message": {
            "chat": {"id": chat_id},
            "from": {"username": "alice", "first_name": "Alice"},
            "text": "/start",
        },
    }
    c_id, reply_start = handle_update(in_memory_db_session, upd_start)
    assert c_id == chat_id
    assert "Welcome!" in reply_start

    # /help
    upd_help = {
        "update_id": 2,
        "message": {
            "chat": {"id": chat_id},
            "from": {"username": "alice"},
            "text": "/help",
        },
    }
    _, reply_help = handle_update(in_memory_db_session, upd_help)
    assert "/subscribe" in reply_help

    # /subscribe GDP
    upd_sub = {
        "update_id": 3,
        "message": {
            "chat": {"id": chat_id},
            "from": {"username": "alice"},
            "text": "/subscribe GDP",
        },
    }
    _, reply_sub = handle_update(in_memory_db_session, upd_sub)
    assert "Subscribed to alerts for GDP" in reply_sub

    # /stop
    upd_stop = {
        "update_id": 4,
        "message": {
            "chat": {"id": chat_id},
            "from": {"username": "alice"},
            "text": "/stop",
        },
    }
    _, reply_stop = handle_update(in_memory_db_session, upd_stop)
    assert "Alerts paused" in reply_stop
    sub = get_subscriber(in_memory_db_session, chat_id)
    assert not sub.is_active


def test_message_templates_rendering():
    """Verify that message templates produce unit-aware, cleanly formatted HTML."""
    # 1. New observation
    obs_payload = {
        "series_code": "GDP",
        "title": "Real Gross Domestic Product",
        "observation_date": "2024-01-01",
        "initial_vintage_date": "2024-02-28",
        "initial_value": "22000.5",
        "units": "Billions of Chained Dollars",
    }
    msg_obs = format_new_observation_message(obs_payload)
    assert "New Economic Observation" in msg_obs
    assert "22000.5 Billions of Chained Dollars" in msg_obs

    # 2. Revision batch
    rev_payload = {
        "series_code": "UNRATE",
        "vintage_date": "2024-03-01",
        "total_revisions": 1,
        "up_revisions_count": 1,
        "down_revisions_count": 0,
        "change_unit_label": "percentage points",
        "top_5_revisions": [
            {
                "observation_date": "2024-01-01",
                "previous_value": "3.7",
                "new_value": "3.9",
                "direction": "up",
                "absolute_revision": "0.2",
                "relative_revision_pct": "5.41",
            }
        ],
    }
    msg_rev = format_revision_batch_message(rev_payload)
    assert "Economic Data Revision" in msg_rev
    assert "revised upward by 0.2 percentage points, 5.41%" in msg_rev

    # 3. Anomaly
    anom_payload = {
        "series_code": "CPI",
        "title": "Consumer Price Index",
        "observation_date": "2024-02-01",
        "vintage_date": "2024-03-01",
        "method": "zscore",
        "score": "3.85",
        "threshold": "3.0",
        "current_value": "310.2",
        "transformed_change": "1.5",
        "change_unit_label": "index points",
    }
    msg_anom = format_anomaly_message(anom_payload)
    assert "Economic Anomaly Flagged for Review" in msg_anom
    assert "genuine shocks or outliers, not confirmed bad data" in msg_anom
    assert "Score:</b> 3.85" in msg_anom


@responses.activate
def test_notification_dispatcher_filtering_and_delivery(in_memory_db_session: Session):
    """Verify dispatcher eligibility filtering, preference matching, history, and delivery."""
    config = load_indicators_config("config/indicators.yaml")
    seed_economic_series(in_memory_db_session, config)
    in_memory_db_session.commit()

    gdp = in_memory_db_session.scalars(
        select(EconomicSeries).where(EconomicSeries.code == "GDP")
    ).first()

    now = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
    today = now.date()

    # Create subscriber with min_revision_pct = 2.0%
    sub, _ = get_or_create_subscriber(in_memory_db_session, telegram_chat_id=777001, now=now)
    update_subscriber_preference(
        in_memory_db_session,
        subscriber_id=sub.id,
        series_code="GDP",
        notify_revision=True,
        min_revision_pct=Decimal("2.0"),
    )

    # Event 1: Small revision (1.0% < 2.0% threshold) -> should NOT match
    ev_small = DataEvent(
        event_key="test:rev:small",
        event_type=DataEventType.REVISION_BATCH,
        series_id=gdp.id,
        occurred_on=today,
        is_backfill=False,
        payload={"series_code": "GDP", "largest_relative_revision": 1.0, "total_revisions": 1},
        created_at=now,
    )
    in_memory_db_session.add(ev_small)

    # Event 2: Large revision (3.5% >= 2.0% threshold) -> SHOULD match
    ev_large = DataEvent(
        event_key="test:rev:large",
        event_type=DataEventType.REVISION_BATCH,
        series_id=gdp.id,
        occurred_on=today,
        is_backfill=False,
        payload={"series_code": "GDP", "largest_relative_revision": 3.5, "total_revisions": 1},
        created_at=now,
    )
    in_memory_db_session.add(ev_large)

    # Event 3: Backfill event -> should NOT match
    ev_backfill = DataEvent(
        event_key="test:rev:backfill",
        event_type=DataEventType.REVISION_BATCH,
        series_id=gdp.id,
        occurred_on=today,
        is_backfill=True,
        payload={"series_code": "GDP", "largest_relative_revision": 5.0},
        created_at=now,
    )
    in_memory_db_session.add(ev_backfill)

    # Event 4: Too old event (> 30 days) -> should NOT match
    ev_old = DataEvent(
        event_key="test:rev:old",
        event_type=DataEventType.REVISION_BATCH,
        series_id=gdp.id,
        occurred_on=today - timedelta(days=45),
        is_backfill=False,
        payload={"series_code": "GDP", "largest_relative_revision": 5.0},
        created_at=now - timedelta(days=45),
    )
    in_memory_db_session.add(ev_old)

    in_memory_db_session.commit()

    # Mock Telegram API
    token = "fake_test_token"
    client = TelegramClient(bot_token=token)
    responses.add(
        responses.POST,
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"ok": True, "result": {"message_id": 8881}},
        status=200,
    )

    # Run dispatcher
    res = dispatch_notifications(
        session=in_memory_db_session,
        telegram_client=client,
        dry_run=False,
        now=now,
    )

    # Eligible young, non-backfill events = 2 (ev_small, ev_large)
    assert res.eligible_events_found == 2
    # Only ev_large satisfies subscriber threshold >= 2.0%
    assert res.matched_subscribers == 1
    assert res.sent_count == 1
    assert res.failed_count == 0

    # Verify notification_history record
    notifs = list(
        in_memory_db_session.scalars(
            select(NotificationHistory).where(NotificationHistory.subscriber_id == sub.id)
        ).all()
    )
    assert len(notifs) == 1
    assert notifs[0].status == NotificationStatus.SENT
    assert notifs[0].telegram_message_id == 8881

    # Run dispatcher again (IDEMPOTENCE): should skip already-sent notification
    res_second = dispatch_notifications(
        session=in_memory_db_session,
        telegram_client=client,
        dry_run=False,
        now=now,
    )
    assert res_second.sent_count == 0
    assert res_second.skipped_count == 1
