"""Database repositories for series access and seeding."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from econ_monitor.core.clock import get_utc_now
from econ_monitor.core.yaml_config import IndicatorsRootConfig
from econ_monitor.db.models import EconomicSeries


def get_all_series(session: Session, active_only: bool = False) -> list[EconomicSeries]:
    """Retrieve all economic series from the database."""
    stmt = select(EconomicSeries).order_by(EconomicSeries.id)
    if active_only:
        stmt = stmt.where(EconomicSeries.is_active.is_(True))
    return list(session.scalars(stmt).all())


def get_series_by_code(session: Session, code: str) -> EconomicSeries | None:
    """Retrieve an economic series by its unique uppercase code."""
    stmt = select(EconomicSeries).where(EconomicSeries.code == code.upper())
    return session.scalars(stmt).first()


def seed_economic_series(session: Session, config: IndicatorsRootConfig) -> tuple[int, int]:
    """Idempotently seed and sync economic series definitions from YAML configuration.

    Returns:
        Tuple[int, int]: (inserted_count, updated_count)
    """
    inserted = 0
    updated = 0
    now = get_utc_now()

    for item in config.indicators:
        series = get_series_by_code(session, item.code)
        if series is None:
            series = EconomicSeries(
                code=item.code.upper(),
                fred_series_id=item.fred_series_id,
                title=item.display_name,
                frequency="M",  # Default baseline, updated from live FRED metadata in Phase 2
                units="Index/Level",
                seasonal_adjustment="Seasonally Adjusted",
                change_unit_label=item.change_unit_label,
                anomaly_transform=item.anomaly_transform,
                expected_min=Decimal(str(item.expected_min)),
                expected_max=Decimal(str(item.expected_max)),
                max_stale_lag_days=item.max_stale_lag_days,
                initial_release_max_lag_days=item.initial_release_max_lag_days,
                history_start=item.history_start,
                is_active=item.active,
                created_at=now,
                updated_at=now,
            )
            session.add(series)
            inserted += 1
        else:
            # Update only config-derived fields; preserve runtime data and metadata
            series.fred_series_id = item.fred_series_id
            series.title = item.display_name
            series.change_unit_label = item.change_unit_label
            series.anomaly_transform = item.anomaly_transform
            series.expected_min = Decimal(str(item.expected_min))
            series.expected_max = Decimal(str(item.expected_max))
            series.max_stale_lag_days = item.max_stale_lag_days
            series.initial_release_max_lag_days = item.initial_release_max_lag_days
            series.history_start = item.history_start
            series.is_active = item.active
            series.updated_at = now
            updated += 1

    session.flush()
    return inserted, updated
