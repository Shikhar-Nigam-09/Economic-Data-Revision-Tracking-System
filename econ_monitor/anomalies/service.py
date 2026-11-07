"""Anomaly detection execution service orchestrating z-score and Isolation Forest models."""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from econ_monitor.anomalies.features import compute_anomaly_features
from econ_monitor.anomalies.iforest import detect_iforest_anomalies
from econ_monitor.anomalies.zscore import AnomalyDetectionItem, detect_zscore_anomalies
from econ_monitor.core.clock import get_utc_now
from econ_monitor.core.errors import SeriesNotFoundError
from econ_monitor.core.logging import get_logger
from econ_monitor.core.yaml_config import load_monitoring_config
from econ_monitor.db.models import (
    AnomalyRecord,
    AnomalyStatus,
    AppState,
    DataEvent,
    DataEventType,
    EconomicSeries,
    Observation,
    ObservationVintage,
)
from econ_monitor.revisions.events import is_series_event_baseline_set

logger = get_logger("econ_monitor.anomalies.service")


@dataclass
class SeriesAnomalyReport:
    """Anomaly detection results for a single indicator."""

    series_code: str
    zscore_ran: bool
    zscore_flagged: int
    zscore_new_records: int
    iforest_ran: bool
    iforest_flagged: int
    iforest_new_records: int
    iforest_skipped_reason: Optional[str] = None
    events_emitted: int = 0


@dataclass
class AnomalyServiceResult:
    """Overall outcome of an anomaly detection execution."""

    run_at: datetime
    series_reports: Dict[str, SeriesAnomalyReport]
    total_flagged: int
    total_new_records: int
    total_events_emitted: int


