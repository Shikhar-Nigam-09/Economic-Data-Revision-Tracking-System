"""End-to-End Isolated Simulated Revision Test.

Verifies the complete pipeline lifecycle against an isolated in-memory test database:
1. Seeds indicator metadata.
2. Ingests baseline vintage data for an indicator.
3. Simulates the arrival of a revised vintage for an observation.
4. Detects revisions and verifies:
   - Exactly one revision_history row is created.
   - Previous value, new value, absolute delta, and relative % are exact.
   - The earlier vintage row remains completely unchanged (zero modification/deletion).
5. Creates a revision_batch data_event.
6. Runs notification dispatcher with a mocked Telegram client and verifies alert delivery.
7. Re-runs revision detection and dispatch and proves 100% idempotency:
   - 0 new revision rows.
   - 0 new data events.
   - 0 duplicate notification attempts.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

from econ_monitor.core.clock import get_utc_now
from econ_monitor.db.models import (
    AppState,
    DataEvent,
    EconomicSeries,
    NotificationHistory,
    Observation,
    ObservationVintage,
    RevisionHistory,
    Subscriber,
    SubscriberPreference,
)
from econ_monitor.notifications.dispatcher import dispatch_notifications
from econ_monitor.revisions.detector import detect_revisions


def test_simulated_revision_end_to_end_idempotent(in_memory_db_session):
    """Execute complete simulated revision detection, alerting, and idempotency check."""
    session = in_memory_db_session
    now = get_utc_now()

    # ---------------------------------------------------------
    # 1. Setup: Seed Economic Indicator & Subscriber
    # ---------------------------------------------------------
    series = EconomicSeries(
        code="CPI",
        fred_series_id="CPIAUCSL",
        title="Consumer Price Index for All Urban Consumers",
        frequency="M",
        units="Index 1982-1984=100",
        seasonal_adjustment="Seasonally Adjusted",
        change_unit_label="Index Points",
        anomaly_transform="pct_change",
        expected_min=Decimal("50.0"),
        expected_max=Decimal("500.0"),
        max_stale_lag_days=45,
        initial_release_max_lag_days=30,
        history_start=date(1990, 1, 1),
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(series)
    session.flush()

    # Mark baseline set so that subsequent detections emit alerts rather than backfill
    baseline_state = AppState(
        key="event_baseline:CPI",
        value={"baseline_set": True, "set_at": now.isoformat()},
        updated_at=now,
    )
    session.add(baseline_state)

    subscriber = Subscriber(
        telegram_chat_id=987654321,
        telegram_username="e2e_analyst",
        display_name="E2E Macro Analyst",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(subscriber)
    session.flush()

    pref = SubscriberPreference(
        subscriber_id=subscriber.id,
        series_id=series.id,
        is_enabled=True,
        notify_new_observation=True,
        notify_revision=True,
        notify_anomaly=True,
        notify_quality_issue=True,
        min_revision_pct=Decimal("0.05"),  # Minimum 0.05% revision to notify
    )
    session.add(pref)
    session.flush()

    # ---------------------------------------------------------
    # 2. Ingest Initial Observation and Vintage 1 (Baseline)
    # ---------------------------------------------------------
    today = now.date()
    obs_date = today - timedelta(days=60)
    obs = Observation(
        series_id=series.id,
        observation_date=obs_date,
        first_seen_at=now,
    )
    session.add(obs)
    session.flush()

    v1_val = Decimal("310.00000000")
    v1_date = today - timedelta(days=20)
    vintage_1 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=v1_date,
        value=v1_val,
        is_missing=False,
        retrieved_at=now,
        source="ALFRED",
    )
    session.add(vintage_1)
    session.flush()

    # Baseline detection run: first vintage is never a revision
    first_res = detect_revisions(session=session, series_codes=["CPI"], now=now)
    assert first_res.total_revisions_created == 0
    assert first_res.total_new_observations == 1

    # ---------------------------------------------------------
    # 3. Simulate Revised Release: Vintage 2
    # ---------------------------------------------------------
    # Value is revised upward from 310.0 to 311.55 (a +0.5% revision, well above 0.05% threshold)
    v2_val = Decimal("311.55000000")
    v2_date = today - timedelta(days=5)
    vintage_2 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=v2_date,
        value=v2_val,
        is_missing=False,
        retrieved_at=now,
        source="ALFRED",
    )
    session.add(vintage_2)
    session.flush()

    # ---------------------------------------------------------
    # 4. Detect Revisions & Verify Database Records
    # ---------------------------------------------------------
    revision_res = detect_revisions(session=session, series_codes=["CPI"], now=now)
    assert revision_res.total_revisions_created == 1

    revisions = session.query(RevisionHistory).filter_by(observation_id=obs.id).all()
    assert len(revisions) == 1
    rev = revisions[0]

    assert rev.from_vintage_id == vintage_1.id
    assert rev.to_vintage_id == vintage_2.id
    assert rev.revision_number == 1
    assert rev.previous_value == v1_val
    assert rev.new_value == v2_val
    assert rev.absolute_revision == Decimal("1.55000000")
    # Relative revision pct = (1.55 / 310.0) * 100 = 0.50%
    assert rev.relative_revision_pct == Decimal("0.50000000")
    assert rev.direction == "up"
    assert rev.revision_type == "value_revision"
    assert rev.revision_lag_days == (v2_date - obs_date).days

    # Zero modification/deletion verification: ensure vintage 1 row is completely unaltered
    session.refresh(vintage_1)
    assert vintage_1.value == v1_val
    assert vintage_1.vintage_date == v1_date

    # Verify data_event was emitted
    events = session.query(DataEvent).filter_by(series_id=series.id).all()
    # Expect 1 initial observation event and 1 revision_batch event
    assert len(events) >= 2
    revision_events = [e for e in events if e.event_type == "revision_batch"]
    assert len(revision_events) == 1
    rev_event = revision_events[0]
    assert rev_event.payload["total_revisions"] == 1
    assert rev_event.payload["up_revisions_count"] == 1
    assert rev_event.is_backfill is False

    # ---------------------------------------------------------
    # 5. Dispatch Alert via Mocked Telegram Client
    # ---------------------------------------------------------
    mock_telegram = MagicMock()
    mock_telegram.send_message.return_value = [10001]

    dispatch_res = dispatch_notifications(
        session=session,
        telegram_client=mock_telegram,
        now=now,
    )

    assert dispatch_res.eligible_events_found >= 1
    assert dispatch_res.sent_count >= 1
    assert mock_telegram.send_message.called

    notifications = session.query(NotificationHistory).filter_by(event_id=rev_event.id).all()
    assert len(notifications) == 1
    notif = notifications[0]
    assert notif.status == "sent"
    assert notif.telegram_message_id == 10001
    assert "CPI" in notif.message_text
    assert "Revision" in notif.message_text

    # ---------------------------------------------------------
    # 6. Idempotency Proof: Second Run Produces Zero Changes
    # ---------------------------------------------------------
    second_res = detect_revisions(session=session, series_codes=["CPI"], now=now)
    assert second_res.total_revisions_created == 0
    assert second_res.total_new_observations == 0

    total_revisions = session.query(RevisionHistory).count()
    assert total_revisions == 1, "Idempotency violated: duplicate revision rows created!"

    total_events = session.query(DataEvent).count()
    assert total_events == len(events), "Idempotency violated: duplicate data_events created!"

    mock_telegram.reset_mock()
    second_dispatch = dispatch_notifications(
        session=session,
        telegram_client=mock_telegram,
        now=now,
    )
    assert second_dispatch.sent_count == 0, "Idempotency violated: duplicate notification sent!"
    assert not mock_telegram.send_message.called
