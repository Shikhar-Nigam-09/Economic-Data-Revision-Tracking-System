"""Pytest global fixtures and configuration."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from econ_monitor.api.deps import get_db
from econ_monitor.api.main import create_app
from econ_monitor.db.base import Base


@pytest.fixture
def api_client() -> TestClient:
    """Return a FastAPI TestClient instance."""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def in_memory_db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite session for fast unit testing of repositories."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Enable SQLite foreign key enforcement
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys = ON;")

    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client_with_db(in_memory_db_session: Session) -> Generator[TestClient, None, None]:
    """Return a TestClient with database dependency overridden by the in-memory session."""
    app = create_app()

    def override_get_db():
        yield in_memory_db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
