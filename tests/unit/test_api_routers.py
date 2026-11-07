"""Comprehensive unit tests for all Phase 5 FastAPI endpoints."""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from econ_monitor.core.clock import get_utc_now
from econ_monitor.core.yaml_config import load_indicators_config
from econ_monitor.db.models import (
    AnomalyRecord,
    AnomalyStatus,
    DataEvent,
    DataQualityLog,
    DataQualityScore,
    EconomicSeries,
    IngestionMode,
    IngestionRun,
    IngestionStatus,
    IngestionTrigger,
    NotificationHistory,
    NotificationStatus,
    Observation,
    ObservationVintage,
    RevisionDirection,
    RevisionHistory,
    RevisionType,
    Subscriber,
)
from econ_monitor.db.repositories import seed_economic_series


def _setup_seeded_series(db: Session) -> EconomicSeries:
    config = load_indicators_config("config/indicators.yaml")
    seed_economic_series(db, config)
    db.commit()
    return db.query(EconomicSeries).filter_by(code="GDP").first()


def test_series_sync_config_endpoint(client_with_db: TestClient, in_memory_db_session: Session):
    """Verify POST /api/v1/series/sync-config updates or inserts series from config."""
    resp = client_with_db.post("/api/v1/series/sync-config")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total"] == 7


def test_observations_and_vintages_endpoints(
    client_with_db: TestClient, in_memory_db_session: Session
):
    """Verify GET /series/{code}/observations and GET /observations/{id}/vintages."""
    now = get_utc_now()
    series = _setup_seeded_series(in_memory_db_session)

    # Insert test observation and 2 vintages
    obs = Observation(
        series_id=series.id,
        observation_date=date(2024, 1, 1),
        first_seen_at=now,
    )
    in_memory_db_session.add(obs)
    in_memory_db_session.flush()

    v1 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2024, 2, 1),
        value=Decimal("100.0"),
        is_missing=False,
        retrieved_at=now,
        source="ALFRED",
    )
    v2 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2024, 3, 1),
        value=Decimal("102.5"),
        is_missing=False,
        retrieved_at=now,
        source="ALFRED",
    )
    in_memory_db_session.add_all([v1, v2])
    in_memory_db_session.commit()

    # 1. Test latest view
    res = client_with_db.get("/api/v1/series/GDP/observations?view=latest")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert float(data["items"][0]["value"]) == 102.5
    assert data["items"][0]["vintage_count"] == 2

    # 2. Test first view
    res_first = client_with_db.get("/api/v1/series/GDP/observations?view=first")
    assert res_first.status_code == 200
    assert float(res_first.json()["items"][0]["value"]) == 100.0

    # 3. Test as_of view without date -> 422
    res_as_of_err = client_with_db.get("/api/v1/series/GDP/observations?view=as_of")
    assert res_as_of_err.status_code == 422

    # 4. Test as_of view with date
    res_as_of = client_with_db.get("/api/v1/series/GDP/observations?view=as_of&as_of=2024-02-15")
    assert res_as_of.status_code == 200
    assert float(res_as_of.json()["items"][0]["value"]) == 100.0
    assert res_as_of.json()["items"][0]["vintage_count"] == 1

    # 5. Test observation vintages list
    res_vints = client_with_db.get(f"/api/v1/observations/{obs.id}/vintages")
    assert res_vints.status_code == 200
    assert res_vints.json()["total"] == 2


def test_revisions_endpoints(client_with_db: TestClient, in_memory_db_session: Session):
    """Verify GET /api/v1/revisions and GET /api/v1/revisions/{id}."""
    now = get_utc_now()
    series = _setup_seeded_series(in_memory_db_session)

    obs = Observation(
        series_id=series.id,
        observation_date=date(2024, 1, 1),
        first_seen_at=now,
    )
    in_memory_db_session.add(obs)
    in_memory_db_session.flush()

    v1 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2024, 2, 1),
        value=Decimal("100.0"),
        is_missing=False,
        retrieved_at=now,
        source="ALFRED",
    )
    v2 = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2024, 3, 1),
        value=Decimal("105.0"),
        is_missing=False,
        retrieved_at=now,
        source="ALFRED",
    )
    in_memory_db_session.add_all([v1, v2])
    in_memory_db_session.flush()

    rev = RevisionHistory(
        observation_id=obs.id,
        series_id=series.id,
        from_vintage_id=v1.id,
        to_vintage_id=v2.id,
        revision_number=1,
        observation_date=date(2024, 1, 1),
        revision_date=date(2024, 3, 1),
        previous_value=Decimal("100.0"),
        new_value=Decimal("105.0"),
        absolute_revision=Decimal("5.0"),
        relative_revision_pct=Decimal("5.0"),
        direction=RevisionDirection.UP,
        revision_type=RevisionType.VALUE_REVISION,
        revision_lag_days=60,
        detected_at=now,
    )
    in_memory_db_session.add(rev)
    in_memory_db_session.commit()

    # List revisions with filters
    res = client_with_db.get("/api/v1/revisions?series=GDP&direction=up")
    assert res.status_code == 200
    assert res.json()["total"] == 1

    # Single revision
    res_single = client_with_db.get(f"/api/v1/revisions/{rev.id}")
    assert res_single.status_code == 200
    assert res_single.json()["series_code"] == "GDP"
    assert float(res_single.json()["absolute_revision"]) == 5.0

    # 404 for unknown revision
    assert client_with_db.get("/api/v1/revisions/99999").status_code == 404


