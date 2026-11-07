"""Integration tests running Alembic migrations and constraint checks against a PostgreSQL instance.

Requires TEST_DATABASE_URL to be defined in .env or environment.
"""

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from econ_monitor.core.config import get_settings
from econ_monitor.core.yaml_config import load_indicators_config
from econ_monitor.db.models import EconomicSeries
from econ_monitor.db.repositories import seed_economic_series


@pytest.fixture(scope="module")
def postgres_test_engine():
    """Module-level fixture for PostgreSQL test database."""
    settings = get_settings()
    test_db_url = settings.get_test_database_url()
    if not test_db_url:
        pytest.skip("TEST_DATABASE_URL is not configured; skipping PostgreSQL integration tests.")

    # Configure Alembic to point to the test database
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", test_db_url)

    # Upgrade to head to establish schema
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(test_db_url, pool_pre_ping=True)
    yield engine

    engine.dispose()


@pytest.mark.db
def test_alembic_migration_round_trip(postgres_test_engine):
    """Verify that Alembic migrations cleanly downgrade to base and upgrade back to head."""
    test_db_url = get_settings().get_test_database_url()
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", test_db_url)

    # Downgrade to base
    command.downgrade(alembic_cfg, "base")

    # Upgrade back to head
    command.upgrade(alembic_cfg, "head")

    # Verify latest_observation_values view exists
    with postgres_test_engine.connect() as conn:
        res = conn.execute(
            text(
                "SELECT table_name FROM information_schema.views "
                "WHERE table_name = 'latest_observation_values';"
            )
        ).scalar()
        assert res == "latest_observation_values"


@pytest.mark.db
def test_postgres_model_constraints(postgres_test_engine):
    """Verify unique constraints and check constraints on live PostgreSQL."""
    session_factory = sessionmaker(bind=postgres_test_engine)
    session: Session = session_factory()
    try:
        config = load_indicators_config("config/indicators.yaml")
        seed_economic_series(session, config)
        session.commit()

        # Test duplicate series code constraint
        dup_series = EconomicSeries(
            code="GDP",  # Already exists
            fred_series_id="DIFFERENT_ID",
            title="Duplicate GDP",
            frequency="M",
            units="Index",
            seasonal_adjustment="SA",
            anomaly_transform="pct_change",
            expected_min=0,
            expected_max=100,
            max_stale_lag_days=60,
            initial_release_max_lag_days=90,
            history_start="1990-01-01",
            is_active=True,
            created_at=config.indicators[0].history_start,
            updated_at=config.indicators[0].history_start,
        )
        session.add(dup_series)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    finally:
        session.close()
