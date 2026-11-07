# Economic Data Monitoring and Revision Tracking System

[![Continuous Integration](https://github.com/shikhar/economic-data-monitoring/actions/workflows/ci.yml/badge.svg)](https://github.com/shikhar/economic-data-monitoring/actions/workflows/ci.yml)
[![Data Snapshots](https://github.com/shikhar/economic-data-monitoring/actions/workflows/collect-data.yml/badge.svg)](https://github.com/shikhar/economic-data-monitoring/actions/workflows/collect-data.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.32+-FF4B4B.svg)](https://streamlit.io)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-336791.svg)](https://www.postgresql.org/)

An enterprise-grade macroeconomic intelligence platform engineered to monitor, archive, and analyze macroeconomic time series and publication vintages from the Federal Reserve Bank of St. Louis (FRED and ALFRED).

---

## 🌟 Unique Selling Proposition (USP)

Most data pipelines overwrite economic observations when agencies issue revisions, permanently destroying the historical reality upon which past market reactions, central bank decisions, and investment strategies were formulated.

**This system solves that fundamental flaw:**
1. **True Point-in-Time Vintages:** Stored immutably via append-only tables and columnar Parquet snapshots.
2. **Revision Analytics:** Quantifies revision magnitudes, calculates revision lag days, evaluates directional bias (systematic under/over-estimation), and renders revision heatmaps.
3. **Dual Anomaly Detection:** Blends statistical rolling Z-score filters with unsupervised scikit-learn Isolation Forests.
4. **Idempotent Telegram Intelligence:** Dispatches actionable alerts directly to Telegram subscribers without double-alerting.
5. **Zero-Database Cloud Harvesting:** Collects data on ephemeral GitHub Actions runners without exposing private databases.

---

## 📊 Core Tracked Economic Indicators

The initial system configuration tracks five foundational United States macroeconomic indicators:

| Code | FRED Series ID | Title | Frequency | Seasonal Adjustment | Units | Anomaly Transform |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **GDP** | `GDP` | Gross Domestic Product | Quarterly | Seasonally Adjusted Annual Rate (SAAR) | Billions of Dollars | `diff` |
| **CPI** | `CPIAUCSL` | Consumer Price Index for All Urban Consumers | Monthly | Seasonally Adjusted | Index 1982-1984=100 | `pct_change` |
| **PAYEMS** | `PAYEMS` | Total Nonfarm Payroll Employment | Monthly | Seasonally Adjusted | Thousands of Persons | `diff` |
| **UNRATE** | `UNRATE` | Civilian Unemployment Rate | Monthly | Seasonally Adjusted | Percent | `diff` |
| **FEDFUNDS** | `FEDFUNDS` | Federal Funds Effective Rate | Monthly | Not Seasonally Adjusted | Percent | `diff` |

---

## 🏗️ System Architecture & Diagrams

### 1. System Context Diagram

```mermaid
flowchart TD
    subgraph StLouisFed["Federal Reserve Bank of St. Louis"]
        FRED["FRED API\n(Latest Data)"]
        ALFRED["ALFRED API\n(Historical Vintages)"]
    end

    subgraph GitHubActions["GitHub Actions (Ephemeral Runner)"]
        GHJob["collect-data.yml\nDaily 15:00 UTC"]
        ParquetSink["Parquet Snapshots\ndata/snapshots/"]
    end

    subgraph LocalSystem["Local Environment"]
        LocalSync["econ-monitor sync\n(PowerShell / Bash)"]
        Postgres[(PostgreSQL 15+\nAppend-Only Core)]
        FastAPI["FastAPI Backend\n:8000"]
        Streamlit["Streamlit Dashboard\n:8501"]
        Telegram["Telegram Bot\n(Long Polling)"]
    end

    FRED --> GHJob
    ALFRED --> GHJob
    GHJob --> ParquetSink
    ParquetSink -->|git pull| LocalSync
    LocalSync --> Postgres
    Postgres --> FastAPI
    FastAPI --> Streamlit
    LocalSync --> Telegram
```

### 2. Ingestion & Analytics Pipeline Flow

```mermaid
flowchart LR
    A["Harvest / Load\n(ALFRED Vintages)"] --> B["Normalize &\nValidate Rows"]
    B --> C["Append-Only\nDatabase Commit"]
    C --> D["Window Function\nRevision Detection"]
    D --> E["Data Quality\nHealth Scoring (0-100)"]
    E --> F["Anomaly Detection\n(Z-Score & Isolation Forest)"]
    F --> G["Data Events\nGeneration"]
    G --> H["Advisory Locked\nTelegram Dispatcher"]
```

### 3. Entity-Relationship (ER) Diagram

```mermaid
erDiagram
    economic_series ||--o{ observations : tracks
    economic_series ||--o{ revision_history : logs
    economic_series ||--o{ data_events : generates
    economic_series ||--o{ data_quality_logs : audits
    economic_series ||--o{ data_quality_scores : rates
    economic_series ||--o{ subscriber_preferences : targets

    observations ||--o{ observation_vintages : contains
    observations ||--o{ revision_history : undergoes
    observations ||--o{ anomaly_records : triggers

    observation_vintages ||--o{ revision_history : "from / to"
    observation_vintages ||--o{ anomaly_records : flags

    subscribers ||--o{ subscriber_preferences : defines
    subscribers ||--o{ notification_history : receives
    data_events ||--o{ notification_history : dispatches
```

### 4. Local vs. GitHub Architecture Matrix

| Capability / Resource | GitHub Actions Runner | Local / Self-Hosted System |
| :--- | :--- | :--- |
| **Execution Environment** | Ephemeral Ubuntu Linux VM | Persistent Windows, macOS, or Linux |
| **Database Required** | ❌ None (No PostgreSQL) | ✅ PostgreSQL 15+ (psycopg v3) |
| **Required Secrets** | `FRED_API_KEY` | `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| **Primary CLI Command** | `python -m econ_monitor.cli snapshot` | `python -m econ_monitor.cli sync --pull` |
| **Storage Artifact** | Columnar Parquet (`vintages.parquet`) | Relational Tables & SQL Views |
| **Telegram Notifications** | ❌ Disabled (No subscriber data) | ✅ Enabled (Advisory locked dispatcher) |
| **UI Presentation** | ❌ None | ✅ FastAPI (`:8000`) & Streamlit (`:8501`) |

---

## ⚡ Quickstart & Installation

### Prerequisites
- Python 3.11 or higher
- Git
- PostgreSQL 15+ (for local database storage)

### Step 1: Clone Repository & Create Virtual Environment
```bash
git clone https://github.com/shikhar/economic-data-monitoring.git
cd "economic-data-monitoring"

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate
```

### Step 2: Install Package with Dependencies
```bash
pip install --upgrade pip
pip install -e .[dev]
```

### Step 3: Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Edit `.env` to configure your settings:
```ini
DATABASE_URL=postgresql+psycopg://username:password@localhost:5432/econ_monitor
FRED_API_KEY=your_fred_api_key_here
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

### Step 4: Run Migrations and Seed Series
```bash
# Apply database schema migrations
alembic upgrade head

# Seed configured indicators from config/indicators.yaml
python -m econ_monitor.cli seed-series
```

---

## 🛠️ CLI Command Reference

The platform provides a unified CLI tool: `python -m econ_monitor.cli` (or `econ-monitor`).

```bash
# 1. Harvest Parquet snapshots from ALFRED (No database required)
econ-monitor snapshot --series CPI UNRATE

# 2. Ingest directly from FRED/ALFRED into PostgreSQL
econ-monitor ingest --series GDP

# 3. Import Parquet snapshots into PostgreSQL
econ-monitor import-snapshots --dir data/snapshots

# 4. Run automated sync pipeline (git pull -> import -> revisions -> quality -> anomalies -> notify)
econ-monitor sync --pull

# 5. Execute full live API pipeline against FRED
econ-monitor pipeline

# 6. Start Telegram bot long-polling daemon
econ-monitor telegram-poll

# 7. Start FastAPI application server
econ-monitor serve --host 127.0.0.1 --port 8000

# 8. Start Streamlit web dashboard
streamlit run dashboard/app.py
```

---

## 🚀 Automation & Scheduled Jobs

### 1. GitHub Actions (Remote Harvesting)
The workflow `.github/workflows/collect-data.yml` triggers daily at **15:00 UTC** (after standard morning economic releases), harvesting updated vintages and committing Parquet files back to `data/snapshots/`.

### 2. Local Task Scheduling
To pull remote snapshots and run local analytics automatically:

#### Windows (PowerShell & Task Scheduler):
Run the provided wrapper script:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\sync_local.ps1
```
Set up a daily task in Windows Task Scheduler invoking `powershell.exe -File "C:\Path\To\scripts\sync_local.ps1"` at 15:30 UTC.

#### macOS / Linux (Cron):
Run the shell script:
```bash
chmod +x ./scripts/sync_local.sh
./scripts/sync_local.sh
```
Add to `crontab -e`:
```cron
30 15 * * * /path/to/economic-data-monitoring/scripts/sync_local.sh >> /path/to/logs/cron.log 2>&1
```

---

## 🖥️ Streamlit Dashboard Guide

Launch both the backend API and the UI:
```bash
# Terminal 1:
econ-monitor serve

# Terminal 2:
streamlit run dashboard/app.py
```
Open your browser to `http://localhost:8501`.

The dashboard contains 9 specialized analytical modules:
1. **Overview:** System status, key metrics, recent revision activity, and indicator health cards.
2. **Economic Indicators:** Catalog of tracked series with release frequencies, units, and transformation settings.
3. **Historical Observations:** Interactive time series charts with side-by-side vintage depth drill-downs.
4. **Revision History:** Comprehensive revision audit log with directional filtering and magnitude charts.
5. **Revision Analytics:** Mean absolute revision, directional bias statistics, lag distributions, and publication heatmaps.
6. **Anomaly Detection:** Outlier records flagged by rolling Z-scores and Isolation Forests with status review controls.
7. **Data Quality:** Real-time health scores (0–100) and severity breakdowns across 7 data validation checks.
8. **Subscriber Management:** Interactive management of Telegram users and alerting preferences.
9. **Notification History:** Complete audit trail of dispatched alert messages and delivery statuses.

*(See `docs/SCREENSHOTS.md` for page specifications and capture guidelines).*

---

## 🛡️ Data Semantics & Quality Assurance

- **Exact Numeric Representation:** All observation and vintage values use `Numeric(24, 8)` to prevent floating-point rounding errors.
- **Strict Immutability:** Historical vintage rows are append-only. Existing vintages are never updated or deleted.
- **Idempotency:** Re-running snapshot ingestion or revision detection creates 0 new rows and 0 duplicate alerts.
- **Decoupled Architecture:** Streamlit never connects to the database directly; all data passes through FastAPI schemas.

---

## 🔍 Troubleshooting & FAQ

**Q: Why does `alembic check` fail?**  
A: `alembic check` requires a live database connection. Ensure `DATABASE_URL` is set in your `.env` file before executing migration commands.

**Q: What if I don't have a FRED API key?**  
A: You can register for a free key at [stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html). Offline unit tests mock all HTTP requests and run without a key.

**Q: How do Telegram notifications avoid spamming?**  
A: The notification dispatcher uses PostgreSQL advisory locks to prevent concurrent execution, and checks unique database constraints `(subscriber_id, event_id, channel)` before dispatching. Backfill events are automatically suppressed.
