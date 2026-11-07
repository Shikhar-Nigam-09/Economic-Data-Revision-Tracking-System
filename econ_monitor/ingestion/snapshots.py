"""Parquet snapshot management, append-only verification, and import engine."""

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, NamedTuple, Optional

import pandas as pd

from econ_monitor.core.clock import get_utc_now
from econ_monitor.core.errors import EconMonitorError
from econ_monitor.core.logging import get_logger
from econ_monitor.ingestion.normalize import VINTAGE_FRAME_COLUMNS

logger = get_logger("econ_monitor.ingestion.snapshots")

MANIFEST_SCHEMA_VERSION = "1.0"


class SnapshotAppendOnlyViolationError(EconMonitorError):
    """Raised when incoming snapshot data contradicts previously recorded vintage values."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="SNAPSHOT_APPEND_ONLY_VIOLATION", details=details)


class SnapshotVerificationError(EconMonitorError):
    """Raised when snapshot manifest verification or checksum validation fails."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="SNAPSHOT_VERIFICATION_ERROR", details=details)


class SnapshotWriteResult(NamedTuple):
    fred_series_id: str
    rows_total: int
    rows_added: int
    file_written: bool
    parquet_path: Path
    sha256: str


def compute_file_sha256(file_path: Path) -> str:
    """Compute the SHA256 checksum of a file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_series_snapshot(
    base_dir: str | Path,
    fred_series_id: str,
    df: pd.DataFrame,
    metadata: Optional[Dict[str, Any]] = None,
) -> SnapshotWriteResult:
    """Persist normalized observations into an append-only Parquet snapshot.

    - Compares new rows with existing snapshot data.
    - If a key (observation_date, vintage_date) already exists with a different value,
      fails loudly with SnapshotAppendOnlyViolationError.
    - If no new rows are introduced, skips writing the file to maintain clean Git history.
    """
    base_path = Path(base_dir)
    series_dir = base_path / fred_series_id
    series_dir.mkdir(parents=True, exist_ok=True)

    parquet_file = series_dir / "vintages.parquet"
    meta_file = series_dir / "metadata.json"

    # Normalize incoming values to exact types
    incoming_df = df.copy()
    if not incoming_df.empty:
        # Convert date columns to date objects if needed
        incoming_df["observation_date"] = incoming_df["observation_date"].apply(
            lambda x: x if isinstance(x, date) else date.fromisoformat(str(x))
        )
        incoming_df["vintage_date"] = incoming_df["vintage_date"].apply(
            lambda x: x if isinstance(x, date) else date.fromisoformat(str(x))
        )
        if "source_realtime_end" in incoming_df.columns:
            incoming_df["source_realtime_end"] = incoming_df["source_realtime_end"].apply(
                lambda x: (
                    None
                    if pd.isna(x) or x is None
                    else (x if isinstance(x, date) else date.fromisoformat(str(x)))
                )
            )

    if parquet_file.exists():
        existing_df = pd.read_parquet(parquet_file, engine="pyarrow")
        # Ensure dates are date objects for comparison
        existing_df["observation_date"] = existing_df["observation_date"].apply(
            lambda x: x if isinstance(x, date) else date.fromisoformat(str(x))
        )
        existing_df["vintage_date"] = existing_df["vintage_date"].apply(
            lambda x: x if isinstance(x, date) else date.fromisoformat(str(x))
        )

        # Build existing key -> (value, is_missing) map
        existing_map: Dict[tuple, tuple] = {}
        for row in existing_df.itertuples(index=False):
            key = (row.observation_date, row.vintage_date)
            existing_map[key] = (str(row.value) if row.value is not None else None, row.is_missing)

        # Check append-only consistency
        new_records = []
        for row in incoming_df.itertuples(index=False):
            key = (row.observation_date, row.vintage_date)
            incoming_val_str = str(row.value) if row.value is not None else None

            if key in existing_map:
                existing_val_str, existing_missing = existing_map[key]
                if existing_missing != row.is_missing or existing_val_str != incoming_val_str:
                    err_msg = (
                        f"Append-only violation for {fred_series_id} on "
                        f"({row.observation_date}, {row.vintage_date}): "
                        f"existing (val={existing_val_str}, missing={existing_missing}) != "
                        f"incoming (val={incoming_val_str}, missing={row.is_missing})"
                    )
                    raise SnapshotAppendOnlyViolationError(err_msg)
            else:
                new_records.append(row._asdict())

        if not new_records:
            # Nothing new to write; return existing stats
            sha256 = compute_file_sha256(parquet_file)
            return SnapshotWriteResult(
                fred_series_id=fred_series_id,
                rows_total=len(existing_df),
                rows_added=0,
                file_written=False,
                parquet_path=parquet_file,
                sha256=sha256,
            )

        merged_df = pd.concat([existing_df, pd.DataFrame(new_records)], ignore_index=True)
        rows_added = len(new_records)
    else:
        merged_df = incoming_df
        rows_added = len(incoming_df)

    # Sort deterministically
    if not merged_df.empty:
        merged_df.sort_values(by=["observation_date", "vintage_date"], inplace=True)
        merged_df.reset_index(drop=True, inplace=True)

    # Convert Decimal objects to string or exact pyarrow type before writing.
    # Storing value as string in Parquet ensures lossless representation.
    write_df = merged_df.copy()
    if not write_df.empty and "value" in write_df.columns:
        write_df["value"] = write_df["value"].apply(lambda v: str(v) if v is not None else None)

    write_df.to_parquet(parquet_file, engine="pyarrow", index=False)
    sha256 = compute_file_sha256(parquet_file)

    if metadata:
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, default=str)

    return SnapshotWriteResult(
        fred_series_id=fred_series_id,
        rows_total=len(merged_df),
        rows_added=rows_added,
        file_written=True,
        parquet_path=parquet_file,
        sha256=sha256,
    )


def update_manifest(
    base_dir: str | Path,
    git_commit: Optional[str] = None,
) -> Path:
    """Generate or update data/snapshots/manifest.json for all series snapshots."""
    base_path = Path(base_dir)
    manifest_file = base_path / "manifest.json"

    series_entries: Dict[str, Any] = {}
    for item in sorted(base_path.iterdir()):
        if item.is_dir():
            parquet_path = item / "vintages.parquet"
            if parquet_path.exists():
                sha256 = compute_file_sha256(parquet_path)
                df = pd.read_parquet(parquet_path, engine="pyarrow")
                if not df.empty:
                    min_obs = str(df["observation_date"].min())
                    max_obs = str(df["observation_date"].max())
                    max_vint = str(df["vintage_date"].max())
                else:
                    min_obs = None
                    max_obs = None
                    max_vint = None

                series_entries[item.name] = {
                    "row_count": len(df),
                    "min_observation_date": min_obs,
                    "max_observation_date": max_obs,
                    "max_vintage_date": max_vint,
                    "content_sha256": sha256,
                }

    manifest_data = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": get_utc_now().isoformat(),
        "git_commit": git_commit,
        "series": series_entries,
    }

    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    return manifest_file


def verify_manifest(base_dir: str | Path) -> Dict[str, Any]:
    """Verify that manifest.json exists, matches schema_version, and checksums match."""
    base_path = Path(base_dir)
    manifest_file = base_path / "manifest.json"

    if not manifest_file.exists():
        raise SnapshotVerificationError(f"Manifest not found at {manifest_file}")

    with open(manifest_file, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise SnapshotVerificationError(
            f"Unsupported manifest schema_version '{manifest.get('schema_version')}'; "
            f"expected '{MANIFEST_SCHEMA_VERSION}'"
        )

    for fred_id, entry in manifest.get("series", {}).items():
        parquet_file = base_path / fred_id / "vintages.parquet"
        if not parquet_file.exists():
            raise SnapshotVerificationError(
                f"Missing Parquet file for series '{fred_id}' declared in manifest: {parquet_file}"
            )

        actual_sha = compute_file_sha256(parquet_file)
        expected_sha = entry.get("content_sha256")
        if actual_sha != expected_sha:
            raise SnapshotVerificationError(
                f"SHA256 mismatch for {fred_id}: expected {expected_sha}, computed {actual_sha}"
            )

    return manifest


def load_series_snapshot_dataframe(base_dir: str | Path, fred_series_id: str) -> pd.DataFrame:
    """Read a series' vintages.parquet file and return a typed DataFrame with Decimals."""
    parquet_path = Path(base_dir) / fred_series_id / "vintages.parquet"
    if not parquet_path.exists():
        return pd.DataFrame(columns=VINTAGE_FRAME_COLUMNS)

    df = pd.read_parquet(parquet_path, engine="pyarrow")
    if df.empty:
        return df

    # Convert dates and values back to exact python objects
    df["observation_date"] = df["observation_date"].apply(
        lambda x: x if isinstance(x, date) else date.fromisoformat(str(x))
    )
    df["vintage_date"] = df["vintage_date"].apply(
        lambda x: x if isinstance(x, date) else date.fromisoformat(str(x))
    )
    if "source_realtime_end" in df.columns:
        df["source_realtime_end"] = df["source_realtime_end"].apply(
            lambda x: (
                None
                if pd.isna(x) or x is None
                else (x if isinstance(x, date) else date.fromisoformat(str(x)))
            )
        )

    df["value"] = df["value"].apply(lambda x: None if pd.isna(x) or x is None else Decimal(str(x)))
    return df