def test_analytics_endpoints(client_with_db: TestClient, in_memory_db_session: Session):
    """Verify all /api/v1/analytics/revisions/* endpoints."""
    _setup_seeded_series(in_memory_db_session)

    # 1. Summary
    res = client_with_db.get("/api/v1/analytics/revisions/summary?series=GDP")
    assert res.status_code == 200
    assert res.json()["series_code"] == "GDP"

    # 2. Direction counts
    res_dir = client_with_db.get("/api/v1/analytics/revisions/direction-counts?series=GDP")
    assert res_dir.status_code == 200
    assert "upward" in res_dir.json()

    # 3. By period
    res_p = client_with_db.get("/api/v1/analytics/revisions/by-period?series=GDP")
    assert res_p.status_code == 200
    assert res_p.json()["series_code"] == "GDP"

    # 4. Magnitude distribution
    res_mag = client_with_db.get("/api/v1/analytics/revisions/magnitude-distribution?series=GDP")
    assert res_mag.status_code == 200
    assert "bins" in res_mag.json()

    # 5. By lag
    res_lag = client_with_db.get("/api/v1/analytics/revisions/by-lag?series=GDP")
    assert res_lag.status_code == 200
    assert len(res_lag.json()["items"]) == 5

    # 6. Frequency
    res_freq = client_with_db.get("/api/v1/analytics/revisions/frequency?series=GDP")
    assert res_freq.status_code == 200

    # 7. First vs latest
    res_fvl = client_with_db.get("/api/v1/analytics/revisions/first-vs-latest?series=GDP")
    assert res_fvl.status_code == 200
    assert res_fvl.json()["series_code"] == "GDP"


