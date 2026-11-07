"""Isolation Forest machine learning anomaly detector for economic observation series."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import List, Optional

import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from econ_monitor.anomalies.features import FEATURE_COLUMNS, extract_feature_payload
from econ_monitor.anomalies.zscore import AnomalyDetectionItem
from econ_monitor.core.logging import get_logger

logger = get_logger("econ_monitor.anomalies.iforest")

DEFAULT_MIN_HISTORY = {
    "M": 60,
    "Q": 40,
    "A": 30,
}


@dataclass
class IForestRunResult:
    """Outcome of an Isolation Forest evaluation on a series."""

    ran: bool
    flagged: List[AnomalyDetectionItem]
    skipped_reason: Optional[str] = None
    rows_evaluated: int = 0


def detect_iforest_anomalies(
    feature_df: pd.DataFrame,
    frequency: str,
    n_estimators: int = 200,
    contamination: float = 0.02,
    random_state: int = 42,
    min_history: Optional[int] = None,
    transform: str = "pct_change",
    window: int = 12,
) -> IForestRunResult:
    """Train and evaluate Isolation Forest on complete historical features.

    Documented limitation:
        Isolation Forest is fitted on full series history, so historical scores
        incorporate hindsight, whereas the trailing z-score uses strictly past data.
    """
    if min_history is None:
        min_history = DEFAULT_MIN_HISTORY.get(frequency.upper(), 60)

    if feature_df.empty:
        return IForestRunResult(
            ran=False,
            flagged=[],
            skipped_reason="No observation rows available.",
            rows_evaluated=0,
        )

    # Filter to rows where all required features are strictly valid and non-null
    valid_mask = feature_df[FEATURE_COLUMNS].notna().all(axis=1)
    valid_df = feature_df[valid_mask].copy()

    total_valid = len(valid_df)
    if total_valid < min_history:
        reason = (
            f"Insufficient history: {total_valid} rows < {min_history} required "
            f"for frequency {frequency}."
        )
        logger.info(f"Skipping Isolation Forest: {reason}")
        return IForestRunResult(
            ran=False,
            flagged=[],
            skipped_reason=reason,
            rows_evaluated=total_valid,
        )

    X = valid_df[FEATURE_COLUMNS].to_numpy(dtype=float)

    pipeline = Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "iforest",
                IsolationForest(
                    n_estimators=n_estimators,
                    contamination=contamination,
                    random_state=random_state,
                ),
            ),
        ]
    )

    pipeline.fit(X)
    preds = pipeline.predict(X)  # 1: inlier, -1: outlier
    scores = pipeline.decision_function(X)

    flagged: List[AnomalyDetectionItem] = []
    for idx, (_, row) in enumerate(valid_df.iterrows()):
        if preds[idx] == -1:
            features = extract_feature_payload(row, transform=transform, window=window)
            obs_id = int(row["observation_id"])
            v_id = int(row["vintage_id"])
            obs_date = row["observation_date"]
            v_date = row["vintage_date"]

            flagged.append(
                AnomalyDetectionItem(
                    observation_id=obs_id,
                    observation_date=obs_date
                    if isinstance(obs_date, date)
                    else pd.to_datetime(obs_date).date(),
                    vintage_id=v_id,
                    vintage_date=v_date
                    if isinstance(v_date, date)
                    else pd.to_datetime(v_date).date(),
                    method="isolation_forest",
                    score=Decimal(str(round(float(scores[idx]), 8))),
                    threshold=Decimal("0.0"),
                    features=features,
                )
            )

    return IForestRunResult(
        ran=True,
        flagged=flagged,
        skipped_reason=None,
        rows_evaluated=total_valid,
    )
