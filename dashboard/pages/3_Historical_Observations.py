"""Historical Observations page: interactive line charts and vintage fan stair-step charts."""

from datetime import datetime

import streamlit as st

from dashboard.api_client import ApiClient, ApiClientError
from dashboard.charts import create_observation_chart, create_vintage_fan_chart
from dashboard.components import render_empty_state, render_error_state, render_page_header

api = ApiClient()

render_page_header(
    title="Historical Observations & Vintages",
    description="Inspect macroeconomic time series across latest, first, and as-of views.",
    last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
)

try:
    series_list = api.list_series(active_only=True)
    if not series_list:
        render_empty_state(
            title="No indicators found",
            message="Please ingest indicator data first.",
            suggested_command="python -m econ_monitor.cli ingest",
        )
        st.stop()

    series_map = {s["code"]: s for s in series_list}
    series_codes = list(series_map.keys())

    # Control bar
    c1, c2, c3, c4 = st.columns([1.5, 1.5, 1.5, 1.5])
    with c1:
        selected_code = st.selectbox("Select Indicator", series_codes, index=0)
    with c2:
        view_mode = st.selectbox(
            "Vintage View Mode",
            ["latest", "first", "as_of"],
            index=0,
            help="latest: current knowledge; first: initial release; as_of: snapshot date",
        )
    with c3:
        as_of_val = None
        if view_mode == "as_of":
            as_of_val = st.date_input("As-Of Date", value=datetime.today()).isoformat()
        else:
            st.write("")
    with c4:
        limit_val = st.slider("Max Observations", min_value=50, max_value=1000, value=500, step=50)

    selected_series = series_map[selected_code]
    unit_label = selected_series.get("units", "")

    # Fetch observations
    obs_resp = api.get_observations(
        code=selected_code,
        view=view_mode,
        as_of=as_of_val,
        limit=limit_val,
    )
    obs_items = obs_resp.get("items", [])

    if not obs_items:
        render_empty_state(
            title=f"No observations for {selected_code}",
            message=f"No records found under view='{view_mode}'.",
            suggested_command=f"python -m econ_monitor.cli ingest --series {selected_code}",
        )
    else:
        st.markdown(f"**Found {obs_resp.get('total', len(obs_items))} observations:**")
        fig = create_observation_chart(
            observations=obs_items,
            series_code=selected_code,
            unit_label=unit_label,
            view_mode=view_mode,
        )
        st.plotly_chart(fig, use_container_width=True)

        # Vintage Revision Drilldown
        st.markdown("### 🔍 Observation Vintage Drilldown")
        st.markdown("Select a specific observation date to inspect its historical revision chain.")

        obs_dates = [o["observation_date"] for o in obs_items]
        selected_obs_date = st.selectbox("Observation Date", obs_dates, index=len(obs_dates) - 1)

        target_obs = next(
            (o for o in obs_items if o["observation_date"] == selected_obs_date), None
        )
        if target_obs:
            obs_id = target_obs["observation_id"]
            vint_resp = api.get_observation_vintages(observation_id=obs_id)
            vintages = vint_resp.get("items", [])

            col_v1, col_v2 = st.columns([2, 1])
            with col_v1:
                fan_fig = create_vintage_fan_chart(
                    observation_date=selected_obs_date,
                    vintages=vintages,
                    series_code=selected_code,
                    unit_label=unit_label,
                )
                st.plotly_chart(fan_fig, use_container_width=True)

            with col_v2:
                st.markdown(f"**Total Vintages Published:** `{len(vintages)}`")
                if vintages:
                    st.dataframe(
                        [
                            {
                                "Vintage Date": v["vintage_date"],
                                f"Value ({unit_label})": v["value"],
                                "Missing": v["is_missing"],
                            }
                            for v in vintages
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )

except ApiClientError as exc:
    render_error_state(exc.message, exc.code)
