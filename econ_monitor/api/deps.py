"""FastAPI route dependencies."""

from sqlalchemy.orm import Session

from econ_monitor.db.session import get_db

__all__ = ["Session", "get_db"]
