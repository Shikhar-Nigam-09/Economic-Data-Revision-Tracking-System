"""Individual data quality checks specified in A9 returning typed CheckResult objects."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from econ_monitor.core.logging import get_logger
from econ_monitor.db.models import (
    DataQualityLog,
    EconomicSeries,
)
from econ_monitor.quality.schemas import get_latest_value_frame_schema, get_vintage_frame_schema

logger = get_logger("econ_monitor.quality.checks")


@dataclass
class CheckResult:
    """Outcome of a single data quality check."""

    check_name: str
    severity: str  # 'info', 'warning', 'error'
    passed: bool
    affected_count: int
    message: str
    details: Optional[Dict[str, Any]] = None
    checked_records_count: int = 0
    failing_records_count: int = 0


def check_missing_values(
    session: Session,
    series: EconomicSeries,
    latest_df: pd.DataFrame,
) -> CheckResult:
    """Check for NULL / missing values among latest vintages (Severity: warning)."""
    total = len(latest_df)
    if total == 0:
        return CheckResult(
            check_name="missing_values",
            severity="warning",
            passed=True,
            affected_count=0,
            message="No observations to check.",
            checked_records_count=0,
            failing_records_count=0,
        )

    # Missing rows have is_missing == True or latest_value is None
    missing_mask = latest_df["is_missing"] | latest_df["latest_value"].isna()
    missing_count = int(missing_mask.sum())
    passed = missing_count == 0

    return CheckResult(
        check_name="missing_values",
        severity="warning",
        passed=passed,
        affected_count=missing_count,
        message=f"{missing_count} missing values found out of {total} latest observations."
        if not passed
        else f"All {total} latest observations have non-null values.",
        details={"total_observations": total, "missing_count": missing_count},
        checked_records_count=total,
        failing_records_count=missing_count,
    )


def check_duplicate_observations(
    session: Session,
    series: EconomicSeries,
    vintages_df: pd.DataFrame,
) -> CheckResult:
    """Check for duplicate observations or duplicate vintage keys (Severity: error)."""
    total = len(vintages_df)
    if total == 0:
        return CheckResult(
            check_name="duplicate_observations",
            severity="error",
            passed=True,
            affected_count=0,
            message="No vintages to check.",
            checked_records_count=0,
            failing_records_count=0,
        )

    # Check duplicate (observation_date, vintage_date)
    dup_mask = vintages_df.duplicated(subset=["observation_date", "vintage_date"], keep=False)
    dup_count = int(dup_mask.sum())
    passed = dup_count == 0

    return CheckResult(
        check_name="duplicate_observations",
        severity="error",
        passed=passed,
        affected_count=dup_count,
        message=f"{dup_count} duplicate vintage records detected."
        if not passed
        else "No duplicate observation or vintage keys found.",
        details={"duplicate_count": dup_count},
        checked_records_count=total,
        failing_records_count=dup_count,
    )


def check_invalid_dates(
    session: Session,
    series: EconomicSeries,
    vintages_df: pd.DataFrame,
    today_date: date,
) -> CheckResult:
    """Check for impossible dates, future observations, or vintage prior to observation.

    Includes rows rejected at ingestion (Severity: error).
    """
    total = len(vintages_df)
    if total == 0:
        return CheckResult(
            check_name="invalid_dates",
            severity="error",
            passed=True,
            affected_count=0,
            message="No records to check.",
            checked_records_count=0,
            failing_records_count=0,
        )

    # Query ingestion rejected rows from data_quality_logs for this series
    rejected_date_logs_count = session.scalars(
        select(func.count(DataQualityLog.id)).where(
            DataQualityLog.series_id == series.id,
            DataQualityLog.check_name == "ingestion_rejected_row",
            DataQualityLog.message.like("%date%"),
        )
    ).one()

    invalid_count = rejected_date_logs_count
    future_obs_count = 0
    vintage_before_obs_count = 0

    for _, row in vintages_df.iterrows():
        obs_d = row["observation_date"]
        v_d = row["vintage_date"]

        if isinstance(obs_d, datetime):
            obs_d = obs_d.date()
        if isinstance(v_d, datetime):
            v_d = v_d.date()

        if obs_d > today_date:
            future_obs_count += 1
            invalid_count += 1
        elif v_d < obs_d:
            vintage_before_obs_count += 1
            invalid_count += 1

    passed = invalid_count == 0
    msg = (
        f"{invalid_count} invalid date violations (future: {future_obs_count}, "
        f"vintage < obs: {vintage_before_obs_count}, rejected: {rejected_date_logs_count})."
        if not passed
        else "All observation and vintage dates are valid and logically ordered."
    )
    return CheckResult(
        check_name="invalid_dates",
        severity="error",
        passed=passed,
        affected_count=invalid_count,
        message=msg,
        details={
            "future_observations": future_obs_count,
            "vintage_before_obs": vintage_before_obs_count,
            "rejected_at_ingestion": rejected_date_logs_count,
        },
        checked_records_count=total + rejected_date_logs_count,
        failing_records_count=invalid_count,
    )


def check_invalid_numeric_format(
    session: Session,
    series: EconomicSeries,
    vintages_df: pd.DataFrame,
) -> CheckResult:
    """Non-numeric value strings rejected at ingestion (Severity: error)."""
    # Count rows rejected during ingestion due to numeric parsing errors
    rejected_num_logs_count = session.scalars(
        select(func.count(DataQualityLog.id)).where(
            DataQualityLog.series_id == series.id,
            DataQualityLog.check_name == "ingestion_rejected_row",
            DataQualityLog.message.like("%numeric%"),
        )
    ).one()

    total = len(vintages_df) + rejected_num_logs_count
    passed = rejected_num_logs_count == 0

    return CheckResult(
        check_name="invalid_numeric_format",
        severity="error",
        passed=passed,
        affected_count=rejected_num_logs_count,
        message=f"{rejected_num_logs_count} non-numeric value strings rejected at ingestion."
        if not passed
        else "All numeric values cleanly parsed as valid Decimals.",
        details={"rejected_numeric_count": rejected_num_logs_count},
        checked_records_count=total,
        failing_records_count=rejected_num_logs_count,
    )


def check_unexpected_nulls(
    session: Session,
    series: EconomicSeries,
    vintages_df: pd.DataFrame,
) -> CheckResult:
    """Check for NULL in fields that must never be NULL (Severity: error)."""
    total = len(vintages_df)
    if total == 0:
        return CheckResult(
            check_name="unexpected_nulls",
            severity="error",
            passed=True,
            affected_count=0,
            message="No records to check.",
            checked_records_count=0,
            failing_records_count=0,
        )

    null_obs = vintages_df["observation_date"].isna().sum()
    null_vint = vintages_df["vintage_date"].isna().sum()
    null_missing = vintages_df["is_missing"].isna().sum()

    total_nulls = int(null_obs + null_vint + null_missing)
    passed = total_nulls == 0

    return CheckResult(
        check_name="unexpected_nulls",
        severity="error",
        passed=passed,
        affected_count=total_nulls,
        message=f"{total_nulls} unexpected NULL values detected in required fields."
        if not passed
        else "No unexpected NULLs in required columns.",
        details={
            "null_observation_dates": int(null_obs),
            "null_vintage_dates": int(null_vint),
            "null_is_missing": int(null_missing),
        },
        checked_records_count=total,
        failing_records_count=total_nulls,
    )


def check_frequency_consistency(
    session: Session,
    series: EconomicSeries,
    latest_df: pd.DataFrame,
) -> CheckResult:
    """Spacing of recent observations matches the series' frequency (Severity: warning)."""
    total = len(latest_df)
    if total < 2:
        return CheckResult(
            check_name="frequency_consistency",
            severity="warning",
            passed=True,
            affected_count=0,
            message="Insufficient observations to test spacing.",
            checked_records_count=total,
            failing_records_count=0,
        )

    # Sort observation dates
    obs_dates = sorted(
        [d.date() if isinstance(d, datetime) else d for d in latest_df["observation_date"].tolist()]
    )

    freq = series.frequency.upper()
    inconsistent_count = 0

    # Test last 12 observation gaps
    recent_dates = obs_dates[-13:]
    for i in range(len(recent_dates) - 1):
        d1 = recent_dates[i]
        d2 = recent_dates[i + 1]
        days_gap = (d2 - d1).days

        if freq == "M":
            # Monthly spacing: typically 28 to 31 days
            if not (27 <= days_gap <= 32):
                inconsistent_count += 1
        elif freq == "Q":
            # Quarterly spacing: typically 89 to 93 days
            if not (88 <= days_gap <= 94):
                inconsistent_count += 1
        elif freq == "A":
            # Annual spacing: typically 365 to 366 days
            if not (364 <= days_gap <= 367):
                inconsistent_count += 1

    passed = inconsistent_count == 0
    checked_count = len(recent_dates) - 1

    msg = (
        f"{inconsistent_count} of {checked_count} intervals deviated from {freq} cadence."
        if not passed
        else f"Recent observations strictly adhere to {freq} frequency intervals."
    )
    return CheckResult(
        check_name="frequency_consistency",
        severity="warning",
        passed=passed,
        affected_count=inconsistent_count,
        message=msg,
        details={"frequency": freq, "inconsistent_count": inconsistent_count},
        checked_records_count=checked_count,
        failing_records_count=inconsistent_count,
    )


