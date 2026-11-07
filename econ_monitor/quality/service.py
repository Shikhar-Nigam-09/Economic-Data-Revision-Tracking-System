"""Data quality evaluation service orchestrating checks, health scoring, and alert transitions."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, NamedTuple, Optional

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from econ_monitor.core.clock import get_utc_now, get_utc_today
from econ_monitor.core.logging import get_logger
from econ_monitor.core.yaml_config import MonitoringConfig, QualityWeights, load_monitoring_config
from econ_monitor.db.models import (
    DataQualityLog,
    DataQualityScore,
    EconomicSeries,
    Observation,
    ObservationVintage,
)
from econ_monitor.quality.checks import (
    CheckResult,
    check_duplicate_observations,
    check_frequency_consistency,
    check_gaps_in_expected_grid,
    check_invalid_dates,
    check_invalid_numeric_format,
    check_missing_values,
    check_pandera_schema,
    check_range_violations,
    check_staleness,
    check_unexpected_nulls,
)
from econ_monitor.quality.scoring import compute_health_score
from econ_monitor.revisions.events import create_quality_issue_event

logger = get_logger("econ_monitor.quality.service")


class SeriesQualityReport(NamedTuple):
    """Quality execution outcome for a single economic series."""

    series_code: str
    run_uuid: str
    composite_score: Decimal
    component_scores: Dict[str, float]
    checks_passed: int
    checks_failed: int
    check_results: Dict[str, CheckResult]
    quality_issue_events_emitted: int


class QualityServiceResult(NamedTuple):
    """Overall outcome of a data quality evaluation run."""

    run_uuid: str
    series_reports: Dict[str, SeriesQualityReport]
    total_checks_evaluated: int
    total_issues_emitted: int


def _load_series_frames(
    session: Session, series: EconomicSeries
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load historical vintages and latest observation values as DataFrames."""
    # 1. Historical vintages frame
    vintages_stmt = (
        select(
            EconomicSeries.fred_series_id,
            Observation.observation_date,
            ObservationVintage.vintage_date,
            ObservationVintage.source_realtime_end,
            ObservationVintage.value,
            ObservationVintage.is_missing,
        )
        .join(Observation, ObservationVintage.observation_id == Observation.id)
        .join(EconomicSeries, Observation.series_id == EconomicSeries.id)
        .where(EconomicSeries.id == series.id)
        .order_by(Observation.observation_date.asc(), ObservationVintage.vintage_date.asc())
    )
    vintages_rows = session.execute(vintages_stmt).all()
    if vintages_rows:
        vintages_df = pd.DataFrame(
            [r._asdict() for r in vintages_rows],
            columns=[
                "fred_series_id",
                "observation_date",
                "vintage_date",
                "source_realtime_end",
                "value",
                "is_missing",
            ],
        )
    else:
        vintages_df = pd.DataFrame(
            columns=[
                "fred_series_id",
                "observation_date",
                "vintage_date",
                "source_realtime_end",
                "value",
                "is_missing",
            ]
        )

    # 2. Latest value frame: latest vintage per observation
    subq = (
        select(
            ObservationVintage.observation_id,
            func.max(ObservationVintage.vintage_date).label("max_v_date"),
        )
        .join(Observation, ObservationVintage.observation_id == Observation.id)
        .where(Observation.series_id == series.id)
        .group_by(ObservationVintage.observation_id)
        .subquery()
    )

    latest_stmt = (
        select(
            Observation.observation_date,
            ObservationVintage.value.label("latest_value"),
            ObservationVintage.is_missing,
        )
        .join(Observation, ObservationVintage.observation_id == Observation.id)
        .join(
            subq,
            (ObservationVintage.observation_id == subq.c.observation_id)
            & (ObservationVintage.vintage_date == subq.c.max_v_date),
        )
        .where(Observation.series_id == series.id)
        .order_by(Observation.observation_date.asc())
    )
    latest_rows = session.execute(latest_stmt).all()
    if latest_rows:
        latest_df = pd.DataFrame(
            [r._asdict() for r in latest_rows],
            columns=["observation_date", "latest_value", "is_missing"],
        )
    else:
        latest_df = pd.DataFrame(columns=["observation_date", "latest_value", "is_missing"])

    return vintages_df, latest_df


