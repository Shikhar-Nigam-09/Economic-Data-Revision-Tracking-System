"""Revision analytics and aggregations honoring true initial release filtering and unit labels."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from econ_monitor.core.errors import SeriesNotFoundError
from econ_monitor.db.models import (
    EconomicSeries,
    Observation,
    ObservationVintage,
    RevisionDirection,
    RevisionHistory,
    RevisionType,
)


@dataclass
class SeriesRevisionSummary:
    """Per-series revision summary statistics."""

    series_code: str
    series_title: str
    change_unit_label: str
    true_initial_release_only: bool
    total_observations: int
    observations_revised: int
    total_revisions: int
    upward_count: int
    downward_count: int
    became_available_count: int
    became_missing_count: int
    mean_absolute_revision: Optional[Decimal]
    median_absolute_revision: Optional[Decimal]
    mean_relative_revision_pct: Optional[Decimal]
    median_relative_revision_pct: Optional[Decimal]
    largest_absolute_revision: Optional[Decimal]
    largest_relative_revision_pct: Optional[Decimal]
    average_revisions_per_observation: float


@dataclass
class DirectionCounts:
    """Count of revisions categorized by direction and type."""

    series_code: str
    upward: int
    downward: int
    became_available: int
    became_missing: int
    total_revisions: int


@dataclass
class HistogramBin:
    bin_start: float
    bin_end: float
    count: int


@dataclass
class MagnitudeDistribution:
    """Distribution of absolute revision magnitudes in histogram bins."""

    series_code: str
    unit_label: str
    bins: List[HistogramBin]
    total_numeric_revisions: int


@dataclass
class PeriodRevisionMetric:
    period: str  # e.g. "2024" or "2024-Q1"
    mean_relative_revision_pct: Optional[Decimal]
    mean_absolute_revision: Optional[Decimal]
    revision_count: int


@dataclass
class LagBucketMetric:
    bucket_label: str  # e.g. "0-30", "31-90", etc.
    revision_count: int
    percentage_of_total: float


@dataclass
class RevisionsPerObservationFrequency:
    revision_count: int  # 0, 1, 2, ...
    observation_count: int
    percentage_of_total: float


@dataclass
class FirstVsLatestComparison:
    """Comparison of initial published vintage versus latest known vintage."""

    series_code: str
    unit_label: str
    true_initial_release_only: bool
    observations_compared: int
    observations_revised: int
    share_revised_upward_pct: float
    share_revised_downward_pct: float
    share_unchanged_pct: float
    mean_signed_absolute_revision: Optional[Decimal]
    mean_signed_relative_revision_pct: Optional[Decimal]
    median_signed_relative_revision_pct: Optional[Decimal]


def _get_true_initial_release_observation_ids(
    session: Session, series: EconomicSeries
) -> List[int]:
    """Retrieve observation IDs that qualify as having a true initial release per A6.6.

    first_vintage_date - observation_date <= initial_release_max_lag_days
    """
    subq = (
        select(
            ObservationVintage.observation_id,
            func.min(ObservationVintage.vintage_date).label("first_v_date"),
        )
        .join(Observation, ObservationVintage.observation_id == Observation.id)
        .where(Observation.series_id == series.id)
        .group_by(ObservationVintage.observation_id)
        .subquery()
    )

    stmt = (
        select(Observation.id, Observation.observation_date, subq.c.first_v_date)
        .join(subq, Observation.id == subq.c.observation_id)
        .where(Observation.series_id == series.id)
    )

    rows = session.execute(stmt).all()
    valid_ids: List[int] = []
    max_lag = series.initial_release_max_lag_days

    for obs_id, obs_date, first_v_date in rows:
        if (first_v_date - obs_date).days <= max_lag:
            valid_ids.append(obs_id)

    return valid_ids


def get_series_revision_summary(
    session: Session,
    series_code: str,
    true_initial_release_only: bool = True,
) -> SeriesRevisionSummary:
    """Compute comprehensive per-series revision summary statistics."""
    series = session.scalars(
        select(EconomicSeries).where(EconomicSeries.code == series_code.upper())
    ).first()
    if not series:
        raise SeriesNotFoundError(series_code)

    unit_label = series.change_unit_label or series.units

    obs_query = select(Observation.id).where(Observation.series_id == series.id)
    if true_initial_release_only:
        valid_obs_ids = _get_true_initial_release_observation_ids(session, series)
        obs_query = obs_query.where(Observation.id.in_(valid_obs_ids))

    obs_ids = list(session.scalars(obs_query).all())
    total_obs = len(obs_ids)

    if total_obs == 0:
        return SeriesRevisionSummary(
            series_code=series.code,
            series_title=series.title,
            change_unit_label=unit_label,
            true_initial_release_only=true_initial_release_only,
            total_observations=0,
            observations_revised=0,
            total_revisions=0,
            upward_count=0,
            downward_count=0,
            became_available_count=0,
            became_missing_count=0,
            mean_absolute_revision=None,
            median_absolute_revision=None,
            mean_relative_revision_pct=None,
            median_relative_revision_pct=None,
            largest_absolute_revision=None,
            largest_relative_revision_pct=None,
            average_revisions_per_observation=0.0,
        )

    rev_query = select(RevisionHistory).where(
        RevisionHistory.series_id == series.id,
        RevisionHistory.observation_id.in_(obs_ids),
    )
    revisions = list(session.scalars(rev_query).all())

    revised_obs_ids = {r.observation_id for r in revisions}
    obs_revised_count = len(revised_obs_ids)
    total_revs = len(revisions)

    up_count = sum(1 for r in revisions if r.direction == RevisionDirection.UP)
    down_count = sum(1 for r in revisions if r.direction == RevisionDirection.DOWN)
    became_avail = sum(1 for r in revisions if r.revision_type == RevisionType.BECAME_AVAILABLE)
    became_miss = sum(1 for r in revisions if r.revision_type == RevisionType.BECAME_MISSING)

    abs_values = [abs(r.absolute_revision) for r in revisions if r.absolute_revision is not None]
    rel_values = [r.relative_revision_pct for r in revisions if r.relative_revision_pct is not None]

    mean_abs = Decimal(str(round(float(np.mean(abs_values)), 4))) if abs_values else None
    median_abs = Decimal(str(round(float(np.median(abs_values)), 4))) if abs_values else None
    largest_abs = max(abs_values) if abs_values else None

    # Exclude undefined relative revisions from averages; never substitute zero
    mean_rel = Decimal(str(round(float(np.mean(rel_values)), 4))) if rel_values else None
    median_rel = Decimal(str(round(float(np.median(rel_values)), 4))) if rel_values else None
    largest_rel = max(rel_values, key=lambda v: abs(v)) if rel_values else None

    avg_revs_per_obs = float(total_revs / total_obs) if total_obs > 0 else 0.0

    return SeriesRevisionSummary(
        series_code=series.code,
        series_title=series.title,
        change_unit_label=unit_label,
        true_initial_release_only=true_initial_release_only,
        total_observations=total_obs,
        observations_revised=obs_revised_count,
        total_revisions=total_revs,
        upward_count=up_count,
        downward_count=down_count,
        became_available_count=became_avail,
        became_missing_count=became_miss,
        mean_absolute_revision=mean_abs,
        median_absolute_revision=median_abs,
        mean_relative_revision_pct=mean_rel,
        median_relative_revision_pct=median_rel,
        largest_absolute_revision=largest_abs,
        largest_relative_revision_pct=largest_rel,
        average_revisions_per_observation=round(avg_revs_per_obs, 2),
    )


def get_direction_counts(
    session: Session,
    series_code: str,
    true_initial_release_only: bool = True,
) -> DirectionCounts:
    """Retrieve revision counts grouped by direction and type."""
    summary = get_series_revision_summary(session, series_code, true_initial_release_only)
    return DirectionCounts(
        series_code=summary.series_code,
        upward=summary.upward_count,
        downward=summary.downward_count,
        became_available=summary.became_available_count,
        became_missing=summary.became_missing_count,
        total_revisions=summary.total_revisions,
    )


def get_magnitude_distribution(
    session: Session,
    series_code: str,
    num_bins: int = 10,
    true_initial_release_only: bool = True,
) -> MagnitudeDistribution:
    """Build histogram bins for absolute revision magnitudes."""
    series = session.scalars(
        select(EconomicSeries).where(EconomicSeries.code == series_code.upper())
    ).first()
    if not series:
        raise SeriesNotFoundError(series_code)

    obs_query = select(Observation.id).where(Observation.series_id == series.id)
    if true_initial_release_only:
        valid_ids = _get_true_initial_release_observation_ids(session, series)
        obs_query = obs_query.where(Observation.id.in_(valid_ids))

    obs_ids = list(session.scalars(obs_query).all())

    rev_query = select(RevisionHistory.absolute_revision).where(
        RevisionHistory.series_id == series.id,
        RevisionHistory.observation_id.in_(obs_ids),
        RevisionHistory.absolute_revision.is_not(None),
    )
    raw_vals = [float(abs(v)) for v in session.scalars(rev_query).all()]

    if not raw_vals:
        return MagnitudeDistribution(
            series_code=series.code,
            unit_label=series.change_unit_label or series.units,
            bins=[],
            total_numeric_revisions=0,
        )

    counts, bin_edges = np.histogram(raw_vals, bins=num_bins)
    bins = [
        HistogramBin(
            bin_start=round(float(bin_edges[i]), 4),
            bin_end=round(float(bin_edges[i + 1]), 4),
            count=int(counts[i]),
        )
        for i in range(len(counts))
    ]

    return MagnitudeDistribution(
        series_code=series.code,
        unit_label=series.change_unit_label or series.units,
        bins=bins,
        total_numeric_revisions=len(raw_vals),
    )


def get_revisions_by_period(
    session: Session,
    series_code: str,
    true_initial_release_only: bool = True,
) -> List[PeriodRevisionMetric]:
    """Revisions by observation year or period for heatmaps (relative percent for comparability)."""
    series = session.scalars(
        select(EconomicSeries).where(EconomicSeries.code == series_code.upper())
    ).first()
    if not series:
        raise SeriesNotFoundError(series_code)

    obs_query = select(Observation.id).where(Observation.series_id == series.id)
    if true_initial_release_only:
        valid_ids = _get_true_initial_release_observation_ids(session, series)
        obs_query = obs_query.where(Observation.id.in_(valid_ids))

    obs_ids = list(session.scalars(obs_query).all())

    rev_query = select(RevisionHistory).where(
        RevisionHistory.series_id == series.id,
        RevisionHistory.observation_id.in_(obs_ids),
    )
    revisions = list(session.scalars(rev_query).all())

    # Group by observation year
    by_period: Dict[str, List[RevisionHistory]] = {}
    for r in revisions:
        year_str = str(r.observation_date.year)
        if year_str not in by_period:
            by_period[year_str] = []
        by_period[year_str].append(r)

    results: List[PeriodRevisionMetric] = []
    for period in sorted(by_period.keys()):
        group = by_period[period]
        abs_vals = [abs(r.absolute_revision) for r in group if r.absolute_revision is not None]
        rel_vals = [r.relative_revision_pct for r in group if r.relative_revision_pct is not None]

        mean_abs = Decimal(str(round(float(np.mean(abs_vals)), 4))) if abs_vals else None
        mean_rel = Decimal(str(round(float(np.mean(rel_vals)), 4))) if rel_vals else None

        results.append(
            PeriodRevisionMetric(
                period=period,
                mean_relative_revision_pct=mean_rel,
                mean_absolute_revision=mean_abs,
                revision_count=len(group),
            )
        )

    return results


def get_revisions_by_lag_bucket(
    session: Session,
    series_code: str,
    true_initial_release_only: bool = True,
) -> List[LagBucketMetric]:
    """Revisions categorized into lag buckets: 0-30, 31-90, 91-180, 181-365, >365 days."""
    series = session.scalars(
        select(EconomicSeries).where(EconomicSeries.code == series_code.upper())
    ).first()
    if not series:
        raise SeriesNotFoundError(series_code)

    obs_query = select(Observation.id).where(Observation.series_id == series.id)
    if true_initial_release_only:
        valid_ids = _get_true_initial_release_observation_ids(session, series)
        obs_query = obs_query.where(Observation.id.in_(valid_ids))

    obs_ids = list(session.scalars(obs_query).all())

    rev_query = select(RevisionHistory.revision_lag_days).where(
        RevisionHistory.series_id == series.id,
        RevisionHistory.observation_id.in_(obs_ids),
    )
    lags = list(session.scalars(rev_query).all())
    total_revs = len(lags)

    buckets = {
        "0-30 days": 0,
        "31-90 days": 0,
        "91-180 days": 0,
        "181-365 days": 0,
        "over 365 days": 0,
    }

    for lag in lags:
        if lag <= 30:
            buckets["0-30 days"] += 1
        elif lag <= 90:
            buckets["31-90 days"] += 1
        elif lag <= 180:
            buckets["91-180 days"] += 1
        elif lag <= 365:
            buckets["181-365 days"] += 1
        else:
            buckets["over 365 days"] += 1

    return [
        LagBucketMetric(
            bucket_label=label,
            revision_count=count,
            percentage_of_total=round((count / total_revs * 100.0), 2) if total_revs > 0 else 0.0,
        )
        for label, count in buckets.items()
    ]


def get_revisions_per_observation_frequency(
    session: Session,
    series_code: str,
    true_initial_release_only: bool = True,
) -> List[RevisionsPerObservationFrequency]:
    """Frequency table showing how many observations had 0, 1, 2, ... revisions."""
    series = session.scalars(
        select(EconomicSeries).where(EconomicSeries.code == series_code.upper())
    ).first()
    if not series:
        raise SeriesNotFoundError(series_code)

    obs_query = select(Observation.id).where(Observation.series_id == series.id)
    if true_initial_release_only:
        valid_ids = _get_true_initial_release_observation_ids(session, series)
        obs_query = obs_query.where(Observation.id.in_(valid_ids))

    all_obs_ids = list(session.scalars(obs_query).all())
    total_obs = len(all_obs_ids)

    if total_obs == 0:
        return []

    # Count revisions per observation
    rev_counts_stmt = (
        select(RevisionHistory.observation_id, func.count(RevisionHistory.id).label("rev_cnt"))
        .where(
            RevisionHistory.series_id == series.id,
            RevisionHistory.observation_id.in_(all_obs_ids),
        )
        .group_by(RevisionHistory.observation_id)
    )
    rev_counts_dict = dict(session.execute(rev_counts_stmt).all())

    # Build frequency distribution
    freq_map: Dict[int, int] = {}
    for obs_id in all_obs_ids:
        cnt = rev_counts_dict.get(obs_id, 0)
        freq_map[cnt] = freq_map.get(cnt, 0) + 1

    return [
        RevisionsPerObservationFrequency(
            revision_count=k,
            observation_count=v,
            percentage_of_total=round((v / total_obs * 100.0), 2),
        )
        for k, v in sorted(freq_map.items())
    ]


def get_first_vs_latest_comparison(
    session: Session,
    series_code: str,
    true_initial_release_only: bool = True,
) -> FirstVsLatestComparison:
    """Compare initial published vintage against the latest known vintage, evaluating bias."""
    series = session.scalars(
        select(EconomicSeries).where(EconomicSeries.code == series_code.upper())
    ).first()
    if not series:
        raise SeriesNotFoundError(series_code)

    unit_label = series.change_unit_label or series.units

    obs_query = select(Observation.id).where(Observation.series_id == series.id)
    if true_initial_release_only:
        valid_ids = _get_true_initial_release_observation_ids(session, series)
        obs_query = obs_query.where(Observation.id.in_(valid_ids))

    target_obs_ids = list(session.scalars(obs_query).all())

    if not target_obs_ids:
        return FirstVsLatestComparison(
            series_code=series.code,
            unit_label=unit_label,
            true_initial_release_only=true_initial_release_only,
            observations_compared=0,
            observations_revised=0,
            share_revised_upward_pct=0.0,
            share_revised_downward_pct=0.0,
            share_unchanged_pct=0.0,
            mean_signed_absolute_revision=None,
            mean_signed_relative_revision_pct=None,
            median_signed_relative_revision_pct=None,
        )

    # For each observation, get first vintage and latest vintage
    vintages_stmt = (
        select(
            ObservationVintage.observation_id,
            ObservationVintage.vintage_date,
            ObservationVintage.value,
            ObservationVintage.is_missing,
        )
        .where(ObservationVintage.observation_id.in_(target_obs_ids))
        .order_by(ObservationVintage.observation_id, ObservationVintage.vintage_date.asc())
    )
    vintages = session.execute(vintages_stmt).all()

    by_obs: Dict[int, list] = {}
    for v in vintages:
        if v.observation_id not in by_obs:
            by_obs[v.observation_id] = []
        by_obs[v.observation_id].append(v)

    compared_count = 0
    revised_count = 0
    up_count = 0
    down_count = 0
    unchanged_count = 0

    signed_abs_revisions: List[Decimal] = []
    signed_rel_revisions: List[Decimal] = []

    for obs_id, v_list in by_obs.items():
        if not v_list:
            continue
        first_v = v_list[0]
        latest_v = v_list[-1]

        # Only compare if both are non-missing values
        if first_v.is_missing or latest_v.is_missing:
            continue
        if first_v.value is None or latest_v.value is None:
            continue

        compared_count += 1
        first_val = Decimal(str(first_v.value))
        latest_val = Decimal(str(latest_v.value))

        if first_val == latest_val:
            unchanged_count += 1
        else:
            revised_count += 1
            diff = latest_val - first_val
            signed_abs_revisions.append(diff)

            if first_val != Decimal("0"):
                rel_diff = (diff / abs(first_val)) * Decimal("100")
                signed_rel_revisions.append(rel_diff)

            if latest_val > first_val:
                up_count += 1
            elif latest_val < first_val:
                down_count += 1

    share_up = round((up_count / compared_count * 100.0), 2) if compared_count > 0 else 0.0
    share_down = round((down_count / compared_count * 100.0), 2) if compared_count > 0 else 0.0
    share_unchanged = (
        round((unchanged_count / compared_count * 100.0), 2) if compared_count > 0 else 0.0
    )

    mean_signed_abs = (
        Decimal(str(round(float(np.mean(signed_abs_revisions)), 4)))
        if signed_abs_revisions
        else None
    )
    mean_signed_rel = (
        Decimal(str(round(float(np.mean(signed_rel_revisions)), 4)))
        if signed_rel_revisions
        else None
    )
    median_signed_rel = (
        Decimal(str(round(float(np.median(signed_rel_revisions)), 4)))
        if signed_rel_revisions
        else None
    )

    return FirstVsLatestComparison(
        series_code=series.code,
        unit_label=unit_label,
        true_initial_release_only=true_initial_release_only,
        observations_compared=compared_count,
        observations_revised=revised_count,
        share_revised_upward_pct=share_up,
        share_revised_downward_pct=share_down,
        share_unchanged_pct=share_unchanged,
        mean_signed_absolute_revision=mean_signed_abs,
        mean_signed_relative_revision_pct=mean_signed_rel,
        median_signed_relative_revision_pct=median_signed_rel,
    )
