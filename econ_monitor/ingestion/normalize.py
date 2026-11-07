"""Data normalization and sanitization for FRED/ALFRED observations."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, NamedTuple, Optional

import pandas as pd

from econ_monitor.core.clock import get_utc_now
from econ_monitor.core.logging import get_logger

logger = get_logger("econ_monitor.ingestion.normalize")

VINTAGE_FRAME_COLUMNS = [
    "fred_series_id",
    "observation_date",
    "vintage_date",
    "source_realtime_end",
    "value",
    "is_missing",
    "source",
    "retrieved_at",
]


class RejectedRow(NamedTuple):
    """Container for records rejected during normalization."""

    raw_data: Dict[str, Any]
    check_name: str  # 'invalid_dates' or 'invalid_numeric_format'
    reason: str


class NormalizationResult(NamedTuple):
    """Result of normalizing raw observation records."""

    df: pd.DataFrame
    rejected: List[RejectedRow]


def parse_date(date_str: Any) -> Optional[date]:
    """Parse date string in ISO format YYYY-MM-DD."""
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        return date.fromisoformat(date_str.strip())
    except (ValueError, TypeError):
        return None


def normalize_observations(
    raw_observations: List[Dict[str, Any]],
    fred_series_id: str,
    history_start: date,
    source: str = "ALFRED",
    retrieved_at: Optional[datetime] = None,
) -> NormalizationResult:
    """Transform raw FRED/ALFRED JSON rows into a standardized vintage DataFrame.

    Rules:
    - Parse dates (YYYY-MM-DD). If invalid, record in rejected list with reason.
    - Parse values strictly with Decimal. Missing markers ('.') become NULL with is_missing=True.
    - Filter rows where observation_date < history_start.
    - Collapse consecutive identical values for the same observation_date.
    - Deterministically sort by observation_date ASC, then vintage_date ASC.
    """
    timestamp = retrieved_at or get_utc_now()
    rejected: List[RejectedRow] = []
    valid_rows: List[Dict[str, Any]] = []

    for raw in raw_observations:
        # 1. Parse observation_date (from 'date')
        obs_date_str = raw.get("date")
        obs_date = parse_date(obs_date_str)
        if obs_date is None:
            rejected.append(
                RejectedRow(
                    raw_data=raw,
                    check_name="invalid_dates",
                    reason=f"Unparseable or missing observation date: '{obs_date_str}'",
                )
            )
            continue

        # Filter against history_start
        if obs_date < history_start:
            continue

        # 2. Parse vintage_date (from 'realtime_start')
        vintage_date_str = raw.get("realtime_start")
        vintage_date = parse_date(vintage_date_str)
        if vintage_date is None:
            rejected.append(
                RejectedRow(
                    raw_data=raw,
                    check_name="invalid_dates",
                    reason=f"Unparseable or missing vintage date: '{vintage_date_str}'",
                )
            )
            continue

        # Validate that vintage date is not before observation date
        if vintage_date < obs_date:
            rejected.append(
                RejectedRow(
                    raw_data=raw,
                    check_name="invalid_dates",
                    reason=(
                        f"Vintage date ({vintage_date}) is dated before its "
                        f"observation date ({obs_date})"
                    ),
                )
            )
            continue

        # 3. Parse source_realtime_end (optional)
        end_date_str = raw.get("realtime_end")
        end_date = parse_date(end_date_str) if end_date_str else None

        # 4. Parse value
        val_raw = raw.get("value")
        if (
            val_raw is None
            or val_raw == "."
            or str(val_raw).strip() == ""
            or str(val_raw).strip() == "."
        ):
            val_dec = None
            is_missing = True
        else:
            try:
                val_dec = Decimal(str(val_raw).strip())
                is_missing = False
            except (InvalidOperation, ValueError, TypeError) as exc:
                rejected.append(
                    RejectedRow(
                        raw_data=raw,
                        check_name="invalid_numeric_format",
                        reason=f"Failed to parse numeric decimal from '{val_raw}': {exc}",
                    )
                )
                continue

        valid_rows.append(
            {
                "fred_series_id": fred_series_id,
                "observation_date": obs_date,
                "vintage_date": vintage_date,
                "source_realtime_end": end_date,
                "value": val_dec,
                "is_missing": is_missing,
                "source": source,
                "retrieved_at": timestamp,
            }
        )

    if not valid_rows:
        empty_df = pd.DataFrame(columns=VINTAGE_FRAME_COLUMNS)
        return NormalizationResult(df=empty_df, rejected=rejected)

    # 5. Group by observation_date and collapse consecutive identical values
    df_temp = pd.DataFrame(valid_rows)
    # Sort first to ensure chronological order per observation
    df_temp.sort_values(by=["observation_date", "vintage_date"], inplace=True)

    collapsed_rows: List[Dict[str, Any]] = []
    for _obs_date, group in df_temp.groupby("observation_date", sort=False):
        last_val: Optional[Decimal] = object()  # type: ignore # sentinel
        last_missing: Optional[bool] = None

        for row in group.to_dict("records"):
            cur_val = row["value"]
            cur_missing = row["is_missing"]

            # If value and missingness are identical to previous vintage, skip
            if cur_missing == last_missing and cur_val == last_val:
                continue

            collapsed_rows.append(row)
            last_val = cur_val
            last_missing = cur_missing

    final_df = pd.DataFrame(collapsed_rows, columns=VINTAGE_FRAME_COLUMNS)
    final_df.sort_values(by=["observation_date", "vintage_date"], inplace=True)
    final_df.reset_index(drop=True, inplace=True)

    return NormalizationResult(df=final_df, rejected=rejected)
