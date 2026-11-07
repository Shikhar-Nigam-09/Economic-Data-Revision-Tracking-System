# Project State: Economic Data Monitoring and Revision Tracking System

## Phase Log

| Phase | Description | Status | Completion Date |
|---|---|---|---|
| **Phase 1** | Project Foundation, Settings, SQLAlchemy Models, Alembic Migrations, and FastAPI Skeleton | **COMPLETED** | 2026-11-06 |
| **Phase 2** | FRED/ALFRED Ingestion, Data Normalization, Snapshots & Storage | **COMPLETED** | 2026-11-07 |
| **Phase 3** | Revision Detection, Revision Analytics & Data Quality Framework | **COMPLETED** | 2026-11-07 |
| **Phase 4** | Anomaly Detection, Subscriber Management, Telegram Alerting & Pipeline Orchestration | **COMPLETED** | 2026-11-07 |
| **Phase 5** | REST API Subsystem, FastAPI Routers, Pydantic Schemas & Background Tasks | Pending | |
| **Phase 6** | Streamlit Interactive Dashboard, GitHub Actions & End-to-End Verification | Pending | |

---

## Phase 1 Summary

### Implemented Models (14 ORM Classes)
1. `EconomicSeries` (`economic_series`): Registry of tracked indicators with frequency, transformation rules, and sanity ranges.
2. `Observation` (`observations`): Canonical observation period identities `(series_id, observation_date)`.
3. `ObservationVintage` (`observation_vintages`): Append-only immutable historical values of each observation with validity dates.
4. `RevisionHistory` (`revision_history`): Append-only revision records with exact `Decimal` differences, relative percentages, and lag metrics.
5. `DataEvent` (`data_events`): Event store driving subscriber alerts (`new_observation`, `revision_batch`, `anomaly`, `quality_issue`).
6. `AnomalyRecord` (`anomaly_records`): Statistical (Z-score) and machine learning (Isolation Forest) flagged anomalies.
7. `DataQualityLog` (`data_quality_logs`): Per-check validation audit records per ingestion run.
8. `DataQualityScore` (`data_quality_scores`): Composite health scores (0–100) per indicator.
9. `Subscriber` (`subscribers`): Telegram chat identity and subscription lifecycle.
10. `SubscriberPreference` (`subscriber_preferences`): Granular per-series notification preferences and revision thresholds.
11. `NotificationHistory` (`notification_history`): Delivery attempts, retry logs, and Telegram message IDs.
12. `IngestionRun` (`ingestion_runs`): Audit ledger for pipeline executions (API or snapshot mode).
13. `IngestionRunSeries` (`ingestion_run_series`): Series-level execution breakdown.
14. `AppState` (`app_state`): Operational key-value store for Telegram offsets and baseline parameters.

### SQL Views
* `latest_observation_values`: Single source of truth for the latest active vintage, earliest vintage, and vintage counts per observation.

### Database Migrations
* `0001_initial_schema`: Full PostgreSQL DDL for all 14 tables, constraints, foreign keys, indexes, and the `latest_observation_values` view.

### Configuration Files
* `config/indicators.yaml`: 5 initial active indicators (`GDP`, `CPI`, `UNRATE`, `INDPRO`, `RETAIL_SALES`) plus 2 inactive extensions (`PAYEMS`, `REAL_GDP_GROWTH`).
* `config/monitoring.yaml`: Quality weights (summing to 100), anomaly detection windows, and notification defaults.

### CLI Subcommands
* `econ-monitor db-check`: Check database connectivity, PostgreSQL version, and applied Alembic revision head.
* `econ-monitor seed-series`: Idempotent upsert of indicators from YAML configuration into `economic_series`.

### API Endpoints
* `GET /health`: Service health and database connectivity probe (returns HTTP 503 if database unreachable).
* `GET /api/v1/series`: Paginated listing of economic series with `active_only` filtering.
* `GET /api/v1/series/{code}`: Detailed series specification by uppercase code.

---

## Phase 2 Summary