def test_anomalies_endpoints(client_with_db: TestClient, in_memory_db_session: Session):
    """Verify /api/v1/anomalies endpoints including listing, patch, and status."""
    now = get_utc_now()
    series = _setup_seeded_series(in_memory_db_session)

    obs = Observation(
        series_id=series.id,
        observation_date=date(2024, 1, 1),
        first_seen_at=now,
    )
    in_memory_db_session.add(obs)
    in_memory_db_session.flush()

    vint = ObservationVintage(
        observation_id=obs.id,
        vintage_date=date(2024, 2, 1),
        value=Decimal("100.0"),
        is_missing=False,
        retrieved_at=now,
        source="ALFRED",
    )
    in_memory_db_session.add(vint)
    in_memory_db_session.flush()

    anom = AnomalyRecord(
        series_id=series.id,
        observation_id=obs.id,
        vintage_id=vint.id,
        method="zscore",
        score=Decimal("3.5"),
        status=AnomalyStatus.FLAGGED,
        features={"zscore": 3.5},
        detected_at=now,
    )
    in_memory_db_session.add(anom)
    in_memory_db_session.commit()

    # 1. List anomalies
    res = client_with_db.get("/api/v1/anomalies?series=GDP")
    assert res.status_code == 200
    assert res.json()["total"] == 1
    assert float(res.json()["items"][0]["score"]) == 3.5

    # 2. Patch anomaly
    patch_res = client_with_db.patch(
        f"/api/v1/anomalies/{anom.id}",
        json={"status": "reviewed", "note": "Checked and confirmed valid shock."},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["status"] == "reviewed"
    assert patch_res.json()["note"] == "Checked and confirmed valid shock."

    # 3. Status check
    st_res = client_with_db.get("/api/v1/anomalies/status")
    assert st_res.status_code == 200


def test_quality_endpoints(client_with_db: TestClient, in_memory_db_session: Session):
    """Verify /api/v1/quality/* endpoints."""
    now = get_utc_now()
    series = _setup_seeded_series(in_memory_db_session)

    score = DataQualityScore(
        run_uuid="uuid-1",
        series_id=series.id,
        score=Decimal("95.50"),
        component_scores={"completeness": 100.0},
        computed_at=now,
    )
    log = DataQualityLog(
        run_uuid="uuid-1",
        series_id=series.id,
        check_name="missing_values",
        severity="warning",
        passed=True,
        affected_count=0,
        message="No missing values",
        checked_at=now,
    )
    in_memory_db_session.add_all([score, log])
    in_memory_db_session.commit()

    # 1. Latest scores
    res_latest = client_with_db.get("/api/v1/quality/scores/latest")
    assert res_latest.status_code == 200
    assert res_latest.json()["total"] == 1

    # 2. Score history
    res_hist = client_with_db.get("/api/v1/quality/scores/history?series=GDP")
    assert res_hist.status_code == 200
    assert res_hist.json()["total"] == 1

    # 3. Quality logs
    res_logs = client_with_db.get("/api/v1/quality/logs?series=GDP&severity=warning")
    assert res_logs.status_code == 200
    assert res_logs.json()["total"] == 1


def test_subscribers_endpoints(client_with_db: TestClient, in_memory_db_session: Session):
    """Verify /api/v1/subscribers CRUD, preferences, and test alert trigger."""
    _setup_seeded_series(in_memory_db_session)

    # 1. Create subscriber
    create_res = client_with_db.post(
        "/api/v1/subscribers",
        json={
            "telegram_chat_id": 987654321,
            "telegram_username": "testuser",
            "display_name": "Test User",
        },
    )
    assert create_res.status_code == 201
    sub_id = create_res.json()["id"]

    # 2. List subscribers
    list_res = client_with_db.get("/api/v1/subscribers")
    assert list_res.status_code == 200
    assert list_res.json()["total"] == 1

    # 3. Get single subscriber
    get_res = client_with_db.get(f"/api/v1/subscribers/{sub_id}")
    assert get_res.status_code == 200
    assert get_res.json()["telegram_username"] == "testuser"

    # 4. Patch subscriber
    patch_res = client_with_db.patch(
        f"/api/v1/subscribers/{sub_id}",
        json={"display_name": "Updated Name"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["display_name"] == "Updated Name"

    # 5. Get and update preferences
    pref_res = client_with_db.get(f"/api/v1/subscribers/{sub_id}/preferences")
    assert pref_res.status_code == 200
    assert len(pref_res.json()["items"]) == 5

    put_pref_res = client_with_db.put(
        f"/api/v1/subscribers/{sub_id}/preferences",
        json={"series_code": "all", "notify_anomaly": False},
    )
    assert put_pref_res.status_code == 200
    assert all(not p["notify_anomaly"] for p in put_pref_res.json()["items"])

    # 6. Test message without token
    test_msg_res = client_with_db.post(f"/api/v1/subscribers/{sub_id}/test-message")
    assert test_msg_res.status_code == 200
    assert test_msg_res.json()["success"] is False

    # 7. Deactivate
    del_res = client_with_db.delete(f"/api/v1/subscribers/{sub_id}")
    assert del_res.status_code == 200
    assert del_res.json()["is_active"] is False


def test_notifications_endpoints(client_with_db: TestClient, in_memory_db_session: Session):
    """Verify /api/v1/notifications, /events, and summary endpoints."""
    now = get_utc_now()
    series = _setup_seeded_series(in_memory_db_session)

    sub = Subscriber(
        telegram_chat_id=12345,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    event = DataEvent(
        event_key="evt-1",
        event_type="new_observation",
        series_id=series.id,
        payload={"msg": "new"},
        is_backfill=False,
        occurred_on=date(2024, 1, 1),
        created_at=now,
    )
    in_memory_db_session.add_all([sub, event])
    in_memory_db_session.flush()

    notif = NotificationHistory(
        subscriber_id=sub.id,
        event_id=event.id,
        channel="telegram",
        status=NotificationStatus.SENT,
        message_text="Test alert message",
        created_at=now,
    )
    in_memory_db_session.add(notif)
    in_memory_db_session.commit()

    # 1. List notifications
    res = client_with_db.get("/api/v1/notifications?status=sent")
    assert res.status_code == 200
    assert res.json()["total"] == 1

    # 2. Notification summary
    res_sum = client_with_db.get("/api/v1/notifications/summary")
    assert res_sum.status_code == 200
    assert res_sum.json()["sent"] == 1

    # 3. List events
    res_evts = client_with_db.get("/api/v1/events?series=GDP")
    assert res_evts.status_code == 200
    assert res_evts.json()["total"] == 1


def test_ingestion_endpoints(client_with_db: TestClient, in_memory_db_session: Session):
    """Verify /api/v1/ingestion/run (including 409 conflict guard) and run logs."""
    now = get_utc_now()
    _setup_seeded_series(in_memory_db_session)

    # 1. Trigger background ingestion
    res = client_with_db.post("/api/v1/ingestion/run?mode=snapshot&notify=false")
    assert res.status_code == 202
    assert res.json()["status"] == "accepted"

    # 2. Verify 409 Conflict when a run is marked as running
    run_rec = IngestionRun(
        run_uuid="uuid-running",
        mode=IngestionMode.API,
        trigger=IngestionTrigger.DASHBOARD,
        status=IngestionStatus.RUNNING,
        started_at=now,
    )
    in_memory_db_session.add(run_rec)
    in_memory_db_session.commit()

    res_conflict = client_with_db.post("/api/v1/ingestion/run")
    assert res_conflict.status_code == 409

    # 3. List runs
    res_runs = client_with_db.get("/api/v1/ingestion/runs")
    assert res_runs.status_code == 200
    assert res_runs.json()["total"] >= 1
