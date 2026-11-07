"""Economic Indicators catalog page with configuration synchronization."""

from datetime import datetime

import streamlit as st

from dashboard.api_client import ApiClient, ApiClientError
from dashboard.components import render_empty_state, render_error_state, render_page_header

api = ApiClient()

render_page_header(
    title="Economic Indicators Catalog",
    description="Configuration metadata, reporting frequencies, and sync operations.",
    last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
)

cols = st.columns([3, 1])
with cols[0]:
    active_only = st.checkbox("Show Active Indicators Only", value=True)
with cols[1]:
    if st.button("🔄 Sync with indicators.yaml", use_container_width=True):
        try:
            res = api.sync_series_config()
            st.success(f"Configuration synchronized: {res.get('message')}")
            st.rerun()
        except ApiClientError as exc:
            render_error_state(exc.message, exc.code)

try:
    series_list = api.list_series(active_only=active_only)
    if not series_list:
        render_empty_state(
            title="No economic series available",
            message="No series found matching current filter.",
            suggested_command="python -m econ_monitor.cli sync-indicators-config",
        )
    else:
        st.markdown(f"**Found {len(series_list)} indicators:**")
        for s in series_list:
            with st.expander(f"📌 **{s.get('code')}** — {s.get('title')}", expanded=False):
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.write(f"**FRED Series ID:** `{s.get('fred_series_id')}`")
                    st.write(f"**Frequency:** `{s.get('frequency')}`")
                    st.write(f"**Seasonal Adj:** `{s.get('seasonal_adjustment')}`")
                with c2:
                    st.write(f"**Units:** {s.get('units')}")
                    st.write(
                        f"**Change Unit Label:** `{s.get('change_unit_label') or s.get('units')}`"
                    )
                    st.write(f"**Anomaly Transform:** `{s.get('anomaly_transform')}`")
                with c3:
                    st.write(
                        f"**Expected Range:** `[{s.get('expected_min')}, {s.get('expected_max')}]`"
                    )
                    st.write(f"**Initial Lag:** `{s.get('initial_release_max_lag_days')} days`")
                    st.write(f"**Max Stale Lag:** `{s.get('max_stale_lag_days')} days`")

                st.caption(
                    f"History starts: `{s.get('history_start')}` | "
                    f"FRED Last Updated: `{s.get('fred_last_updated') or 'Never'}` | "
                    f"Status: `{'Active' if s.get('is_active') else 'Inactive'}`"
                )

except ApiClientError as exc:
    render_error_state(exc.message, exc.code)