### Ingestion Components Implemented
1. `FredClient` (`econ_monitor/ingestion/fred_client.py`):
   - Rate limiter throttled to 0.6s per request (~100 req/min, safely below FRED's 120 req/min quota).
   - Exponential backoff with jitter on HTTP 429 and 5xx errors.
   - Paging with `limit=100000` and validation of FRED `count` field.
   - API key redaction in URLs, log outputs, and exceptions.
   - Typed exceptions: `FredAuthError`, `FredNotFoundError`, `FredRateLimitError`, `FredServerError`, `FredResponseError`.
2. `normalize_observations` (`econ_monitor/ingestion/normalize.py`):
   - Strictly enforces DataFrame schema with `VINTAGE_FRAME_COLUMNS`.
   - Exact `Decimal` parsing with `.` mapped to `None` (`is_missing=True`).
   - Collapses consecutive duplicate vintages with unchanged values.
   - Rejection auditing returning `List[RejectedRow]` without silent row dropping.
3. `store_vintage_dataframe` & metadata persistence (`econ_monitor/ingestion/storage.py`):
   - Upserts `observations` and inserts append-only immutable `observation_vintages`.
   - Updates `economic_series` metadata (`title`, `frequency`, `units`, `seasonal_adjustment`, `fred_last_updated`, `change_unit_label`).
   - Logs rejected observations directly to `data_quality_logs`.
4. Parquet Snapshot Engine (`econ_monitor/ingestion/snapshots.py`):
   - Append-only Parquet storage under `data/snapshots/{fred_series_id}.parquet`.
   - Manifest management (`manifest.json`) tracking SHA-256 checksums, observation dates, vintage dates, and row counts.
   - Tamper-proofing: raises `SnapshotAppendOnlyViolationError` if historical rows are altered or deleted.
   - Functions 100% offline without database connectivity.
5. Ingestion Orchestrator (`econ_monitor/ingestion/service.py`):
   - Pluggable `DatabaseSink` and `SnapshotSink`.
   - Incremental skip checking based on FRED `last_updated` metadata (UTC-normalized).
   - Failure isolation: failures in one indicator log `status="partial"` without failing other indicators.
   - Snapshot import logic (`import_snapshots_to_database`) for populating the database from GitHub Actions snapshot artifacts.

### CLI Subcommands Registered
* `econ-monitor verify-series`: Live probe of FRED metadata, vintage semantics, and updates `docs/FRED_API_NOTES.md`.
* `econ-monitor snapshot`: Exports indicators into append-only Parquet snapshots and updates `manifest.json`.
* `econ-monitor ingest`: Runs ingestion against PostgreSQL with options `--series`, `--force-full`, `--offline-snapshots`.
* `econ-monitor import-snapshots`: Loads local or committed Parquet snapshots directly into the PostgreSQL database.

### Research & Verification Documentation
* `docs/FRED_API_NOTES.md`: Comprehensive documentation on ALFRED semantics (`output_type=1` vs `output_type=4`), pagination, missing value conventions, and indicator profiles.

---

## Phase 3 Summary

### Components Implemented
1. **Revision Metrics Engine** (`econ_monitor/revisions/metrics.py`):
   - Pure function `compute_revision_metrics(previous, new)` using Python `Decimal` arithmetic.
   - Calculates exact signed `absolute_revision = new - old` and `relative_revision_pct = ((new - old) / abs(old)) * 100`.
   - Safely yields `None` (not NaN or Div-by-Zero) when `old == 0` or either value is `None`.
   - Categorizes transitions into `value_revision`, `became_available`, and `became_missing`.
   - Helper `format_revision_change` formats changes with series unit labels (e.g. `+0.30 percentage points (+7.50% of previous rate)`).
2. **Revision Detector** (`econ_monitor/revisions/detector.py`):
   - Employs SQL window functions (`LAG(value)`, `LAG(is_missing)`, `LAG(vintage_date)`, `LAG(id)`) partitioned by `observation_id` ordered by `vintage_date`.
   - Distinct-from comparison detects genuine value revisions and NULL transitions while collapsing consecutive identical vintages.
   - Incremental processing: queries only vintages without existing entries in `revision_history`.
   - Revision numbering strictly per observation: genuine revisions increment monotonically (`revision_number = 1, 2, ...`).
   - Four distinct classifications exposed per series: `new_observations_count`, `revised_observations_count`, `unchanged_observations_count`, `missing_observations_count`.
   - Boundary tracking in `app_state` (`detection_boundary:{code}`) ensures transactional safety and failure isolation per series.
3. **Data Events Generator** (`econ_monitor/revisions/events.py`):
   - Implements atomic alerts for `new_observation` and `revision_batch` per A6.8.
   - Grouping: `revision_batch:{code}:{vintage_date}` captures all revisions released on that vintage date, computing top-5 revisions and maximum absolute/relative values.
   - Backfill rule: records `event_baseline:{code}` in `app_state`. Any events generated during initial history loading are flagged with `is_backfill=True`.
4. **Revision Analytics & Aggregations** (`econ_monitor/revisions/analytics.py`):
   - Typed dataclass query functions: `SeriesRevisionSummary`, `DirectionCounts`, `MagnitudeDistribution`, `PeriodRevisionMetric`, `LagBucketMetric`, `RevisionsPerObservationFrequency`, `FirstVsLatestComparison`.
   - Strict `true_initial_release_only` filter: excludes observations whose earliest ALFRED snapshot exceeds `initial_release_max_lag_days`.
   - Bias calculations: mean/median signed absolute and relative revisions, and share revised upward vs downward.
5. **Data Quality Framework & Health Scoring** (`econ_monitor/quality/`):
   - `schemas.py`: Pandera schemas for historical vintage frames and latest-value frames with configurable series ranges.
   - `checks.py`: 10 comprehensive checks per A9 (`missing_values`, `duplicate_observations`, `invalid_dates`, `invalid_numeric_format`, `unexpected_nulls`, `frequency_consistency`, `gaps_in_expected_grid`, `range_violations`, `staleness`, `pandera_schema`).
   - Grid checking leverages pandas offsets (`MS`, `QS`, `YS`), correctly verifying quarterly/annual cadences without false positive gaps.
   - Staleness incorporates configurable `max_stale_lag_days` and injected system clock.
   - `scoring.py`: deterministic composite health score formula (0 to 100) using configurable weights from `config/monitoring.yaml`.
   - `service.py`: logs executions into `data_quality_logs` and `data_quality_scores`, emitting `quality_issue` alert events strictly on passing-to-failing transitions.

### CLI Subcommands Registered
- `econ-monitor detect-revisions [--series CODE ...]`: Detects revisions, advances detection boundaries, and logs alert events.
- `econ-monitor quality-run [--series CODE ...] [--config PATH]`: Executes the data quality check suite, records execution logs, and computes health scores.

---

## Phase 4 Summary

### Components Implemented
1. **Anomaly Detection Feature Engineering** (`econ_monitor/anomalies/features.py`):
   - Computes configurable transformations: `pct_change = ((x_t / x_{t-1}) - 1) * 100` for level/index series, and `diff = x_t - x_{t-1}` for rate series.
   - Prevents look-ahead bias by shifting changes before rolling (`shift(1)`), ensuring observation $t$'s rolling mean and standard deviation strictly evaluate only past data $\{c_{t-1}, \dots, c_{t-W}\}$.
   - Handles edge cases: division by zero, non-positive std, incomplete windows, and missing observations.
2. **Z-Score Detector** (`econ_monitor/anomalies/zscore.py`):
   - Identifies statistical outliers where $|z| \ge \text{threshold}$ (configurable in `config/monitoring.yaml`, default 3.0).
   - Skips observations where the rolling standard deviation is zero or the trailing window is incomplete.
3. **Isolation Forest Detector** (`econ_monitor/anomalies/iforest.py`):
   - Scikit-learn Pipeline with `StandardScaler` and `IsolationForest(n_estimators=200, contamination=0.02, random_state=42)`.
   - Strictly enforces minimum observation counts before execution ($M: 60, Q: 40, A: 30$), cleanly skipping with documented reasons when historical data is insufficient.
   - Accurately captures decision scores and flags outliers ($predict == -1$).
4. **Anomaly Execution Service** (`econ_monitor/anomalies/service.py`):
   - Coordinates detection across active series against the `latest_observation_values` view.
   - Idempotently stores flagged records into `anomaly_records` with `UNIQUE(vintage_id, method)`.
   - Emits `DataEvent`s with key `anomaly:{code}:{obs_date}:{vintage_date}:{method}`, flagging `is_backfill=True` when baseline is not yet set.
   - Persists execution summaries in `app_state` under key `anomaly_last_run`.
5. **Subscriber Management** (`econ_monitor/notifications/subscribers.py`):
   - Lifecycle management for Telegram recipients (`Subscriber` and `SubscriberPreference`).
   - Automatically seeds default preferences from `config/monitoring.yaml` across active indicators upon registration.
   - Supports granular preferences per indicator or batch updating across all indicators.
   - Provides deactivation on `/stop` or HTTP 403 (bot blocked).
6. **Telegram Bot API Client** (`econ_monitor/notifications/telegram_client.py`):
   - Direct HTTP wrapper over Telegram Bot API (`sendMessage`, `getUpdates`) without third-party frameworks.
   - Complete secret protection: redacts bot tokens from URLs, logging output, and exception traces.
   - HTML escaping helper preventing injection attacks.
   - Chunking helper splitting long messages cleanly at line breaks within the 4096-character limit.
   - Handles HTTP 429 rate limits by extracting `parameters.retry_after` and applying exponential backoff.
   - Raises typed `TelegramUserBlockedError` on HTTP 403 to trigger automatic subscriber deactivation.
7. **Interactive Bot Commands Handler** (`econ_monitor/notifications/bot_commands.py`):
   - Pure, testable update handler supporting `/start`, `/help`, `/indicators`, `/subscribe <CODE|all>`, `/unsubscribe <CODE|all>`, `/preferences`, `/status`, `/stop`.
8. **Message Templates Engine** (`econ_monitor/notifications/templates.py`):
   - Unit-aware, HTML-formatted messages for `new_observation`, `revision_batch`, `anomaly`, and `quality_issue`.
   - Distinctly presents anomalies with review disclaimer (*"Flagged for investigation — genuine shocks or outliers, not confirmed bad data"*).
9. **Notification Dispatcher** (`econ_monitor/notifications/dispatcher.py`):
   - Selects young, non-backfill events ($< \text{NOTIFY\_MAX\_EVENT\_AGE\_DAYS}$) from active series.
   - Evaluates subscriber preferences and filters revisions below `min_revision_pct`.
   - PostgreSQL advisory locking (`pg_try_advisory_lock`) stopping concurrent workers.
   - Idempotent delivery tracking via `notification_history` (pending $\to$ sent / failed).
   - Rate limit pacing between sends and `--dry-run` preview support without external network calls.
10. **Pipeline Orchestrator** (`econ_monitor/pipeline.py`):
    - Multi-stage sequential executor: Ingestion (API or Snapshot) $\to$ Revision Detection $\to$ Quality Checks $\to$ Anomaly Detection $\to$ Notification Dispatch.
    - Tracks step timing and outcomes; stops immediately if any stage fails.

### CLI Subcommands Registered
- `econ-monitor anomaly-run [--series CODE ...]`: Runs z-score and Isolation Forest detection.
- `econ-monitor notify-dispatch [--dry-run]`: Dispatches pending alerts to subscribers.
- `econ-monitor telegram-poll [--timeout SECONDS] [--once]`: Long polls for Telegram bot commands.
- `econ-monitor telegram-test [--chat-id ID]`: Sends a test alert to verify Telegram credentials.
- `econ-monitor sync [--dir PATH] [--no-notify] [--dry-run-notify]`: Snapshot pipeline mode.
- `econ-monitor pipeline [--series CODE ...] [--no-notify] [--dry-run-notify]`: Live API pipeline mode.

---

## Phase 5 Completed: FastAPI Integration and Streamlit Dashboard

### Delivered Modules & Components

1. **FastAPI Backend Endpoints (`econ_monitor/api/routers/` & `econ_monitor/schemas/`):**
   - **Health:** `GET /health` with dynamic database connectivity status.
   - **Series:** `GET /series`, `GET /series/{code}`, `POST /series/sync-config`.
   - **Observations:** `GET /series/{code}/observations` supporting `view=latest|first|as_of` (`as_of=YYYY-MM-DD`), date boundaries, and pagination; `GET /observations/{id}/vintages` for the full historical vintage chain.
   - **Revisions:** `GET /revisions` with multi-factor filters (`series`, `start_date`, `end_date`, `direction`, `type`, `min_abs_pct`), `GET /revisions/{id}`.
   - **Analytics:** `GET /analytics/revisions/summary`, `/by-period`, `/direction-counts`, `/magnitude-distribution`, `/by-lag`, `/frequency`, `/first-vs-latest` (all supporting `series` and `true_initial_release_only`).
   - **Anomalies:** `GET /anomalies` (calculating `is_superseded_vintage`), `GET /anomalies/status`, `PATCH /anomalies/{id}` (updating status and reviewer notes), `POST /anomalies/run`.
   - **Quality:** `GET /quality/scores/latest`, `GET /quality/scores/history`, `GET /quality/logs`, `POST /quality/run`.
   - **Subscribers:** `GET /subscribers`, `POST /subscribers`, `GET /subscribers/{id}`, `PATCH /subscribers/{id}`, `DELETE /subscribers/{id}` (deactivation), `GET /subscribers/{id}/preferences`, `PUT /subscribers/{id}/preferences`, `POST /subscribers/{id}/test-message`.
   - **Notifications:** `GET /notifications`, `GET /notifications/summary`, `GET /events`, `POST /notifications/dispatch`.
   - **Ingestion:** `POST /ingestion/run` (concurrency-safe background runner with HTTP 409 conflict guard), `GET /ingestion/runs`, `GET /ingestion/runs/{id}`, `POST /ingestion/import-snapshots`.

2. **Streamlit Multipage Dashboard (`dashboard/`):**
   - **`dashboard/api_client.py`:** Type-safe HTTP client wrapping all backend endpoints with uniform error normalization and timeouts.
   - **`dashboard/charts.py`:** Plotly visualization builders:
     - Historical time series line chart with markers.
     - Revision stair-step vintage fan chart (`shape='hv'`).
     - Revision magnitude histogram.
     - Lag bucket bar chart.
     - Revision direction donut chart.
     - Mean relative revision by period bar chart.
   - **`dashboard/components.py`:** Reusable UI widgets: standardized page header banner with update timestamp and active filter indicator, friendly empty-state guidance with suggested CLI commands, and metric cards.
   - **`dashboard/app.py` & 9 Dedicated Pages:**
     1. `1_Overview.py`: System status, indicator KPIs, indicators table, on-demand pipeline trigger.
     2. `2_Economic_Indicators.py`: Catalog metadata, lag parameters, config sync.
     3. `3_Historical_Observations.py`: Latest/first/as-of line charts, vintage drilldown fan charts.
     4. `4_Revision_History.py`: Filterable revision events audit table and single revision inspector.
     5. `5_Revision_Analytics.py`: Statistical summary KPIs, histograms, lag buckets, period metrics, first vs latest directional bias table.
     6. `6_Anomaly_Detection.py`: Flagged records, superseded vintage tags, reviewer notes, status update form, detection execution trigger.
     7. `7_Data_Quality.py`: Multi-factor health score cards (0–100), component score breakdown, evaluation logs, on-demand test runner.
     8. `8_Subscriber_Management.py`: Subscriber registration, active recipients list, preference editor per indicator, test alert trigger.
     9. `9_Notification_History.py`: Delivery metrics, audit table, manual and dry-run dispatch triggers.

3. **Architectural Isolation:**
   - Strict architectural boundary verified: `dashboard/` communicates **only** via HTTP (`ApiClient`) and has zero imports from `sqlalchemy`, `psycopg`, `alembic`, or `econ_monitor`.
   - Verified by dedicated AST scan test `tests/unit/test_dashboard_boundaries.py`.

4. **Testing Suite:**
   - 86 unit tests passing across backend services, API routers, architectural boundaries, and Streamlit AppTests.
   - Zero lint or formatting errors across all 108 files (`ruff check` and `ruff format` 100% clean).

---

## Phase 6 Completed: GitHub Actions Automation, Persistence, Integration, and Documentation

### Delivered Modules & Components

1. **Remote Snapshot Harvesting Workflow (`.github/workflows/collect-data.yml`):**
   - Triggers: Scheduled cron daily at 15:00 UTC (`0 15 * * *`) and `workflow_dispatch` with inputs `series` and `full_refresh`.
   - Permissions: `contents: write`.
   - Concurrency group `collect-data` (`cancel-in-progress: false`).
   - Setup Python 3.11 with pip caching; executes `python -m econ_monitor.cli snapshot`.
   - Manifest validation and step summary publishing to `$GITHUB_STEP_SUMMARY`.
   - Bot commit configuration (`github-actions[bot]`); stages strictly `data/snapshots/`.
   - Commit message `data: update snapshots [skip ci]`; rebase pull and push.
   - Operates with zero database connection and zero Telegram secrets (only `FRED_API_KEY` required).

2. **Continuous Integration Workflow (`.github/workflows/ci.yml`):**
   - Triggers on push and PR to `main` and `master`.
   - Runs `ruff check .`, `ruff format --check .`, and `pytest -m "not db and not live" -v`.

3. **Local Synchronization Enhancement & Automation Scripts:**
   - Updated `econ-monitor sync` command with `--pull` flag (`econ_monitor/cli.py`) to run `git pull --ff-only` prior to ingestion.
   - Created `scripts/sync_local.ps1` (PowerShell) for Windows Task Scheduler automation.
   - Created `scripts/sync_local.sh` (Bash) for macOS/Linux cron automation.

4. **End-to-End Simulated Revision Test (`tests/unit/test_simulated_revision_e2e.py`):**
   - Executes full lifecycle against an isolated in-memory test database fixture:
     - Seeds indicators and active subscriber preferences.
     - Ingests initial observation vintage (baseline).
     - Ingests simulated revised vintage.
     - Detects revisions: exactly 1 revision row created, exact mathematical deltas, zero modification/deletion of earlier vintage.
     - Generates `revision_batch` data event.
     - Dispatches notification with mocked Telegram client.
     - Re-executes detection and dispatch: proves 100% idempotency (0 new revisions, 0 new events, 0 duplicate alerts).

5. **Comprehensive Project Documentation:**
   - **`README.md`**: Complete production guide with USP, 4 detailed Mermaid architecture diagrams (System Context, Ingestion/Analytics Pipeline Flow, Model ER Diagram, Local vs GitHub Matrix), installation guides (Windows/macOS/Linux), CLI command reference, and troubleshooting.
   - **`docs/ARCHITECTURE.md`**: In-depth architectural design decisions, immutability rationale, anomaly detection trade-offs, and boundary isolation.
   - **`docs/SCREENSHOTS.md` & `docs/screenshots/`**: Screenshot capture guide and image placeholders for all 9 dashboard pages.
   - **`docs/FEATURE_CHECKLIST.md`**: Comprehensive traceability matrix verifying all spec requirements against code and unit tests.
   - **Secrets & Configuration Audit**: Finalized `.env.example`, `.gitignore`, and confirmed zero credential leaks in git history.

---

## Pending Live Credentials Tasks (Awaiting User Credentials)
* Physical connection to user's live PostgreSQL instance (`DATABASE_URL`).
* Live execution of `econ-monitor verify-series` and `econ-monitor ingest` using actual `FRED_API_KEY`.
* Executing `alembic upgrade head` against live database.
* Running live integration tests with `TEST_DATABASE_URL`.
* Real-data ALFRED cross-verification via `fredapi.get_series_as_of_date` against live database.
* Live Telegram notifications delivery to user's registered chat ID with active bot token.

