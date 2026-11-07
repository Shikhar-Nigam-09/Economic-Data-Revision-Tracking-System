"""Anomaly Detection page: inspection, review status, and manual execution."""

from datetime import datetime

import streamlit as st

from dashboard.api_client import ApiClient, ApiClientError
from dashboard.components import (
    render_empty_state,
    render_error_state,
    render_page_header,
)

api = ApiClient()

render_page_header(
    title="Anomaly Detection & Outlier Review",
    description="Statistical z-score and Isolation Forest outlier detection with reviewer notes.",
    last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
)

try:
    series_list = api.list_series(active_only=True)
    series_codes = ["All"] + [s["code"] for s in series_list]

    # Filters and Trigger Row
    c1, c2, c3, c4 = st.columns([1.5, 1.5, 1.5, 1.5])
    with c1:
        sel_series = st.selectbox("Indicator", series_codes, index=0)
    with c2:
        sel_method = st.selectbox(
            "Detection Method", ["All", "zscore", "isolation_forest"], index=0
        )
    with c3:
        sel_status = st.selectbox(
            "Review Status", ["All", "flagged", "reviewed", "dismissed"], index=0
        )
    with c4:
        if st.button("🚀 Run Anomaly Detection", type="primary", use_container_width=True):
            try:
                run_res = api.trigger_anomaly_run()
                st.success(
                    f"Execution finished: {run_res.get('total_flagged')} flagged, "
                    f"{run_res.get('total_new_records')} new records."
                )
                st.rerun()
            except ApiClientError as exc:
                render_error_state(exc.message, exc.code)

    s_param = None if sel_series == "All" else sel_series
    m_param = None if sel_method == "All" else sel_method
    st_param = None if sel_status == "All" else sel_status

    anom_resp = api.list_anomalies(
        series=s_param,
        method=m_param,
        status=st_param,
        limit=200,
    )
    items = anom_resp.get("items", [])

    if not items:
        render_empty_state(
            title="No anomaly records found",
            message="No flagged observations match your current filter.",
            suggested_command="python -m econ_monitor.cli detect-anomalies",
        )
    else:
        st.markdown(f"**Found {anom_resp.get('total', len(items))} anomaly records:**")

        table_rows = []
        for a in items:
            table_rows.append(
                {
                    "ID": a.get("id"),
                    "Series": a.get("series_code"),
                    "Obs Date": a.get("observation_date"),
                    "Vintage Date": a.get("vintage_date"),
                    "Reported Value": a.get("vintage_value"),
                    "Method": a.get("method"),
                    "Score": round(float(a.get("score")), 3) if a.get("score") is not None else "—",
                    "Status": a.get("status"),
                    "Superseded Vintage?": "⚠️ Yes (Superseded)"
                    if a.get("is_superseded_vintage")
                    else "✅ Current",
                    "Reviewer Note": a.get("note") or "—",
                    "Detected At": a.get("detected_at"),
                }
            )

        st.dataframe(table_rows, use_container_width=True, hide_index=True)

        # Anomaly Status Review Form
        st.markdown("### ✍️ Review & Update Anomaly Record")
        anom_ids = [a["id"] for a in items]
        target_id = st.selectbox("Select Anomaly Record ID", anom_ids)

        if target_id:
            curr_target = next(a for a in items if a["id"] == target_id)
            with st.form(f"review_form_{target_id}"):
                st.write(
                    f"Updating Record #{target_id} for **{curr_target['series_code']}** "
                    f"({curr_target['observation_date']})"
                )
                new_status = st.selectbox(
                    "Set New Status",
                    ["flagged", "reviewed", "dismissed"],
                    index=["flagged", "reviewed", "dismissed"].index(curr_target["status"]),
                )
                new_note = st.text_input("Reviewer Note", value=curr_target.get("note") or "")
                submitted = st.form_submit_button("Save Status Update")

                if submitted:
                    try:
                        updated = api.patch_anomaly(
                            anomaly_id=target_id,
                            status=new_status,
                            note=new_note,
                        )
                        st.success(f"Record #{target_id} updated to '{updated.get('status')}'.")
                        st.rerun()
                    except ApiClientError as exc:
                        render_error_state(exc.message, exc.code)

except ApiClientError as exc:
    render_error_state(exc.message, exc.code)