def check_gaps_in_expected_grid(
    session: Session,
    series: EconomicSeries,
    latest_df: pd.DataFrame,
    today_date: date,
) -> CheckResult:
    """Check for missing expected grid dates since history_start (Severity: warning).

    Uses pandas offsets MS, QS, YS. Legitimate spacing is never flagged as a gap.
    """
    freq = series.frequency.upper()
    offset_map = {"M": "MS", "Q": "QS", "A": "YS"}
    offset_str = offset_map.get(freq, "MS")

    if latest_df.empty:
        return CheckResult(
            check_name="gaps_in_expected_grid",
            severity="warning",
            passed=False,
            affected_count=1,
            message="No observations found in series.",
            checked_records_count=1,
            failing_records_count=1,
        )

    obs_dates = set(
        [d.date() if isinstance(d, datetime) else d for d in latest_df["observation_date"].tolist()]
    )

    start_date = series.history_start
    max_obs_date = max(obs_dates)

    # Expected grid using pandas date_range
    expected_grid = [
        ts.date()
        for ts in pd.date_range(start=start_date, end=max_obs_date, freq=offset_str).tolist()
    ]

    total_expected = len(expected_grid)
    missing_dates = [d for d in expected_grid if d not in obs_dates]
    missing_count = len(missing_dates)

    passed = missing_count == 0
    msg = (
        f"{missing_count} grid periods missing of {total_expected} since {start_date}."
        if not passed
        else f"All {total_expected} expected grid periods present with no missing intervals."
    )
    return CheckResult(
        check_name="gaps_in_expected_grid",
        severity="warning",
        passed=passed,
        affected_count=missing_count,
        message=msg,
        details={
            "total_expected": total_expected,
            "missing_grid_dates_count": missing_count,
            "first_few_missing": [d.isoformat() for d in missing_dates[:5]],
        },
        checked_records_count=total_expected,
        failing_records_count=missing_count,
    )


