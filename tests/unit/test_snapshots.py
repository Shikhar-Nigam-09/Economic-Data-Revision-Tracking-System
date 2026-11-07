"""Unit tests for Parquet snapshots, append-only verification, and manifest integrity."""

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from econ_monitor.ingestion.normalize import VINTAGE_FRAME_COLUMNS
from econ_monitor.ingestion.snapshots import (
    SnapshotAppendOnlyViolationError,
    SnapshotVerificationError,
    load_series_snapshot_dataframe,
    update_manifest,
    verify_manifest,
    write_series_snapshot,
)


@pytest.fixture
def synthetic_dataframe() -> pd.DataFrame:
    """Fixture providing synthetic normalized vintage records."""
    data = [
        {
            "fred_series_id": "GDPC1",
            "observation_date": date(2020, 1, 1),
            "vintage_date": date(2020, 2, 1),
            "source_realtime_end": date(2020, 3, 1),
            "value": Decimal("19000.5"),
            "is_missing": False,
            "source": "ALFRED",
            "retrieved_at": datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc),
        },
        {
            "fred_series_id": "GDPC1",
            "observation_date": date(2020, 1, 1),
            "vintage_date": date(2020, 3, 1),
            "source_realtime_end": date(9999, 12, 31),
            "value": Decimal("19050.2"),
            "is_missing": False,
            "source": "ALFRED",
            "retrieved_at": datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc),
        },
    ]
    return pd.DataFrame(data, columns=VINTAGE_FRAME_COLUMNS)


def test_snapshot_write_and_load(tmp_path: Path, synthetic_dataframe: pd.DataFrame):
    """Verify writing Parquet snapshot and reloading with exact Decimal values."""
    res = write_series_snapshot(
        base_dir=tmp_path,
        fred_series_id="GDPC1",
        df=synthetic_dataframe,
        metadata={"title": "Synthetic GDP", "last_updated": "2024-01-01"},
    )
    assert res.rows_total == 2
    assert res.rows_added == 2
    assert res.file_written is True
    assert res.parquet_path.exists()

    # Read back
    df_loaded = load_series_snapshot_dataframe(tmp_path, "GDPC1")
    assert len(df_loaded) == 2
    assert df_loaded.iloc[0]["value"] == Decimal("19000.5")
    assert df_loaded.iloc[1]["value"] == Decimal("19050.2")


def test_snapshot_append_only_violation(tmp_path: Path, synthetic_dataframe: pd.DataFrame):
    """Verify that writing a different value for an existing vintage key raises error."""
    write_series_snapshot(tmp_path, "GDPC1", synthetic_dataframe)

    # Attempt to write conflicting value for (2020-01-01, 2020-02-01)
    conflicting_df = pd.DataFrame(
        [
            {
                "fred_series_id": "GDPC1",
                "observation_date": date(2020, 1, 1),
                "vintage_date": date(2020, 2, 1),
                "source_realtime_end": date(2020, 3, 1),
                "value": Decimal("99999.9"),  # Different from 19000.5!
                "is_missing": False,
                "source": "ALFRED",
                "retrieved_at": datetime.now(timezone.utc),
            }
        ],
        columns=VINTAGE_FRAME_COLUMNS,
    )

    with pytest.raises(SnapshotAppendOnlyViolationError, match="Append-only violation"):
        write_series_snapshot(tmp_path, "GDPC1", conflicting_df)


def test_snapshot_no_rewrite_when_unchanged(tmp_path: Path, synthetic_dataframe: pd.DataFrame):
    """Verify that re-submitting identical data does not rewrite the file."""
    res1 = write_series_snapshot(tmp_path, "GDPC1", synthetic_dataframe)
    assert res1.file_written is True

    res2 = write_series_snapshot(tmp_path, "GDPC1", synthetic_dataframe)
    assert res2.file_written is False
    assert res2.rows_added == 0


def test_manifest_generation_and_checksum_verification(
    tmp_path: Path, synthetic_dataframe: pd.DataFrame
):
    """Verify manifest creation and checksum verification."""
    write_series_snapshot(tmp_path, "GDPC1", synthetic_dataframe)
    manifest_file = update_manifest(tmp_path, git_commit="abc1234")
    assert manifest_file.exists()

    manifest = verify_manifest(tmp_path)
    assert manifest["schema_version"] == "1.0"
    assert manifest["git_commit"] == "abc1234"
    assert "GDPC1" in manifest["series"]
    assert manifest["series"]["GDPC1"]["row_count"] == 2

    # Tamper with file to verify checksum failure
    parquet_file = tmp_path / "GDPC1" / "vintages.parquet"
    with open(parquet_file, "ab") as f:
        f.write(b"corrupt_extra_bytes")

    with pytest.raises(SnapshotVerificationError, match="SHA256 mismatch"):
        verify_manifest(tmp_path)
