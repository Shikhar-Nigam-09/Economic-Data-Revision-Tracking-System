"""Revision History page: filterable revision events and drilldown."""

from datetime import datetime

import streamlit as st

from dashboard.api_client import ApiClient, ApiClientError
from dashboard.components import render_empty_state, render_error_state, render_page_header

api = ApiClient()

render_page_header(
    title="Revision History & Audit Trail",
    description="Macroeconomic data revisions across releases, with direction and lag tracking.",
    last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
)

try:
    series_list = api.list_series(active_only=True)
    series_codes = ["All"] + [s["code"] for s in series_list]

    # Filters
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        sel_series = st.selectbox("Indicator", series_codes, index=0)
    with c2:
        sel_dir = st.selectbox("Direction", ["All", "up", "down"], index=0)
    with c3:
        sel_type = st.selectbox(
            "Revision Type",
            ["All", "value_revision", "became_available", "became_missing"],
            index=0,
        )
    with c4:
        min_pct = st.number_input("Min Relative Revision (%)", min_value=0.0, value=0.0, step=0.1)

    s_param = None if sel_series == "All" else sel_series
    d_param = None if sel_dir == "All" else sel_dir
    t_param = None if sel_type == "All" else sel_type
    p_param = None if min_pct <= 0 else min_pct

    rev_resp = api.list_revisions(
        series=s_param,
        direction=d_param,
        revision_type=t_param,
        min_abs_pct=p_param,
        limit=200,
    )
    rev_items = rev_resp.get("items", [])

    if not rev_items:
        render_empty_state(
            title="No revision events found",
            message="No revisions match the specified filters or none detected yet.",
            suggested_command="python -m econ_monitor.cli detect-revisions",
        )
    else:
        tot_cnt = rev_resp.get("total", len(rev_items))
        st.markdown(f"**Found {tot_cnt} revision events (showing first {len(rev_items)}):**")

        table_rows = []
        for r in rev_items:
            table_rows.append(
                {
                    "ID": r.get("id"),
                    "Series": r.get("series_code"),
                    "Obs Date": r.get("observation_date"),
                    "Revision Date": r.get("revision_date"),
                    "Prev Value": r.get("previous_value"),
                    "New Value": r.get("new_value"),
                    "Abs Change": r.get("absolute_revision"),
                    "Rel Change (%)": f"{r.get('relative_revision_pct')}%"
                    if r.get("relative_revision_pct") is not None
                    else "—",
                    "Direction": r.get("direction") or "—",
                    "Type": r.get("revision_type"),
                    "Lag (Days)": r.get("revision_lag_days"),
                }
            )

        st.dataframe(table_rows, use_container_width=True, hide_index=True)

        # Single revision inspector
        st.markdown("### 🔎 Single Revision Inspector")
        rev_ids = [r["id"] for r in rev_items]
        target_rev_id = st.selectbox("Select Revision ID", rev_ids)
        if target_rev_id:
            single = api.get_revision(target_rev_id)
            c_a, c_b = st.columns(2)
            with c_a:
                st.write(f"**Series:** `{single.get('series_code')}`")
                st.write(f"**Observation Date:** `{single.get('observation_date')}`")
                st.write(f"**Revision Date:** `{single.get('revision_date')}`")
                st.write(f"**Revision Number:** `{single.get('revision_number')}`")
            with c_b:
                st.write(f"**Previous Value:** `{single.get('previous_value')}`")
                st.write(f"**New Value:** `{single.get('new_value')}`")
                st.write(f"**Absolute Revision:** `{single.get('absolute_revision')}`")
                st.write(f"**Revision Lag:** `{single.get('revision_lag_days')} days`")

except ApiClientError as exc:
    render_error_state(exc.message, exc.code)
