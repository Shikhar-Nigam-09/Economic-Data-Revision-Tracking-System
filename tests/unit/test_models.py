"""Unit tests for SQLAlchemy models and mapper relationships."""

from sqlalchemy.orm import configure_mappers

from econ_monitor.db.base import Base


def test_all_mappers_resolve():
    """Verify that configure_mappers() resolves with no missing foreign keys
    or relationship errors."""
    configure_mappers()


def test_all_expected_tables_present():
    """Verify all 14 required tables exist in the declarative metadata."""
    expected_tables = {
        "economic_series",
        "observations",
        "observation_vintages",
        "revision_history",
        "data_events",
        "anomaly_records",
        "data_quality_logs",
        "data_quality_scores",
        "subscribers",
        "subscriber_preferences",
        "notification_history",
        "ingestion_runs",
        "ingestion_run_series",
        "app_state",
    }
    actual_tables = set(Base.metadata.tables.keys())
    assert expected_tables.issubset(actual_tables)


def test_naming_convention_applied():
    """Verify that the naming convention is attached to declarative metadata."""
    assert Base.metadata.naming_convention is not None
    assert "ck" in Base.metadata.naming_convention
    assert "fk" in Base.metadata.naming_convention
    assert "pk" in Base.metadata.naming_convention
    assert "uq" in Base.metadata.naming_convention
    assert "ix" in Base.metadata.naming_convention
