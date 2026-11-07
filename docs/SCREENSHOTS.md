# Economic Data Monitoring Dashboard — Screenshots and Capture Guide

This guide describes each of the nine pages in the Economic Data Monitoring Streamlit dashboard and provides instructions for capturing and replacing the screenshot placeholders in `docs/screenshots/`.

> **Notice:** The screenshot files in this directory are intentionally documented as placeholders awaiting live database credentials and populated production data. Per the project requirements, fake or fabricated screenshots have not been generated.

---

## How to Run the Dashboard for Screenshot Capture

Once you have configured `DATABASE_URL` in your `.env` file and seeded data, launch both the backend API and the Streamlit dashboard:

```bash
# Terminal 1: Start FastAPI backend
python -m econ_monitor.cli serve

# Terminal 2: Start Streamlit dashboard
python -m streamlit run dashboard/app.py
```

The dashboard will open at `http://localhost:8501`.

---

## Screenshot Inventory and Page Specifications

### 1. Overview (`01_overview.png`)
- **Page Route:** `dashboard/pages/1_Overview.py` (Navigation: *Overview*)
- **Visual Elements to Capture:**
  - KPI summary metrics: Active Series, Tracked Observations, Recorded Revisions, Active Subscribers.
  - Recent Revision Activity metric cards with direction badges (`🟢 UP`, `🔴 DOWN`).
  - Latest Data Quality Health score indicators per indicator.
  - System status indicators: API health, Database connectivity, and Pipeline execution readiness.
- **Recommended Browser Viewport:** 1920 × 1080, scrolled to display both KPI cards and recent revision table.

### 2. Economic Indicators (`02_indicators.png`)
- **Page Route:** `dashboard/pages/2_Economic_Indicators.py` (Navigation: *Economic Indicators*)
- **Visual Elements to Capture:**
  - Indicator catalog table displaying the 5 core series (`GDP`, `CPI`, `PAYEMS`, `UNRATE`, `FEDFUNDS`).
  - Metadata columns: FRED Series ID, Frequency, Units, Seasonal Adjustment status, and History Start date.
  - Detail expander showing anomaly transformation config (`diff` / `pct_change`) and expected min/max boundaries.

### 3. Historical Observations (`03_observations.png`)
- **Page Route:** `dashboard/pages/3_Historical_Observations.py` (Navigation: *Historical Observations*)
- **Visual Elements to Capture:**
  - Indicator selector (e.g., select `CPI` or `PAYEMS`) and observation date range slider.
  - Latest Observation Values interactive Plotly line chart.
  - Vintage depth inspection table: expanding an observation date showing all historical vintages side by side.

### 4. Revision History (`04_revisions.png`)
- **Page Route:** `dashboard/pages/4_Revision_History.py` (Navigation: *Revision History*)
- **Visual Elements to Capture:**
  - Revision log table with filters for Indicator, Date Range, Direction (`UP`, `DOWN`), and Revision Type.
  - Columns showing Observation Date, Revision Date, Previous Value, New Value, Absolute Revision, Relative %, and Lag (days).
  - Magnitude visualization comparing upward versus downward revisions over time.

### 5. Revision Analytics (`05_analytics.png`)
- **Page Route:** `dashboard/pages/5_Revision_Analytics.py` (Navigation: *Revision Analytics*)
- **Visual Elements to Capture:**
  - Summary metrics: Mean Absolute Revision, Mean Relative Revision %, Mean Revision Lag, and Total Revisions.
  - Directional Bias indicator (percentage of upward vs. downward revisions).
  - Revision Heatmap matrix: Observation Period on the vertical axis vs. Release Vintage on the horizontal axis.
  - Revision lag distribution histogram.

### 6. Anomaly Detection (`06_anomalies.png`)
- **Page Route:** `dashboard/pages/6_Anomaly_Detection.py` (Navigation: *Anomaly Detection*)
- **Visual Elements to Capture:**
  - Flagged anomaly records table with indicator, observation date, method (`zscore` / `isolation_forest`), score, and threshold.
  - Time series chart with flagged anomaly data points highlighted as distinct markers.
  - Status review toggle (`flagged`, `reviewed`, `dismissed`).

### 7. Data Quality (`07_quality.png`)
- **Page Route:** `dashboard/pages/7_Data_Quality.py` (Navigation: *Data Quality*)
- **Visual Elements to Capture:**
  - Indicator health score gauge / progress cards (0–100 score).
  - Check breakdown table: Missing Values, Duplicate Observations, Future Observation Dates, Expected Range, Monotonic Vintages, Ingestion Recency, and Anomaly Spike checks.
  - Severity status badges (`PASS`, `WARNING`, `ERROR`).

### 8. Subscriber Management (`08_subscribers.png`)
- **Page Route:** `dashboard/pages/8_Subscriber_Management.py` (Navigation: *Subscriber Management*)
- **Visual Elements to Capture:**
  - Active Telegram subscribers table (Chat ID, Username, Display Name, Active Status).
  - Notification preference checkboxes per subscriber: New Observations, Revisions, Anomalies, Quality Issues, and Minimum Revision % Threshold.
  - Add / Deactivate subscriber controls.

### 9. Notification History (`09_notifications.png`)
- **Page Route:** `dashboard/pages/9_Notification_History.py` (Navigation: *Notification History*)
- **Visual Elements to Capture:**
  - Notification delivery log table showing Timestamp, Subscriber, Channel (`telegram`), Status (`sent`, `failed`, `pending`, `skipped`), and Attempts.
  - Message preview expander showing rendered Telegram Markdown alerts.
  - Filter by delivery status and indicator.

---

## Directory Setup

The `docs/screenshots/` folder is reserved for saving these 9 files:

```
docs/screenshots/
├── 01_overview.png
├── 02_indicators.png
├── 03_observations.png
├── 04_revisions.png
├── 05_analytics.png
├── 06_anomalies.png
├── 07_quality.png
├── 08_subscribers.png
└── 09_notifications.png
```
