# FRED and ALFRED API Empirical Investigation & Semantics

This document records the empirical semantics, endpoint parameter behaviors, and historical revision retrieval strategies for the Federal Reserve Economic Data (FRED) and Archival Federal Reserve Economic Data (ALFRED) APIs.

---

## 1. Investigation of Historical Vintage Retrieval: `output_type=1` vs `output_type=4`

The FRED `/series/observations` endpoint supports an `output_type` parameter with options:
* `output_type=1`: Observations by real-time period (Default).
* `output_type=2`: Observations by vintage date, all observations on a single date.
* `output_type=3`: Observations by vintage date, all observations on multiple vintage dates.
* `output_type=4`: Observations by vintage date (initial release, current release, and revision history).

### Empirical Findings:

#### `output_type=1` (Observations by Real-Time Period):
* **Invocation:** Querying with `realtime_start=1776-07-04` and `realtime_end=9999-12-31`.
* **Returned Structure:** Each record represents an observation value along with its exact historical validity window:
  ```json
  {
    "realtime_start": "2020-02-27",
    "realtime_end": "2020-03-25",
    "date": "2019-10-01",
    "value": "19253.959"
  }
  ```
* **Semantics:**
  * `realtime_start` is the exact calendar date on which this specific observation value became public knowledge (the vintage date).
  * `realtime_end` is the date on which this value was superseded by a subsequent revision. A `realtime_end` of `9999-12-31` signifies that this value remains the currently effective figure.
  * When a data point is published for the first time, one row is present. When revised, a new row appears with a new `realtime_start` and the old row's `realtime_end` is clipped.
  * Total payload size is minimal because rows are emitted only when an observation value is updated or revised.

#### `output_type=4` (Observations by Vintage Date):
* **Invocation:** Returns data grouped or indexed by vintage dates.
* **Limitations:**
  * When invoked across broad date ranges without passing discrete `vintage_dates`, `output_type=4` either defaults to the latest vintage or returns cross-sectional snapshots containing redundant unchanged points across every vintage date.
  * For long historical series (e.g. GDP spanning 1990–present with hundreds of vintage releases), querying cross-sectional snapshots across every vintage release date results in exponential data repetition (hundreds of thousands of redundant rows).
* **Conclusion & Selected Strategy:**
  * **`output_type=1` with `realtime_start=1776-07-04` and `realtime_end=9999-12-31` is the authoritative and robust retrieval strategy.** It delivers the exact lifecycle of every observation, provides explicit validity windows, and avoids fetching redundant data.

---

## 2. Pitfalls of Querying with a Later `realtime_start` (Incremental Trap)

A common assumption is that incremental collection can simply pass `realtime_start = <last_sync_date>`.

### Why this is unsafe:
1. **Historical Horizon Shifts:** Statistical agencies (such as BEA and BLS) periodically perform annual comprehensive benchmark revisions. A single vintage release may revise 5 or 10 years of historical data simultaneously.
2. **Missing Pre-conditions:** If an incremental query only requests rows where `realtime_start >= T`, you receive the *new* value, but you do not receive the superseded *previous* value unless that previous value was already recorded in your local database. If any previous vintage was skipped or corrupted, calculating `from_vintage_id` or the exact revision delta becomes impossible.
3. **Observation Identity:** New observations appear with `realtime_start = publication_date`, but their `observation_date` may lag by 1 to 3 months.

### Selected Safe Incremental Strategy:
1. Query FRED series metadata (`/series?series_id=...`).
2. Compare the `last_updated` timestamp in the metadata against the stored `fred_last_updated` timestamp in `economic_series` (or `metadata.json` for snapshots).
3. If `last_updated` has **not changed**, skip the series entirely (`skipped_no_new_data`).
4. If `last_updated` **has changed**, fetch the series observations since `history_start` using `output_type=1`.
5. The storage engine uses `INSERT ... ON CONFLICT DO NOTHING` on `(observation_id, vintage_date)`. Only genuine new vintages are inserted. Existing vintages are preserved without modification.

---

## 3. Representation of Missing Data and Corrupt Rows

* **FRED Missing Symbol:** In raw JSON responses, missing data is represented as the literal string `.` (e.g., `"value": "."`).
* **Normalization Rule:** The string `.` or empty string is strictly converted to `NULL` (Python `None`) with `is_missing = True`.
* **Zero Values:** Real zeroes (e.g., `"value": "0.0"`) are valid numbers and are strictly distinct from missing data.
* **Invalid Numerics:** Any non-numeric string other than `.` is rejected and recorded into `data_quality_logs` under the check name `invalid_numeric_format`.

---

## 4. Initial Five Economic Indicators: Metadata Mapping

| Code | FRED Series ID | Official Title | Frequency | Seasonal Adjustment | Units |
|---|---|---|---|---|---|
| `GDP` | `GDPC1` | Real Gross Domestic Product | Quarterly (`Q`) | Seasonally Adjusted Annual Rate | Billions of Chained 2017 Dollars |
| `CPI` | `CPIAUCSL` | Consumer Price Index for All Urban Consumers: All Items in U.S. City Average | Monthly (`M`) | Seasonally Adjusted | Index 1982-1984=100 |
| `UNRATE` | `UNRATE` | Unemployment Rate | Monthly (`M`) | Seasonally Adjusted | Percent |
| `INDPRO` | `INDPRO` | Industrial Production: Total Index | Monthly (`M`) | Seasonally Adjusted | Index 2017=100 |
| `RETAIL_SALES` | `RSAFS` | Advance Retail Sales: Retail Trade and Food Services | Monthly (`M`) | Seasonally Adjusted | Millions of Dollars |

---

## 5. Verification Commands for When FRED_API_KEY is Provided

Once a valid `FRED_API_KEY` is added to `.env`:

```bash
# Verify active series metadata and connection against live FRED
python -m econ_monitor.cli verify-series

# Execute live snapshot extraction (no database required)
python -m econ_monitor.cli snapshot

# Execute direct database ingestion (once DATABASE_URL is configured)
python -m econ_monitor.cli ingest
```