def _fetch_series_latest_df(session: Session, series: EconomicSeries) -> pd.DataFrame:
    """Retrieve the latest vintage per observation for an economic series."""
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

    stmt = (
        select(
            Observation.id.label("observation_id"),
            Observation.observation_date,
            ObservationVintage.id.label("vintage_id"),
            ObservationVintage.vintage_date,
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

    rows = session.execute(stmt).all()
    if not rows:
        return pd.DataFrame(
            columns=[
                "observation_id",
                "observation_date",
                "vintage_id",
                "vintage_date",
                "latest_value",
                "is_missing",
            ]
        )

    return pd.DataFrame(
        [r._asdict() for r in rows],
        columns=[
            "observation_id",
            "observation_date",
            "vintage_id",
            "vintage_date",
            "latest_value",
            "is_missing",
        ],
    )


def _persist_anomaly_item(
    session: Session,
    series: EconomicSeries,
    item: AnomalyDetectionItem,
    now: datetime,
) -> tuple[bool, bool]:
    """Persist an anomaly record and emit a data event idempotently.

    Returns (record_inserted, event_emitted).
    """
    record_inserted = False
    event_emitted = False

    # Check for existing anomaly record UNIQUE(vintage_id, method)
    existing_rec = session.scalars(
        select(AnomalyRecord).where(
            AnomalyRecord.vintage_id == item.vintage_id,
            AnomalyRecord.method == item.method,
        )
    ).first()

    if existing_rec is None:
        rec = AnomalyRecord(
            series_id=series.id,
            observation_id=item.observation_id,
            vintage_id=item.vintage_id,
            method=item.method,
            score=item.score,
            threshold=item.threshold,
            features=item.features,
            status=AnomalyStatus.FLAGGED,
            detected_at=now,
        )
        session.add(rec)
        session.flush()
        record_inserted = True

    obs_d_str = item.observation_date.isoformat()
    v_d_str = item.vintage_date.isoformat()
    event_key = f"anomaly:{series.code}:{obs_d_str}:{v_d_str}:{item.method}"
    existing_event = session.scalars(
        select(DataEvent).where(DataEvent.event_key == event_key)
    ).first()

    if existing_event is None:
        is_baseline_set = is_series_event_baseline_set(session, series.code)
        is_backfill = not is_baseline_set

        payload = {
            "series_code": series.code,
            "title": series.title,
            "observation_date": item.observation_date.isoformat(),
            "vintage_date": item.vintage_date.isoformat(),
            "method": item.method,
            "score": str(item.score),
            "threshold": str(item.threshold) if item.threshold is not None else None,
            "current_value": str(item.features.get("current_value")),
            "previous_value": str(item.features.get("previous_value")),
            "transformed_change": str(item.features.get("transformed_change")),
            "units": series.units,
            "change_unit_label": series.change_unit_label,
        }

        event = DataEvent(
            event_key=event_key,
            event_type=DataEventType.ANOMALY,
            series_id=series.id,
            observation_id=item.observation_id,
            vintage_date=item.vintage_date,
            payload=payload,
            is_backfill=is_backfill,
            occurred_on=item.vintage_date,
            created_at=now,
        )
        session.add(event)
        session.flush()
        event_emitted = True

    return record_inserted, event_emitted


def run_anomaly_detection(
    session: Session,
    series_code: Optional[str] = None,
    now: Optional[datetime] = None,
) -> AnomalyServiceResult:
    """Execute z-score and Isolation Forest anomaly detection across series."""
    if now is None:
        now = get_utc_now()

    monitoring_cfg = load_monitoring_config()
    anomalies_cfg = monitoring_cfg.anomalies

    # Determine targeted series
    if series_code:
        series_obj = session.scalars(
            select(EconomicSeries).where(EconomicSeries.code == series_code.upper())
        ).first()
        if not series_obj:
            raise SeriesNotFoundError(series_code)
        target_series = [series_obj]
    else:
        target_series = session.scalars(
            select(EconomicSeries).where(EconomicSeries.is_active == True)  # noqa: E712
        ).all()

    series_reports: Dict[str, SeriesAnomalyReport] = {}
    total_flagged = 0
    total_new_records = 0
    total_events_emitted = 0

    for series in target_series:
        freq = series.frequency or "M"
        transform = series.anomaly_transform or "pct_change"
        window = anomalies_cfg.windows.get(freq, 12)
        min_history = anomalies_cfg.min_history_required.get(freq, 60)

        latest_df = _fetch_series_latest_df(session, series)
        if latest_df.empty:
            logger.info(f"No observations found for {series.code}, skipping anomaly detection.")
            report = SeriesAnomalyReport(
                series_code=series.code,
                zscore_ran=False,
                zscore_flagged=0,
                zscore_new_records=0,
                iforest_ran=False,
                iforest_flagged=0,
                iforest_new_records=0,
                iforest_skipped_reason="No observation rows available.",
                events_emitted=0,
            )
            series_reports[series.code] = report
            continue

        feature_df = compute_anomaly_features(
            latest_df, frequency=freq, transform=transform, window=window
        )

        # 1. Z-score detector
        zscore_items = detect_zscore_anomalies(
            feature_df,
            threshold=anomalies_cfg.zscore.threshold,
            transform=transform,
            window=window,
        )
        zscore_new_cnt = 0
        zscore_events_cnt = 0
        for item in zscore_items:
            rec_ins, ev_ins = _persist_anomaly_item(session, series, item, now)
            if rec_ins:
                zscore_new_cnt += 1
            if ev_ins:
                zscore_events_cnt += 1

        # 2. Isolation Forest detector
        iforest_res = detect_iforest_anomalies(
            feature_df,
            frequency=freq,
            n_estimators=anomalies_cfg.isolation_forest.n_estimators,
            contamination=anomalies_cfg.isolation_forest.contamination,
            random_state=anomalies_cfg.isolation_forest.random_state,
            min_history=min_history,
            transform=transform,
            window=window,
        )
        iforest_new_cnt = 0
        iforest_events_cnt = 0
        for item in iforest_res.flagged:
            rec_ins, ev_ins = _persist_anomaly_item(session, series, item, now)
            if rec_ins:
                iforest_new_cnt += 1
            if ev_ins:
                iforest_events_cnt += 1

        series_events = zscore_events_cnt + iforest_events_cnt
        series_flagged = len(zscore_items) + len(iforest_res.flagged)
        series_new_recs = zscore_new_cnt + iforest_new_cnt

        total_flagged += series_flagged
        total_new_records += series_new_recs
        total_events_emitted += series_events

        series_reports[series.code] = SeriesAnomalyReport(
            series_code=series.code,
            zscore_ran=True,
            zscore_flagged=len(zscore_items),
            zscore_new_records=zscore_new_cnt,
            iforest_ran=iforest_res.ran,
            iforest_flagged=len(iforest_res.flagged),
            iforest_new_records=iforest_new_cnt,
            iforest_skipped_reason=iforest_res.skipped_reason,
            events_emitted=series_events,
        )

    # Save run summary to AppState under anomaly_last_run
    summary_data = {
        "run_at": now.isoformat(),
        "total_flagged": total_flagged,
        "total_new_records": total_new_records,
        "total_events_emitted": total_events_emitted,
        "series": {
            code: {
                "zscore": {
                    "ran": rep.zscore_ran,
                    "flagged": rep.zscore_flagged,
                    "new_records": rep.zscore_new_records,
                },
                "isolation_forest": {
                    "ran": rep.iforest_ran,
                    "flagged": rep.iforest_flagged,
                    "new_records": rep.iforest_new_records,
                    "skipped_reason": rep.iforest_skipped_reason,
                },
                "events_emitted": rep.events_emitted,
            }
            for code, rep in series_reports.items()
        },
    }

    state_obj = session.get(AppState, "anomaly_last_run")
    if state_obj is None:
        state_obj = AppState(key="anomaly_last_run", value=summary_data, updated_at=now)
        session.add(state_obj)
    else:
        state_obj.value = summary_data
        state_obj.updated_at = now

    session.flush()

    return AnomalyServiceResult(
        run_at=now,
        series_reports=series_reports,
        total_flagged=total_flagged,
        total_new_records=total_new_records,
        total_events_emitted=total_events_emitted,
    )
