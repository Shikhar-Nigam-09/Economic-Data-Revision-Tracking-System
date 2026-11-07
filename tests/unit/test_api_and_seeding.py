"""Unit tests for FastAPI endpoints and database seeding."""

from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from econ_monitor.core.yaml_config import load_indicators_config
from econ_monitor.db.repositories import get_series_by_code, seed_economic_series


def test_seeding_idempotency(in_memory_db_session: Session):
    """Verify that seed_economic_series inserts on first run and safely
    updates on subsequent runs."""
    config = load_indicators_config("config/indicators.yaml")

    # First run: inserts all indicators
    inserted, updated = seed_economic_series(in_memory_db_session, config)
    assert inserted == len(config.indicators)
    assert updated == 0

    # Second run: updates existing, inserts none
    inserted_2, updated_2 = seed_economic_series(in_memory_db_session, config)
    assert inserted_2 == 0
    assert updated_2 == len(config.indicators)

    # Verify GDP exists and has expected attributes
    gdp = get_series_by_code(in_memory_db_session, "GDP")
    assert gdp is not None
    assert gdp.code == "GDP"
    assert gdp.fred_series_id == "GDPC1"
    assert gdp.is_active is True
    assert gdp.expected_max == Decimal("200000.00000000")


def test_health_endpoint_db_connected(client_with_db: TestClient):
    """Verify /health returns 200 and 'connected' when database check succeeds."""
    with patch("econ_monitor.api.main.get_engine") as mock_get_engine:
        # Simulate successful connection
        mock_conn = mock_get_engine.return_value.connect.return_value.__enter__.return_value
        mock_conn.execute.return_value = None

        response = client_with_db.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["database"] == "connected"


def test_health_endpoint_db_disconnected(client_with_db: TestClient):
    """Verify /health returns 503 and 'disconnected' when database is unreachable."""
    with patch(
        "econ_monitor.api.main.get_engine", side_effect=Exception("Database connection refused")
    ):
        response = client_with_db.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert data["database"] == "disconnected"


def test_series_endpoints(client_with_db: TestClient, in_memory_db_session: Session):
    """Verify GET /api/v1/series and GET /api/v1/series/{code}."""
    config = load_indicators_config("config/indicators.yaml")
    seed_economic_series(in_memory_db_session, config)
    in_memory_db_session.commit()

    # 1. List all series
    resp = client_with_db.get("/api/v1/series")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == len(config.indicators)
    assert len(data["items"]) == len(config.indicators)

    # 2. Filter active only
    resp_active = client_with_db.get("/api/v1/series?active_only=true")
    assert resp_active.status_code == 200
    data_active = resp_active.json()
    assert data_active["total"] == sum(1 for i in config.indicators if i.active)

    # 3. Retrieve specific series
    resp_gdp = client_with_db.get("/api/v1/series/GDP")
    assert resp_gdp.status_code == 200
    gdp_data = resp_gdp.json()
    assert gdp_data["code"] == "GDP"
    assert gdp_data["fred_series_id"] == "GDPC1"

    # 4. Unknown series returns 404 with structured error
    resp_404 = client_with_db.get("/api/v1/series/NONEXISTENT")
    assert resp_404.status_code == 404
    err_data = resp_404.json()
    assert "error" in err_data
    assert err_data["error"]["code"] == "SERIES_NOT_FOUND"