def check_range_violations(
    session: Session,
    series: EconomicSeries,
    latest_df: pd.DataFrame,
) -> CheckResult:
    """Latest values outside expected_min and expected_max (Severity: error)."""
    total = len(latest_df)
    if total == 0:
        return CheckResult(
            check_name="range_violations",
            severity="error",
            passed=True,
            affected_count=0,
            message="No observations to check.",
            checked_records_count=0,
            failing_records_count=0,
        )

    min_val = float(series.expected_min)
    max_val = float(series.expected_max)

    valid_vals = latest_df[latest_df["latest_value"].notna()]["latest_value"].astype(float)
    checked_count = len(valid_vals)

    violations = valid_vals[(valid_vals < min_val) | (valid_vals > max_val)]
    viol_count = len(violations)
    passed = viol_count == 0

    msg = (
        f"{viol_count} observations outside sane range [{min_val}, {max_val}]."
        if not passed
        else f"All {checked_count} latest observations fall in [{min_val}, {max_val}]."
    )
    return CheckResult(
        check_name="range_violations",
        severity="error",
        passed=passed,
        affected_count=viol_count,
        message=msg,
        details={
            "expected_min": min_val,
            "expected_max": max_val,
            "violating_count": viol_count,
        },
        checked_records_count=checked_count,
        failing_records_count=viol_count,
    )


