"""Package initialization for revisions engine."""

from econ_monitor.revisions.analytics import (
    DirectionCounts,
    FirstVsLatestComparison,
    HistogramBin,
    LagBucketMetric,
    MagnitudeDistribution,
    PeriodRevisionMetric,
    RevisionsPerObservationFrequency,
    SeriesRevisionSummary,
    get_direction_counts,
    get_first_vs_latest_comparison,
    get_magnitude_distribution,
    get_revisions_by_lag_bucket,
    get_revisions_by_period,
    get_revisions_per_observation_frequency,
    get_series_revision_summary,
)
from econ_monitor.revisions.detector import (
    DetectionResult,
    SeriesDetectionCounts,
    detect_revisions,
    detect_revisions_for_series,
)
from econ_monitor.revisions.events import (
    create_new_observation_event,
    create_quality_issue_event,
    create_revision_batch_event,
    is_series_event_baseline_set,
    mark_series_event_baseline_set,
)
from econ_monitor.revisions.metrics import (
    RevisionMetrics,
    compute_revision_metrics,
    format_revision_change,
)

__all__ = [
    "RevisionMetrics",
    "compute_revision_metrics",
    "format_revision_change",
    "SeriesDetectionCounts",
    "DetectionResult",
    "detect_revisions",
    "detect_revisions_for_series",
    "create_new_observation_event",
    "create_revision_batch_event",
    "create_quality_issue_event",
    "is_series_event_baseline_set",
    "mark_series_event_baseline_set",
    "SeriesRevisionSummary",
    "DirectionCounts",
    "HistogramBin",
    "MagnitudeDistribution",
    "PeriodRevisionMetric",
    "LagBucketMetric",
    "RevisionsPerObservationFrequency",
    "FirstVsLatestComparison",
    "get_series_revision_summary",
    "get_direction_counts",
    "get_magnitude_distribution",
    "get_revisions_by_period",
    "get_revisions_by_lag_bucket",
    "get_revisions_per_observation_frequency",
    "get_first_vs_latest_comparison",
]
