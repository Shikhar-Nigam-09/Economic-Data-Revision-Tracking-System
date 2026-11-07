"""Overview page: System status, tracked indicators summary, and quick pipeline trigger."""

from datetime import datetime

import streamlit as st

from dashboard.api_client import ApiClient, ApiClientError
from dashboard.components import (
    render_empty_state,
    render_error_state,
    render_metric_cards,
    render_page_header,
)

api = ApiClient()

render_page_header(
    title="Macroeconomic System Overview",
    description="Real-time status of monitored economic indicators and health scores.",
    last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
)

# 1. System Health & Connectivity Check
health = api.get_health()
db_status = health.get("database", "disconnected")
is_healthy = health.get("status") == "ok" and db_status == "connected"

cols = st.columns([3, 1])
with cols[0]:
    if is_healthy:
        st.success(f"🟢 **API Backend Healthy** | Database Connection: `{db_status}`")
    else:
        st.error(f"🔴 **API / Database Degraded** | Database Connection: `{db_status}`")
with cols[1]:
    if st.button("🔄 Refresh Data", use_container_width=True):
        st.rerun()

# 2. Key Metrics Row
try:
    series_list = api.list_series(active_only=True)
    rev_resp = api.list_revisions(limit=1)
    anomaly_status = api.get_anomalies_status()
    quality_scores = api.get_latest_quality_scores()

    avg_health_score = (
        round(sum(float(s.get("score", 0)) for s in quality_scores) / len(quality_scores), 1)
        if quality_scores
        else "N/A"
    )

    metrics = [
        {"label": "Active Indicators", "value": str(len(series_list))},
        {"label": "Total Revisions Tracked", "value": str(rev_resp.get("total", 0))},
        {"label": "Total Flagged Anomalies", "value": str(anomaly_status.get("total_flagged", 0))},
        {
            "label": "Avg Health Score",
            "value": f"{avg_health_score}/100" if avg_health_score != "N/A" else "—",
        },
    ]
    render_metric_cards(metrics)

    st.markdown("### 📊 Tracked Economic Indicators")
    if not series_list:
        render_empty_state(
            title="No tracked indicators found in database",
            message="Indicators must be seeded into the database.",
            suggested_command="python -m econ_monitor.cli seed-series",
        )
    else:
        indicators_table = []
        for s in series_list:
            indicators_table.append(
                {
                    "Code": s.get("code"),
                    "FRED Series ID": s.get("fred_series_id"),
                    "Title": s.get("title"),
                    "Frequency": s.get("frequency"),
                    "Units": s.get("units"),
                    "Seasonal Adj.": s.get("seasonal_adjustment"),
                    "Expected Range": f"[{s.get('expected_min')}, {s.get('expected_max')}]",
                    "FRED Last Updated": s.get("fred_last_updated") or "Never",
                }
            )
        st.dataframe(indicators_table, use_container_width=True, hide_index=True)

    # 3. Pipeline Ingestion Trigger Section
    st.markdown("### ⚡ Pipeline Operations")
    st.markdown("Trigger an on-demand data sync and analysis pipeline run.")

    pipe_cols = st.columns([1, 1, 2])
    with pipe_cols[0]:
        pipe_mode = st.selectbox("Ingestion Mode", ["api", "snapshot"], index=0)
    with pipe_cols[1]:
        notify_toggle = st.checkbox("Dispatch Telegram Alerts", value=True)

    if st.button("🚀 Run Ingestion Pipeline", type="primary"):
        try:
            res = api.trigger_ingestion(mode=pipe_mode, notify=notify_toggle)
            st.success(f"Pipeline scheduled successfully: {res.get('message')}")
        except ApiClientError as exc:
            if exc.status_code == 409:
                st.warning("⚠️ An ingestion pipeline run is already in progress. Please wait.")
            else:
                render_error_state(exc.message, exc.code)

except ApiClientError as exc:
    render_error_state(exc.message, exc.code)