def check_staleness(
    session: Session,
    series: EconomicSeries,
    latest_df: pd.DataFrame,
    today_date: date,
) -> CheckResult:
    """Check if series is stale beyond max_stale_lag_days (Severity: warning)."""
    if latest_df.empty:
        return CheckResult(
            check_name="staleness",
            severity="warning",
            passed=False,
            affected_count=1,
            message="No observations exist; series is stale.",
            checked_records_count=1,
            failing_records_count=1,
        )

    obs_dates = [
        d.date() if isinstance(d, datetime) else d for d in latest_df["observation_date"].tolist()
    ]
    latest_obs_date = max(obs_dates)

    # Estimate one period length
    freq = series.frequency.upper()
    period_days = 30 if freq == "M" else (90 if freq == "Q" else 365)

    end_of_period = pd.to_datetime(latest_obs_date) + pd.Timedelta(days=period_days)
    lag_days = (today_date - end_of_period.date()).days

    max_allowed = series.max_stale_lag_days
    is_stale = lag_days > max_allowed
    days_overdue = max(0, lag_days - max_allowed)

    msg = (
        f"Series stale by {days_overdue} days (lag: {lag_days}d, max: {max_allowed}d)."
        if is_stale
        else f"Series is fresh (lag: {lag_days} days <= allowed: {max_allowed} days)."
    )
    return CheckResult(
        check_name="staleness",
        severity="warning",
        passed=not is_stale,
        affected_count=1 if is_stale else 0,
        message=msg,
        details={
            "latest_observation_date": latest_obs_date.isoformat(),
            "lag_days": lag_days,
            "max_allowed_lag_days": max_allowed,
            "days_overdue": days_overdue,
        },
        checked_records_count=max_allowed,
        failing_records_count=days_overdue,
    )


def check_pandera_schema(
    session: Session,
    series: EconomicSeries,
    vintages_df: pd.DataFrame,
    latest_df: pd.DataFrame,
) -> CheckResult:
    """Pandera validation (lazy=True) of vintage and latest-value frames (Severity: error)."""
    total = len(vintages_df) + len(latest_df)
    if total == 0:
        return CheckResult(
            check_name="pandera_schema",
            severity="error",
            passed=True,
            affected_count=0,
            message="No records to validate.",
            checked_records_count=0,
            failing_records_count=0,
        )

    vintage_schema = get_vintage_frame_schema()
    latest_schema = get_latest_value_frame_schema(
        expected_min=float(series.expected_min),
        expected_max=float(series.expected_max),
    )

    failures: List[str] = []

    try:
        vintage_schema.validate(vintages_df, lazy=True)
    except Exception as exc:
        failures.append(f"Vintage frame schema error: {exc}")

    try:
        latest_schema.validate(latest_df, lazy=True)
    except Exception as exc:
        failures.append(f"Latest value frame schema error: {exc}")

    passed = len(failures) == 0
    return CheckResult(
        check_name="pandera_schema",
        severity="error",
        passed=passed,
        affected_count=len(failures),
        message="Pandera schemas validated successfully."
        if passed
        else f"Pandera schema validation failed: {'; '.join(failures)}",
        details={"failures": failures},
        checked_records_count=total,
        failing_records_count=len(failures),
    )
