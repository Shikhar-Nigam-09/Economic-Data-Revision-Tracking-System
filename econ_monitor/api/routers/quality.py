"""API router for data quality scores, check execution logs, and manual check runs."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from econ_monitor.api.deps import get_db
from econ_monitor.db.models import DataQualityLog, DataQualityScore, EconomicSeries
from econ_monitor.quality.service import run_data_quality_service
from econ_monitor.schemas.quality import (
    QualityLogItem,
    QualityLogsResponse,
    QualityRunResponse,
    QualityScoreItem,
    QualityScoresResponse,
)

router = APIRouter(prefix="/quality", tags=["Data Quality"])


@router.get("/scores/latest", response_model=QualityScoresResponse)
def get_latest_scores(db: Session = Depends(get_db)) -> QualityScoresResponse:
    """Retrieve the most recent health score per active economic series."""
    # Subquery for max computed_at per series_id
    max_subq = (
        select(
            DataQualityScore.series_id,
            func.max(DataQualityScore.computed_at).label("max_computed_at"),
        )
        .group_by(DataQualityScore.series_id)
        .subquery()
    )

    stmt = (
        select(DataQualityScore, EconomicSeries.code.label("series_code"))
        .join(EconomicSeries, DataQualityScore.series_id == EconomicSeries.id)
        .join(
            max_subq,
            (DataQualityScore.series_id == max_subq.c.series_id)
            & (DataQualityScore.computed_at == max_subq.c.max_computed_at),
        )
        .where(EconomicSeries.is_active.is_(True))
        .order_by(EconomicSeries.code.asc())
    )

    rows = db.execute(stmt).all()
    items = [
        QualityScoreItem(
            id=s.id,
            run_uuid=s.run_uuid,
            series_id=s.series_id,
            series_code=s_code,
            score=s.score,
            component_scores=s.component_scores or {},
            computed_at=s.computed_at,
        )
        for s, s_code in rows
    ]

    return QualityScoresResponse(items=items, total=len(items))


@router.get("/scores/history", response_model=QualityScoresResponse)
def get_scores_history(
    series: Optional[str] = Query(default=None, description="Series code filter (e.g. GDP, CPI)"),
    limit: int = Query(default=100, ge=1, le=1000, description="Page limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    db: Session = Depends(get_db),
) -> QualityScoresResponse:
    """Retrieve historical health score runs for trend tracking and monitoring."""
    stmt = select(DataQualityScore, EconomicSeries.code.label("series_code")).join(
        EconomicSeries, DataQualityScore.series_id == EconomicSeries.id
    )

    if series is not None:
        stmt = stmt.where(EconomicSeries.code == series.upper())

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count = db.scalar(count_stmt) or 0

    stmt = (
        stmt.order_by(DataQualityScore.computed_at.desc(), DataQualityScore.id.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = db.execute(stmt).all()

    items = [
        QualityScoreItem(
            id=s.id,
            run_uuid=s.run_uuid,
            series_id=s.series_id,
            series_code=s_code,
            score=s.score,
            component_scores=s.component_scores or {},
            computed_at=s.computed_at,
        )
        for s, s_code in rows
    ]

    return QualityScoresResponse(items=items, total=total_count)


@router.get("/logs", response_model=QualityLogsResponse)
def get_quality_logs(
    series: Optional[str] = Query(default=None, description="Series code filter"),
    check_name: Optional[str] = Query(default=None, description="Quality check name filter"),
    severity: Optional[str] = Query(
        default=None, description="Severity filter: info, warning, error"
    ),
    passed: Optional[bool] = Query(default=None, description="Passed status filter (true/false)"),
    limit: int = Query(default=100, ge=1, le=1000, description="Page limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    db: Session = Depends(get_db),
) -> QualityLogsResponse:
    """Retrieve granular data quality check evaluation logs."""
    stmt = select(DataQualityLog, EconomicSeries.code.label("series_code")).join(
        EconomicSeries, DataQualityLog.series_id == EconomicSeries.id
    )

    if series is not None:
        stmt = stmt.where(EconomicSeries.code == series.upper())
    if check_name is not None:
        stmt = stmt.where(DataQualityLog.check_name == check_name)
    if severity is not None:
        stmt = stmt.where(DataQualityLog.severity == severity)
    if passed is not None:
        stmt = stmt.where(DataQualityLog.passed == passed)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count = db.scalar(count_stmt) or 0

    stmt = (
        stmt.order_by(DataQualityLog.checked_at.desc(), DataQualityLog.id.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = db.execute(stmt).all()

    items = [
        QualityLogItem(
            id=log.id,
            run_uuid=log.run_uuid,
            series_id=log.series_id,
            series_code=s_code,
            ingestion_run_id=log.ingestion_run_id,
            check_name=log.check_name,
            severity=log.severity,
            passed=log.passed,
            affected_count=log.affected_count,
            message=log.message,
            details=log.details,
            checked_at=log.checked_at,
        )
        for log, s_code in rows
    ]

    return QualityLogsResponse(items=items, total=total_count)


@router.post("/run", response_model=QualityRunResponse)
def trigger_quality_run(
    series: Optional[str] = Query(default=None, description="Optional series code filter"),
    db: Session = Depends(get_db),
) -> QualityRunResponse:
    """Execute data quality validation and health scoring synchronously."""
    series_codes = [series] if series else None
    res = run_data_quality_service(db, series_codes=series_codes)

    series_summary = {}
    for code, rep in res.series_reports.items():
        series_summary[code] = {
            "score": float(rep.composite_score),
            "checks_passed": rep.checks_passed,
            "checks_failed": rep.checks_failed,
            "events_emitted": rep.quality_issue_events_emitted,
        }

    return QualityRunResponse(
        run_uuid=res.run_uuid,
        total_checks_evaluated=res.total_checks_evaluated,
        total_issues_emitted=res.total_issues_emitted,
        series=series_summary,
    )
