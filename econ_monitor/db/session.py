"""Database engine, sessionmaker, context managers, and FastAPI dependencies."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from econ_monitor.core.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine(db_url: str | None = None) -> Engine:
    """Return an active SQLAlchemy engine, lazily initializing if needed."""
    global _engine, _session_factory
    if db_url is not None:
        return create_engine(db_url, pool_pre_ping=True)

    if _engine is None:
        settings = get_settings()
        url = settings.require_database_url()
        _engine = create_engine(url, pool_pre_ping=True)
        _session_factory = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    return _engine


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Return a sessionmaker factory."""
    global _session_factory
    if engine is not None:
        return sessionmaker(bind=engine, autocommit=False, autoflush=False)
    if _session_factory is None:
        eng = get_engine()
        _session_factory = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    return _session_factory


@contextmanager
def session_scope(custom_engine: Engine | None = None) -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations."""
    factory = get_session_factory(custom_engine)
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a database session."""
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Reset global engine and sessionmaker (useful in tests)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