def _get_previous_check_status(session: Session, series_id: int, check_name: str) -> Optional[bool]:
    """Retrieve the 'passed' status of the most recent completed check for this series."""
    stmt = (
        select(DataQualityLog.passed)
        .where(
            DataQualityLog.series_id == series_id,
            DataQualityLog.check_name == check_name,
        )
        .order_by(DataQualityLog.checked_at.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def evaluate_series_quality(
    session: Session,
    series: EconomicSeries,
    run_uuid: str,
    weights: QualityWeights,
    now: Optional[datetime] = None,
    today_date: Optional[date] = None,
) -> SeriesQualityReport:
    """Run all quality checks for a single series and record logs and scores."""
    if now is None:
        now = get_utc_now()
    if today_date is None:
        today_date = get_utc_today()

    vintages_df, latest_df = _load_series_frames(session, series)

    # Has this series had a previous quality run?
    has_prev_run = (
        session.scalars(
            select(DataQualityScore.id).where(DataQualityScore.series_id == series.id).limit(1)
        ).first()
        is not None
    )
    is_backfill = not has_prev_run

    # Execute all checks from A9
    check_results: Dict[str, CheckResult] = {}

    check_results["missing_values"] = check_missing_values(session, series, latest_df)
    check_results["duplicate_observations"] = check_duplicate_observations(
        session, series, vintages_df
    )
    check_results["invalid_dates"] = check_invalid_dates(session, series, vintages_df, today_date)
    check_results["invalid_numeric_format"] = check_invalid_numeric_format(
        session, series, vintages_df
    )
    check_results["unexpected_nulls"] = check_unexpected_nulls(session, series, vintages_df)
    check_results["frequency_consistency"] = check_frequency_consistency(session, series, latest_df)
    check_results["gaps_in_expected_grid"] = check_gaps_in_expected_grid(
        session, series, latest_df, today_date
    )
    check_results["range_violations"] = check_range_violations(session, series, latest_df)
    check_results["staleness"] = check_staleness(session, series, latest_df, today_date)
    check_results["pandera_schema"] = check_pandera_schema(session, series, vintages_df, latest_df)

    # Compute health score
    score_result = compute_health_score(check_results, weights)

    # Persist DataQualityLog rows
    logs_to_add: List[DataQualityLog] = []
    events_emitted = 0

    for check_name, res in check_results.items():
        log_entry = DataQualityLog(
            run_uuid=run_uuid,
            series_id=series.id,
            ingestion_run_id=None,
            check_name=res.check_name,
            severity=res.severity,
            passed=res.passed,
            affected_count=res.affected_count,
            message=res.message,
            details=res.details,
            checked_at=now,
        )
        logs_to_add.append(log_entry)

        # Emit quality_issue event ONLY on passing-to-failing transitions
        # or if it's the very first run (with is_backfill=True)
        prev_passed = _get_previous_check_status(session, series.id, check_name)

        transition_to_failure = False
        if not res.passed:
            if prev_passed is None:
                # First time seeing this check, and it's failing
                transition_to_failure = True
            elif prev_passed is True:
                # Transitioned from passing to failing
                transition_to_failure = True

        if transition_to_failure:
            event = create_quality_issue_event(
                session=session,
                series=series,
                check_name=res.check_name,
                run_uuid=run_uuid,
                message=res.message,
                details=res.details,
                is_backfill=is_backfill,
                now=now,
            )
            if event is not None:
                events_emitted += 1

    session.add_all(logs_to_add)

    # Persist DataQualityScore row
    score_entry = DataQualityScore(
        run_uuid=run_uuid,
        series_id=series.id,
        score=score_result.composite_score,
        component_scores=score_result.component_scores,
        computed_at=now,
    )
    session.add(score_entry)
    session.flush()

    passed_count = sum(1 for c in check_results.values() if c.passed)
    failed_count = sum(1 for c in check_results.values() if not c.passed)

    return SeriesQualityReport(
        series_code=series.code,
        run_uuid=run_uuid,
        composite_score=score_result.composite_score,
        component_scores=score_result.component_scores,
        checks_passed=passed_count,
        checks_failed=failed_count,
        check_results=check_results,
        quality_issue_events_emitted=events_emitted,
    )


def run_data_quality_service(
    session: Session,
    series_codes: Optional[List[str]] = None,
    monitoring_config: Optional[MonitoringConfig] = None,
    now: Optional[datetime] = None,
    today_date: Optional[date] = None,
) -> QualityServiceResult:
    """Run data quality validation and scoring across active series."""
    if monitoring_config is None:
        monitoring_config = load_monitoring_config()
    if now is None:
        now = get_utc_now()
    if today_date is None:
        today_date = get_utc_today()

    run_uuid = str(uuid.uuid4())
    weights = monitoring_config.quality.weights

    query = select(EconomicSeries).where(EconomicSeries.is_active.is_(True))
    if series_codes:
        upper_codes = [c.upper() for c in series_codes]
        query = query.where(EconomicSeries.code.in_(upper_codes))

    series_list = list(session.scalars(query).all())
    series_reports: Dict[str, SeriesQualityReport] = {}
    total_checks = 0
    total_issues = 0

    for series in series_list:
        try:
            with session.begin_nested():
                rep = evaluate_series_quality(
                    session=session,
                    series=series,
                    run_uuid=run_uuid,
                    weights=weights,
                    now=now,
                    today_date=today_date,
                )
            session.commit()
            series_reports[series.code] = rep
            total_checks += rep.checks_passed + rep.checks_failed
            total_issues += rep.quality_issue_events_emitted
            logger.info(
                f"Completed quality evaluation for {series.code}: "
                f"Score={rep.composite_score}/100, Passed={rep.checks_passed}, "
                f"Failed={rep.checks_failed}."
            )
        except Exception as exc:
            session.rollback()
            logger.error(f"Failed quality evaluation for series {series.code}: {exc}")
            raise

    return QualityServiceResult(
        run_uuid=run_uuid,
        series_reports=series_reports,
        total_checks_evaluated=total_checks,
        total_issues_emitted=total_issues,
    )
