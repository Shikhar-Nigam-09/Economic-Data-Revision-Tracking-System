"""Deterministic health score computation per A9 specification."""

from decimal import Decimal
from typing import Dict, NamedTuple

from econ_monitor.core.yaml_config import QualityWeights
from econ_monitor.quality.checks import CheckResult


class HealthScoreResult(NamedTuple):
    """Calculated health score and component breakdown."""

    composite_score: Decimal
    component_scores: Dict[str, float]


def compute_health_score(
    check_results: Dict[str, CheckResult],
    weights: QualityWeights,
) -> HealthScoreResult:
    """Calculate composite health score (0 to 100) per A9 formula.

    Component formulas:
    - Component score = 100 * (1 - failing_fraction), clamped to [0, 100].
    - Timeliness falls linearly with days overdue, reaching 0 when overdue days == allowed lag.
    - Composite score is weighted sum divided by 100.
    """
    # 1. Completeness (missing_values, gaps_in_expected_grid)
    comp_checks = [check_results.get("missing_values"), check_results.get("gaps_in_expected_grid")]
    comp_checked = sum(c.checked_records_count for c in comp_checks if c)
    comp_failed = sum(c.failing_records_count for c in comp_checks if c)
    if comp_checked > 0:
        comp_score = max(0.0, min(100.0, 100.0 * (1.0 - (comp_failed / comp_checked))))
    else:
        comp_score = 100.0

    # 2. Validity (invalid_dates, invalid_numeric_format, range_violations, unexpected_nulls)
    val_checks = [
        check_results.get("invalid_dates"),
        check_results.get("invalid_numeric_format"),
        check_results.get("range_violations"),
        check_results.get("unexpected_nulls"),
    ]
    val_checked = sum(c.checked_records_count for c in val_checks if c)
    val_failed = sum(c.failing_records_count for c in val_checks if c)
    if val_checked > 0:
        val_score = max(0.0, min(100.0, 100.0 * (1.0 - (val_failed / val_checked))))
    else:
        val_score = 100.0

    # 3. Uniqueness (duplicate_observations)
    uniq_check = check_results.get("duplicate_observations")
    if uniq_check and uniq_check.checked_records_count > 0:
        uniq_score = max(
            0.0,
            min(
                100.0,
                100.0
                * (1.0 - (uniq_check.failing_records_count / uniq_check.checked_records_count)),
            ),
        )
    else:
        uniq_score = 100.0

    # 4. Frequency Consistency (frequency_consistency)
    freq_check = check_results.get("frequency_consistency")
    if freq_check and freq_check.checked_records_count > 0:
        freq_score = max(
            0.0,
            min(
                100.0,
                100.0
                * (1.0 - (freq_check.failing_records_count / freq_check.checked_records_count)),
            ),
        )
    else:
        freq_score = 100.0

    # 5. Timeliness (staleness)
    # Falls linearly with days overdue and reaches 0 when overdue days equal the allowed lag.
    stale_check = check_results.get("staleness")
    if stale_check and stale_check.details:
        days_overdue = stale_check.details.get("days_overdue", 0)
        max_allowed = stale_check.details.get("max_allowed_lag_days", 1)
        if days_overdue <= 0:
            time_score = 100.0
        else:
            time_score = max(0.0, 100.0 * (1.0 - (days_overdue / max_allowed)))
    else:
        time_score = 100.0

    # 6. Schema Conformance (pandera_schema)
    schema_check = check_results.get("pandera_schema")
    if schema_check and schema_check.checked_records_count > 0:
        schema_score = max(
            0.0,
            min(
                100.0,
                100.0
                * (1.0 - (schema_check.failing_records_count / schema_check.checked_records_count)),
            ),
        )
    else:
        schema_score = 100.0

    component_scores = {
        "completeness": round(comp_score, 2),
        "validity": round(val_score, 2),
        "uniqueness": round(uniq_score, 2),
        "frequency_consistency": round(freq_score, 2),
        "timeliness": round(time_score, 2),
        "schema_conformance": round(schema_score, 2),
    }

    # Composite weighted average
    weighted_sum = (
        comp_score * weights.completeness
        + val_score * weights.validity
        + uniq_score * weights.uniqueness
        + freq_score * weights.frequency_consistency
        + time_score * weights.timeliness
        + schema_score * weights.schema_conformance
    )
    composite = round(weighted_sum / 100.0, 2)
    composite_dec = Decimal(str(composite))

    return HealthScoreResult(
        composite_score=composite_dec,
        component_scores=component_scores,
    )
