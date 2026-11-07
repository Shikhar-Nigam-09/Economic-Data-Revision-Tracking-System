"""Z-score statistical anomaly detector for economic observation series."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from econ_monitor.anomalies.features import extract_feature_payload
from econ_monitor.core.logging import get_logger

logger = get_logger("econ_monitor.anomalies.zscore")


@dataclass
class AnomalyDetectionItem:
    """Detected candidate anomaly observation ready for persistence."""

    observation_id: int
    observation_date: date
    vintage_id: int
    vintage_date: date
    method: str
    score: Decimal
    threshold: Decimal | None
    features: Dict[str, Any]


def detect_zscore_anomalies(
    feature_df: pd.DataFrame,
    threshold: float = 3.0,
    transform: str = "pct_change",
    window: int = 12,
) -> List[AnomalyDetectionItem]:
    """Flag observations where abs(z_score) >= threshold without look-ahead bias.

    Observations with NaN z-scores (incomplete trailing window or zero std)
    are skipped.
    """
    if feature_df.empty or "z_score" not in feature_df.columns:
        return []

    flagged: List[AnomalyDetectionItem] = []

    for _, row in feature_df.iterrows():
        z = row["z_score"]
        if pd.isna(z) or np.isinf(z):
            continue

        if abs(float(z)) >= threshold:
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
                    method="zscore",
                    score=Decimal(str(round(float(z), 8))),
                    threshold=Decimal(str(threshold)),
                    features=features,
                )
            )

    return flagged
