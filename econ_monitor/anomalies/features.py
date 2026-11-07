"""Feature engineering for anomaly detection on economic observation time series."""

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from econ_monitor.core.logging import get_logger

logger = get_logger("econ_monitor.anomalies.features")

DEFAULT_WINDOWS = {
    "M": 12,
    "Q": 8,
    "A": 5,
}

FEATURE_COLUMNS = [
    "current_value",
    "previous_value",
    "transformed_change",
    "rolling_mean",
    "rolling_std",
    "z_score",
]


def compute_anomaly_features(
    latest_df: pd.DataFrame,
    frequency: str,
    transform: str = "pct_change",
    window: Optional[int] = None,
) -> pd.DataFrame:
    """Compute rolling trailing features and z-score without look-ahead bias.

    Transforms:
        - 'pct_change': ((x_t / x_{t-1}) - 1) * 100 for level and index series
        - 'diff': x_t - x_{t-1} for rate series

    Trailing window:
        Strictly excludes the current point t by shifting changes by 1 before
        computing rolling mean and rolling standard deviation over the window.
    """
    if window is None:
        window = DEFAULT_WINDOWS.get(frequency.upper(), 12)

    if latest_df.empty:
        df = latest_df.copy()
        for col in FEATURE_COLUMNS:
            df[col] = pd.Series(dtype="float64")
        return df

    # Work on a copy and sort chronologically by observation_date
    df = latest_df.copy()
    if "observation_date" in df.columns:
        df = df.sort_values("observation_date", ascending=True).reset_index(drop=True)

    # Resolve latest value column name
    val_col = "latest_value" if "latest_value" in df.columns else "value"
    values = pd.to_numeric(df[val_col], errors="coerce")

    # If is_missing flag is present, treat missing observations as NaN
    if "is_missing" in df.columns:
        values = values.mask(df["is_missing"].fillna(False).astype(bool))

    df["current_value"] = values
    df["previous_value"] = values.shift(1)

    # Compute transformed change
    if transform == "pct_change":
        # Avoid division by zero: if previous_value == 0, result is NaN
        with np.errstate(divide="ignore", invalid="ignore"):
            prev = df["previous_value"].to_numpy(dtype=float)
            curr = df["current_value"].to_numpy(dtype=float)
            pct = np.where(
                (prev == 0) | np.isnan(prev) | np.isnan(curr), np.nan, (curr / prev - 1.0) * 100.0
            )
            df["transformed_change"] = pct
    elif transform == "diff":
        df["transformed_change"] = df["current_value"] - df["previous_value"]
    else:
        raise ValueError(
            f"Unsupported anomaly transform '{transform}'. Must be 'pct_change' or 'diff'."
        )

    # Trailing rolling mean and std: shift(1) strictly excludes current point t
    trailing_change = df["transformed_change"].shift(1)
    df["rolling_mean"] = trailing_change.rolling(window=window, min_periods=window).mean()
    df["rolling_std"] = trailing_change.rolling(window=window, min_periods=window).std(ddof=1)

    # Compute z-score: (change_t - rolling_mean) / rolling_std
    # Skip / NaN when rolling_std is NaN or 0.0
    valid_std_mask = (df["rolling_std"].notna()) & (df["rolling_std"] > 0)
    df["z_score"] = np.nan
    df.loc[valid_std_mask, "z_score"] = (
        df.loc[valid_std_mask, "transformed_change"] - df.loc[valid_std_mask, "rolling_mean"]
    ) / df.loc[valid_std_mask, "rolling_std"]

    return df


def extract_feature_payload(
    row: pd.Series | Dict[str, Any],
    transform: str,
    window: int,
) -> Dict[str, Any]:
    """Convert a feature row into a clean, JSON-serializable dictionary."""

    def _clean_val(v: Any) -> Optional[float]:
        if v is None or pd.isna(v):
            return None
        return round(float(v), 6)

    return {
        "current_value": _clean_val(row["current_value"]),
        "previous_value": _clean_val(row["previous_value"]),
        "transformed_change": _clean_val(row["transformed_change"]),
        "rolling_mean": _clean_val(row["rolling_mean"]),
        "rolling_std": _clean_val(row["rolling_std"]),
        "z_score": _clean_val(row["z_score"]),
        "transform": transform,
        "window": window,
    }
