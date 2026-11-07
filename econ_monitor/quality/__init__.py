"""Package initialization for data quality module."""

from econ_monitor.quality.checks import CheckResult
from econ_monitor.quality.schemas import (
    get_latest_value_frame_schema,
    get_vintage_frame_schema,
)
from econ_monitor.quality.scoring import HealthScoreResult, compute_health_score
from econ_monitor.quality.service import (
    QualityServiceResult,
    SeriesQualityReport,
    evaluate_series_quality,
    run_data_quality_service,
)

__all__ = [
    "CheckResult",
    "get_vintage_frame_schema",
    "get_latest_value_frame_schema",
    "HealthScoreResult",
    "compute_health_score",
    "SeriesQualityReport",
    "QualityServiceResult",
    "evaluate_series_quality",
    "run_data_quality_service",
]
