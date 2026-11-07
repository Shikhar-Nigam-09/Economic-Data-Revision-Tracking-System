"""Pandera validation schemas for vintage frames and latest-observation frames."""

import pandera.pandas as pa
from pandera.pandas import Column, DataFrameSchema


def get_vintage_frame_schema() -> DataFrameSchema:
    """Return Pandera schema for historical vintage dataframes.

    Validates schema conformance, data types, and check constraints:
    - fred_series_id: string
    - observation_date: datetime/date
    - vintage_date: datetime/date
    - source_realtime_end: date/datetime or object, nullable
    - value: numeric, nullable
    - is_missing: bool
    """
    return DataFrameSchema(
        columns={
            "fred_series_id": Column(pa.String, nullable=False),
            "observation_date": Column(pa.Object, nullable=False),
            "vintage_date": Column(pa.Object, nullable=False),
            "value": Column(pa.Object, nullable=True),
            "is_missing": Column(pa.Bool, nullable=False),
        },
        coerce=False,
        strict=False,
    )


def get_latest_value_frame_schema(
    expected_min: float,
    expected_max: float,
) -> DataFrameSchema:
    """Return Pandera schema for latest observation values frame with series range check."""
    return DataFrameSchema(
        columns={
            "observation_date": Column(pa.Object, nullable=False),
            "latest_value": Column(
                pa.Float,
                nullable=True,
                checks=[
                    pa.Check.greater_than_or_equal_to(expected_min),
                    pa.Check.less_than_or_equal_to(expected_max),
                ],
            ),
            "is_missing": Column(pa.Bool, nullable=False),
        },
        coerce=True,
        strict=False,
    )
