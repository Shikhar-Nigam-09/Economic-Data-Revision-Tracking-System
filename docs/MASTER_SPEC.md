PART A: MASTER PROJECT SPECIFICATION
A1. Purpose
The system collects economic indicators from FRED and ALFRED, stores every historical version (vintage) of every observation, detects revisions, validates data quality, flags anomalies, notifies Telegram subscribers, and shows everything in a local Streamlit dashboard.
The central feature is revision tracking. If an agency first publishes GDP growth as 2.5% and later revises it to 2.8%, the system keeps both versions, measures the revision (+0.3 percentage points, upward), notifies subscribers, and shows both values on the dashboard. It must also tell a brand-new observation apart from a revision to an old one.
A2. Hard constraints
• Portfolio and academic project. It runs locally. No live deployment, no public hosting, no external hosted database.
• Free or open-source tools only.
• No Docker, Redis, Celery, Kafka, Kubernetes, or message queues. Background work uses FastAPI BackgroundTasks and CLI commands.
• Understandable, maintainable code. No over-engineering.
• Real data only. Synthetic data is allowed only inside automated tests and must be labeled synthetic. It is never loaded into the development database or shown on the dashboard.
A3. Technology stack (fixed)
Component
Technology
Language
Python 3.11+
Data sources
FRED + ALFRED
Data collection
fredapi + requests
Scheduling
GitHub Actions
Data processing
pandas + NumPy
Database
PostgreSQL 15+ (local install)
Backend
FastAPI
ORM
SQLAlchemy 2.0 (synchronous API)
Migrations
Alembic
Revision detection
ALFRED vintage data + Python + SQL
Data validation
Custom Python checks + Pandera
Anomaly detection
Z-score + scikit-learn Isolation Forest
Notifications
Telegram Bot API
Dashboard
Streamlit
Visualization
Plotly
Configuration
Environment variables + YAML config files
Version control
Git + GitHub
Supporting libraries that the stack itself requires (allowed): psycopg v3 (driver, URL scheme postgresql+psycopg://), pydantic v2 and pydantic-settings, PyYAML, pyarrow (Parquet snapshots), uvicorn, httpx (FastAPI TestClient), pytest, responses (HTTP mocking in tests), ruff. The CLI uses stdlib argparse. Telegram is called with requests directly against the Bot API, with no extra bot framework.
A4. Repository layout
economic-data-monitor/
  pyproject.toml            # installed with: pip install -e .[dev]
  alembic.ini
  .env.example  .gitignore  README.md
  alembic/                  # env.py, versions/
  config/                   # indicators.yaml, monitoring.yaml
  data/snapshots/           # committed Parquet snapshots + manifest.json
  docs/                     # MASTER_SPEC.md, PROJECT_STATE.md, FRED_API_NOTES.md,
                            # ARCHITECTURE.md, FEATURE_CHECKLIST.md
  econ_monitor/
    core/                   # config, logging, clock, errors
    db/                     # base, session, models, repositories, views
    ingestion/              # fred_client, normalize, storage, service, snapshots
    revisions/              # metrics, detector, events, analytics
    quality/                # checks, schemas, scoring, service
    anomalies/              # features, zscore, iforest, service
    notifications/          # subscribers, telegram_client, bot_commands,
                            # templates, dispatcher
    schemas/                # Pydantic request/response schemas
    api/                    # main, deps, routers/
    pipeline.py             # orchestration of all steps
    cli.py                  # python -m econ_monitor.cli <command>
  dashboard/                # app.py, api_client.py, charts.py, pages/
  scripts/
  tests/                    # unit/ and integration/
  .github/workflows/        # collect-data.yml, ci.yml
Dependency direction (prevents circular imports): core <- db <- (ingestion, revisions, quality, anomalies, notifications) <- (pipeline, api, cli). The dashboard package talks to the API over HTTP only and imports nothing from econ_monitor. All imports are absolute (from econ_monitor.db.models import ...).
A5. Configuration
Environment variables (secrets and machine-specific values only)
Variable
Purpose
DATABASE_URL
Main PostgreSQL URL, e.g. postgresql+psycopg://USER:PASSWORD@localhost:5432/econ_monitor
TEST_DATABASE_URL
A separate test database. Tests never touch the main one
FRED_API_KEY
Free key from fred.stlouisfed.org (the same key serves ALFRED data)
TELEGRAM_BOT_TOKEN
Bot token from @BotFather
API_HOST, API_PORT
FastAPI bind address. Defaults 127.0.0.1 and 8000
API_BASE_URL
Used by Streamlit. Default http://127.0.0.1:8000
NOTIFY_MAX_EVENT_AGE_DAYS
Events older than this are never notified. Default 30
LOG_LEVEL
Default INFO
Secrets are read only from the environment (.env locally, GitHub Secrets in Actions). They are never stored in the database, never logged, and never committed.
Config files
• config/indicators.yaml: one entry per indicator with code, fred_series_id, display_name, category, active, anomaly_transform (pct_change or diff), expected_min, expected_max, max_stale_lag_days, initial_release_max_lag_days, history_start, optional change_unit_label, and notes. Frequency, units, and seasonal adjustment come from FRED metadata. The config may state expected values, and a mismatch is reported. Adding an indicator means adding a YAML entry and running seed-series, with no code changes.
• config/monitoring.yaml: quality weights and thresholds, anomaly parameters, notification settings, and the frequency table (see A9 and A10).
Initial indicators (candidate IDs, to be verified against live FRED metadata in Phase 2)
Code
FRED ID
Expected title
Freq
Expected units
Anomaly transform
Sane range
Max stale lag (days)
GDP
GDPC1
Real Gross Domestic Product
Q
Billions of Chained Dollars, SAAR
pct_change
0 to 200,000
130
CPI
CPIAUCSL
CPI for All Urban Consumers: All Items, Seasonally Adjusted
M
Index 1982-1984=100
pct_change
0 to 2,000
60
UNRATE
UNRATE
Unemployment Rate
M
Percent
diff
0 to 30
45
INDPRO
INDPRO
Industrial Production: Total Index
M
Index 2017=100
pct_change
0 to 300
60
RETAIL_SALES
RSAFS
Advance Retail Sales: Retail Trade and Food Services
M
Millions of Dollars
pct_change
0 to 10,000,000
60
Sane ranges are loose guards against corrupt data, not forecasts. Defaults: initial_release_max_lag_days is 90 for monthly and 150 for quarterly series. history_start defaults to 1990-01-01.
Optional entries shipped with active: false to demonstrate config-driven extension: PAYEMS (Total Nonfarm Payrolls, heavily revised) and A191RL1Q225SBEA (Real GDP percent change from preceding period, SAAR, which matches the 2.5% to 2.8% style example).
Honest expectation: revision richness differs by series. GDP, industrial production, and retail sales are revised often. CPI (seasonal-factor updates) and the unemployment rate (annual seasonal-factor updates) are revised rarely. The dashboard must show this truthfully.
A6. Core domain rules (most important section)
1. Two different dates. observation_date is the period the number describes (for example 2026-01-01 for January 2026, or the first day of a quarter for quarterly data). vintage_date is the date on which that number became known (ALFRED realtime_start). Never mix them in names, joins, or charts.
2. ALFRED semantics. The FRED API series/observations endpoint called with realtime_start=1776-07-04 and realtime_end=9999-12-31 returns, per observation, rows carrying realtime_start (first date the value was known) and realtime_end (last date it was current; 9999-12-31 means still current). The value . means missing. Rows are expected only when the value changes, but code must defensively collapse consecutive identical values. Never reconstruct revisions by repeatedly downloading current FRED values. Phase 2 verifies this behavior against the live API and records it in docs/FRED_API_NOTES.md. Verified behavior overrides assumptions here.
3. Immutability. observation_vintages, revision_history, data_events, and data_quality_* rows are never updated or deleted by pipeline code. Inserts use ON CONFLICT DO NOTHING. anomaly_records may change only its review fields. source_realtime_end is informational and never used in logic. Validity windows are derived from the next vintage date.
4. Classification.
    ◦ New observation: the observation identity is created for the first time. Its first vintage is its original publication, not a revision.
    ◦ Revised observation: a later vintage has a different non-null value from the previous vintage.
    ◦ Unchanged: no new vintage, or the same value.
    ◦ Missing: value is NULL (source .). A missing observation inside the expected date grid is a separate quality check.
    ◦ Transitions involving NULL are stored in revision_history as became_available or became_missing with no numeric metrics.
5. Revision metrics (computed with Decimal): absolute_revision = new - old. relative_revision_pct = ((new - old) / abs(old)) * 100, stored as NULL when old is zero or NULL (never infinity or NaN). direction is up if new > old and down if new < old. revision_number is 1 for the first change after the original publication, 2 for the next, and so on. revision_lag_days = revision_date - observation_date. Units: absolute revision is expressed in the series' units, labeled by change_unit_label (for example percentage points for UNRATE, index points for CPI and INDPRO, millions of dollars for RETAIL_SALES). For percent-valued series the relative percentage is a percent of the previous rate and must be labeled as such, so it is never confused with percentage points. All dashboard and notification text uses the label.
6. True initial release. ALFRED does not cover all history. For old observations the first vintage is only the earliest ALFRED snapshot, not the original release. An observation counts as having a true initial release only if first_vintage_date - observation_date <= initial_release_max_lag_days. Analytics comparing first release to later values default to those observations, with a switch to include all. The dashboard says which filter is active.
7. Frequency. Frequency comes from FRED metadata (M, Q, A supported; W and D optional). The expected observation grid uses pandas offsets MS, QS, YS. Quarterly and annual series are never expected to have monthly observations. Rolling windows and change features are computed on the series' native observation index, with no resampling or interpolation. An unsupported frequency fails loudly.
8. Events drive alerts. Every alertable fact becomes a data_events row with a deterministic event_key:
    ◦ new_observation:{code}:{observation_date}
    ◦ revision_batch:{code}:{vintage_date}: one event per series per vintage date, grouping all revisions published in that vintage. The payload holds counts of up and down revisions, the largest absolute and relative revision, and the top 5 revisions.
    ◦ anomaly:{code}:{observation_date}:{vintage_date}:{method}
    ◦ quality_issue:{code}:{check_name}:{run_uuid}: emitted only when a check transitions from passing to failing. Events created while loading a series' first-ever history are marked is_backfill = true and are never notified. The dispatcher also ignores events older than NOTIFY_MAX_EVENT_AGE_DAYS.
A7. Database schema
All timestamps are timestamptz (UTC). Dates are date. Money-like and index values use Numeric(24,8), never float. Enumerations are String columns with CHECK constraints and Python StrEnum classes (not native PostgreSQL ENUM types). Constraint names follow an Alembic naming convention set on the declarative base.
Table
Purpose
Key columns and constraints
economic_series
One row per tracked indicator
id PK; code UNIQUE; fred_series_id UNIQUE; title; frequency; units; seasonal_adjustment; change_unit_label; anomaly_transform; expected_min, expected_max; max_stale_lag_days; initial_release_max_lag_days; history_start; is_active; fred_last_updated; created_at; updated_at
observations
Identity of one observation
id PK; series_id FK; observation_date; first_seen_at; ingestion_run_id FK; UNIQUE(series_id, observation_date)
observation_vintages
Append-only versions of each observation
id BigInt PK; observation_id FK; vintage_date; source_realtime_end NULL; value Numeric NULL; is_missing; ingestion_run_id FK; retrieved_at; source; UNIQUE(observation_id, vintage_date); CHECK(is_missing = (value IS NULL)); index (observation_id, vintage_date DESC)
revision_history
Append-only revisions
id; observation_id FK; series_id FK; from_vintage_id FK; to_vintage_id FK UNIQUE; revision_number >= 1; observation_date; revision_date; previous_value; new_value; absolute_revision; relative_revision_pct; direction (up/down/NULL); revision_type (value_revision/became_available/became_missing); revision_lag_days; detected_at; indexes on (series_id, revision_date) and (observation_id, revision_number)
data_events
The unit of alerting
id; event_key UNIQUE; event_type; series_id FK; observation_id NULL; vintage_date NULL; payload JSONB; is_backfill; occurred_on; created_at
anomaly_records
Flagged values
id; series_id FK; observation_id FK; vintage_id FK; method (zscore/isolation_forest); score; threshold NULL; features JSONB; status (flagged/reviewed/dismissed); reviewed_at; note; detected_at; UNIQUE(vintage_id, method)
data_quality_logs
One row per check per run
id; run_uuid; series_id FK; ingestion_run_id NULL FK; check_name; severity (info/warning/error); passed; affected_count; message; details JSONB; checked_at; index (series_id, checked_at)
data_quality_scores
Health score history
id; run_uuid; series_id FK; score Numeric(5,2); component_scores JSONB; computed_at; UNIQUE(run_uuid, series_id)
subscribers
Telegram users
id; telegram_chat_id BigInt UNIQUE; telegram_username; display_name; is_active; created_at; updated_at; deactivated_at
subscriber_preferences
Per-subscriber, per-series settings
id; subscriber_id FK ON DELETE CASCADE; series_id FK; is_enabled; notify_new_observation; notify_revision; notify_anomaly; notify_quality_issue; min_revision_pct NULL; UNIQUE(subscriber_id, series_id). "All indicators" is represented by one row per active series
notification_history
Every attempted alert
id; subscriber_id FK; event_id FK; channel (default telegram); status (pending/sent/failed/skipped); message_text; attempts; last_error; telegram_message_id; created_at; sent_at; UNIQUE(subscriber_id, event_id, channel)
ingestion_runs
One row per ingestion execution
id; run_uuid UNIQUE; mode (api/snapshot); trigger (manual/scheduled/dashboard/cli); started_at; finished_at; status (running/success/partial/failed); series_requested; series_succeeded; rows_fetched; rows_inserted; error_summary; snapshot_manifest_sha256; git_commit
ingestion_run_series
Per-series result of a run
id; run_id FK; series_id FK; status (success/skipped_no_new_data/failed); vintages_fetched; observations_new; vintages_new; vintages_unchanged; missing_values; error_message; UNIQUE(run_id, series_id)
app_state
Small key-value state
key PK; value JSONB; updated_at. Holds the Telegram update offset, per-series baselines, and the last anomaly-run summary
SQL view latest_observation_values (created in a migration with op.execute): observation_id, series_id, observation_date, first_vintage_date, first_value, latest_vintage_date, latest_value, vintage_count. It is the single definition of the "current" value of an observation. Alembic autogenerate must not try to manage it.
A8. Ingestion and persistence architecture (local versus GitHub)
GITHUB (ephemeral runner)                 YOUR COMPUTER
-------------------------                 ---------------------------------------
schedule or manual trigger                git pull   (receives new snapshots)
  python -m econ_monitor.cli snapshot        |
  FRED + ALFRED -> Parquet files             v
  commit data/snapshots/** to the repo     python -m econ_monitor.cli sync
  (no database, no Telegram token)            import snapshots -> PostgreSQL
                                              detect revisions -> data quality
                                              anomaly detection -> events
                                              Telegram notifications
                                                  |
                                              FastAPI :8000  <-  Streamlit :8501
• GitHub runners are ephemeral and cannot reach your local PostgreSQL. No workflow may assume otherwise.
• The snapshot command must run with no DATABASE_URL, no PostgreSQL, and no Telegram token. It needs only FRED_API_KEY.
• A local ingest command (API directly into PostgreSQL) also exists. Both paths share the same fetch, normalize, and storage logic and differ only in the sink.
• Snapshot layout. data/snapshots/<FRED_ID>/vintages.parquet holds all vintage rows for a series, sorted by observation_date then vintage_date, with values stored exactly (decimal, not float), is_missing, source_realtime_end, retrieved_at, and source. data/snapshots/<FRED_ID>/metadata.json holds the FRED metadata and last_updated. data/snapshots/manifest.json holds schema_version, generated_at, the git commit if known, and per series: row_count, min and max observation_date, max vintage_date, and content_sha256.
• Append-only merge. The runner starts from a checkout that already contains the previous snapshot. New rows are added. Existing rows are never modified or removed. If an existing key now arrives with a different value, the run fails loudly. Files are rewritten only when content changed, which keeps Git history small.
• Importer. Verifies schema_version and checksums first, is idempotent (a second import inserts zero rows), and records an ingestion_runs row with mode snapshot and the manifest hash.
• Workflow commit rules. Stage only data/snapshots/. Commit only if something changed. Message data: update snapshots [skip ci]. Use the GitHub Actions bot identity. Pull with rebase before pushing. Use a concurrency group so two runs never overlap. Secrets are never printed or committed.
• Incremental collection. Skip a series when FRED last_updated and the newest vintage date show nothing new compared with the stored data or snapshot. Otherwise fetch and insert only new vintage rows.
• Notifications run only on your computer, because subscribers live in your local database. There is no webhook (no public server). The bot receives commands through long polling (telegram-poll).
A9. Data quality design
Each check writes one data_quality_logs row per run.
check_name
What it verifies
Severity when failing
missing_values
NULL values among latest vintages
warning
duplicate_observations
Duplicate (series, observation date) or duplicate vintage keys
error
invalid_dates
Unparseable or impossible dates, observation dates in the future, a vintage dated before its observation. Includes rows rejected at ingestion
error
invalid_numeric_format
Non-numeric value strings rejected at ingestion
error
unexpected_nulls
NULL in fields that must never be NULL
error
frequency_consistency
Spacing of recent observations matches the series' frequency
warning
gaps_in_expected_grid
Dates missing from the grid expected for the series' frequency since history_start
warning
range_violations
Latest values outside expected_min and expected_max
error
staleness
today - (latest_observation_date + one period) > max_stale_lag_days
warning
pandera_schema
Pandera validation (lazy=True) of the vintage frame and the latest-value frame
error
Health score (0 to 100), weights configurable in monitoring.yaml, defaults: completeness 25 (missing_values, gaps_in_expected_grid), validity 20 (invalid_dates, invalid_numeric_format, range_violations, unexpected_nulls), uniqueness 10, frequency consistency 15, timeliness 20 (staleness), schema conformance 10 (pandera_schema). A component equals 100 times (1 minus the failing fraction of checked records), clamped to 0 to 100. The timeliness component falls linearly with days overdue and reaches 0 when overdue days equal the allowed lag. The formula is documented in code and README. Scores are stored every run, so health has a history.
A10. Anomaly detection design
• Runs on the latest vintage of each observation. The transform comes from config: pct_change is (x_t / x_(t-1) - 1) * 100 for level and index series, and diff is x_t - x_(t-1) for rate series. Both use the native observation index.
• Features: current value, previous value, transformed change, trailing rolling mean and rolling standard deviation of the change (window excludes the current point), and the z-score of the change against that window.
• Windows: monthly 12, quarterly 8, annual 5 observations (configurable). Z-score requires a full window. Isolation Forest requires at least 60 monthly, 40 quarterly, or 30 annual rows with all features defined.
• Z-score: flag when abs(z) >= 3.0 (configurable). Skip when the trailing std is 0 or the window is incomplete.
• Isolation Forest: Pipeline(StandardScaler, IsolationForest(n_estimators=200, contamination=0.02, random_state=42)) fitted per series. Store the decision score. With insufficient history, skip Isolation Forest for that series (recorded in the run summary) and still run the z-score. Documented limitation: Isolation Forest is fitted on full history, so historical scores use hindsight, while the z-score uses no look-ahead.
• A flag means flagged for investigation, never confirmed bad data. Genuine shocks (for example 2020) are expected to be flagged and must not be filtered out.
• Idempotent through UNIQUE(vintage_id, method). A revised observation gets a new vintage that is evaluated afresh. Older records stay, and the dashboard marks them as based on a superseded vintage.
• The run summary (per series and method: ran, skipped with reason, flagged count) is stored in app_state under anomaly_last_run.
A11. Notifications design
• Notification types and defaults: new observation (off), revision (on), anomaly (on), quality issue (off). Preferences are per subscriber and per series. min_revision_pct is compared with the largest absolute relative revision in a revision_batch event.
• Flow: eligible events (not backfill, young enough, active series) are matched with subscribers whose preferences allow them. Insert a notification_history row as pending using ON CONFLICT DO NOTHING, then send, then mark sent, failed (retried on later runs, max 3 attempts), or skipped. A PostgreSQL advisory lock stops two dispatchers running at once.
• Telegram client: HTML parse mode with escaping, split messages at 4096 characters, honor 429 retry_after, deactivate a subscriber on 403 (bot blocked), and never log the token.
• Without a token the dispatcher fails with a clear message and a non-zero exit code. No row is ever marked sent falsely.
• Bot commands: /start, /help, /indicators, /subscribe <CODE|all>, /unsubscribe <CODE|all>, /preferences, /status, /stop.
• Message wording is unit-aware, states the observation period and the vintage date, says revised upward or downward, and describes anomalies as flagged for review.
A12. API design
Base path /api/v1. Errors use {error: {code, message, details}}. Lists use limit (default 100, max 1000) and offset. Routers are thin, and business logic lives in service modules shared with the CLI.
Group
Endpoints
Health
GET /health
Series
GET /series, GET /series/{code}, POST /series/sync-config
Observations
GET /series/{code}/observations (start, end, view=latest|first|as_of, as_of=YYYY-MM-DD), GET /observations/{id}/vintages
Revisions
GET /revisions (filters: series, dates, direction, type, min_abs_pct), GET /revisions/{id}
Analytics
GET /analytics/revisions/summary, /by-period, /direction-counts, /magnitude-distribution, /by-lag, /frequency, /first-vs-latest (all accept series and true_initial_release_only)
Anomalies
GET /anomalies, GET /anomalies/status, PATCH /anomalies/{id}, POST /anomalies/run
Quality
GET /quality/scores/latest, GET /quality/scores/history, GET /quality/logs, POST /quality/run
Subscribers
GET /subscribers, POST /subscribers, GET, PATCH, DELETE (deactivate) /subscribers/{id}, GET, PUT /subscribers/{id}/preferences, POST /subscribers/{id}/test-message
Notifications
GET /notifications, GET /notifications/summary, GET /events, POST /notifications/dispatch
Ingestion
POST /ingestion/run (background task, 409 if a run is active), GET /ingestion/runs, GET /ingestion/runs/{id}, POST /ingestion/import-snapshots
A13. Dashboard design
Streamlit multipage app with Plotly. Pages: (1) Overview, (2) Economic Indicators, (3) Historical Observations, (4) Revision History, (5) Revision Analytics, (6) Anomaly Detection, (7) Data Quality, (8) Subscriber Management, (9) Notification History.
Rules: it talks to the API over HTTP only. It never shows fabricated data. Empty states say which command to run. All values carry unit labels. The "true initial release only" filter is always visible when active. Each page shows when its data was last updated.
A14. CLI catalogue
Run as python -m econ_monitor.cli <command>.
db-check, seed-series, verify-series, ingest, snapshot, import-snapshots, detect-revisions, quality-run, anomaly-run, notify-dispatch, telegram-poll, telegram-test, sync (snapshot import mode, then everything after), pipeline (live API mode, then everything after).
Exit codes: 0 success, 1 failure, 2 misconfiguration. Every command prints a structured summary. A failed command never prints a success message.
A15. Engineering standards
• Model class names (use exactly these everywhere): EconomicSeries, Observation, ObservationVintage, RevisionHistory, DataEvent, AnomalyRecord, DataQualityLog, DataQualityScore, Subscriber, SubscriberPreference, NotificationHistory, IngestionRun, IngestionRunSeries, AppState.
• Time comes from econ_monitor/core/clock.py so tests can freeze it. All timestamps are UTC.
• Typed exceptions, no bare except, no swallowed failures, honest statuses.
• Standard logging. Never log secrets. Redact API keys in URLs and exception text.
• Tests use pytest with markers db (needs TEST_DATABASE_URL) and live (needs a real API key, skipped by default). Test schemas are built by Alembic migrations.
• ruff clean.
• Definition of done for every phase: tests pass, alembic upgrade head and alembic check are clean, commands were run on real data where applicable, the completion report is delivered, and docs/PROJECT_STATE.md is updated.