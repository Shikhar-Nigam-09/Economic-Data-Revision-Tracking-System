"""Data Quality page: health score KPIs, component breakdowns, and check execution logs."""

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
    title="Data Quality & Integrity Monitoring",
    description="Multi-factor health scoring across completeness, validity, and timeliness.",
    last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
)

try:
    cols = st.columns([3, 1])
    with cols[1]:
        if st.button("🧪 Run Quality Checks", type="primary", use_container_width=True):
            try:
                res = api.trigger_quality_run()
                tot_eval = res.get("total_checks_evaluated")
                st.success(f"Quality checks evaluated: {tot_eval} checks across indicators.")
                st.rerun()
            except ApiClientError as exc:
                render_error_state(exc.message, exc.code)

    # 1. Latest Health Scores per Indicator
    latest_scores = api.get_latest_quality_scores()

    st.markdown("### 🏆 Indicator Health Scores")
    if not latest_scores:
        render_empty_state(
            title="No health scores computed yet",
            message="Run quality checks to compute the initial health scores.",
            suggested_command="python -m econ_monitor.cli check-quality",
        )
    else:
        score_cards = []
        for s in latest_scores:
            score_cards.append(
                {
                    "label": f"{s.get('series_code')} Health",
                    "value": f"{s.get('score')}/100",
                }
            )
        render_metric_cards(score_cards)

        # Health Component Breakdown Table
        st.markdown("#### 🔍 Component Score Breakdown (0–100)")
        breakdown_rows = []
        for s in latest_scores:
            components = s.get("component_scores", {})
            breakdown_rows.append(
                {
                    "Indicator": s.get("series_code"),
                    "Composite Score": f"{s.get('score')}/100",
                    "Completeness (25)": components.get("completeness", "—"),
                    "Validity (20)": components.get("validity", "—"),
                    "Timeliness (20)": components.get("timeliness", "—"),
                    "Consistency (15)": components.get("frequency_consistency", "—"),
                    "Uniqueness (10)": components.get("uniqueness", "—"),
                    "Schema (10)": components.get("schema_conformance", "—"),
                    "Evaluated At": s.get("computed_at"),
                }
            )
        st.dataframe(breakdown_rows, use_container_width=True, hide_index=True)

    # 2. Detailed Quality Check Logs
    st.markdown("### 📋 Quality Check Execution Logs")
    f_c1, f_c2, f_c3 = st.columns(3)
    with f_c1:
        f_severity = st.selectbox("Severity", ["All", "info", "warning", "error"], index=0)
    with f_c2:
        f_passed = st.selectbox("Status", ["All", "Passed Only", "Failed Only"], index=0)
    with f_c3:
        f_limit = st.slider("Max Logs", min_value=50, max_value=500, value=100, step=50)

    sev_param = None if f_severity == "All" else f_severity
    passed_param = None
    if f_passed == "Passed Only":
        passed_param = True
    elif f_passed == "Failed Only":
        passed_param = False

    logs_resp = api.get_quality_logs(
        severity=sev_param,
        passed=passed_param,
        limit=f_limit,
    )
    logs = logs_resp.get("items", [])

    if not logs:
        st.info("No logs match the selected filter.")
    else:
        st.markdown(
            f"**Showing {len(logs)} logs (out of {logs_resp.get('total', len(logs))} total):**"
        )
        log_table = []
        for log_item in logs:
            status_icon = (
                "✅ Passed" if log_item.get("passed") else f"❌ Failed ({log_item.get('severity')})"
            )
            log_table.append(
                {
                    "Checked At": log_item.get("checked_at"),
                    "Indicator": log_item.get("series_code"),
                    "Check Name": log_item.get("check_name"),
                    "Status": status_icon,
                    "Affected Records": log_item.get("affected_count", 0),
                    "Message": log_item.get("message"),
                }
            )
        st.dataframe(log_table, use_container_width=True, hide_index=True)

except ApiClientError as exc:
    render_error_state(exc.message, exc.code)
