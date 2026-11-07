"""Revision Analytics page: summary KPIs, histograms, and first vs latest comparison."""

from datetime import datetime

import streamlit as st

from dashboard.api_client import ApiClient, ApiClientError
from dashboard.charts import (
    create_direction_donut_chart,
    create_lag_bucket_chart,
    create_period_revisions_bar_chart,
    create_revision_magnitude_histogram,
)
from dashboard.components import (
    render_empty_state,
    render_error_state,
    render_metric_cards,
    render_page_header,
)

api = ApiClient()

# Header with dynamic filter banner
render_page_header(
    title="Revision Analytics & Statistical Insights",
    description="Analyze revision magnitudes, lag distribution, and initial release stability.",
    last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
)

try:
    series_list = api.list_series(active_only=True)
    if not series_list:
        render_empty_state(
            title="No indicators found",
            message="Please ingest data before running analytics.",
            suggested_command="python -m econ_monitor.cli ingest",
        )
        st.stop()

    series_codes = [s["code"] for s in series_list]

    top_c1, top_c2 = st.columns([2, 2])
    with top_c1:
        selected_code = st.selectbox("Select Economic Indicator", series_codes, index=0)
    with top_c2:
        true_initial_only = st.checkbox(
            "True Initial Release Only (A6.6 Filter)",
            value=True,
            help="Excludes historical backfill where initial vintage arrived after long lag.",
        )

    # 1. Revision Summary KPI Cards
    summary = api.get_revision_summary(
        series=selected_code,
        true_initial_release_only=true_initial_only,
    )
    unit_label = summary.get("change_unit_label", "")

    mean_abs = summary.get("mean_absolute_revision")
    mean_rel = summary.get("mean_relative_revision_pct")
    avg_per_obs = summary.get("average_revisions_per_observation", 0.0)

    metrics = [
        {"label": "Observations Evaluated", "value": str(summary.get("total_observations", 0))},
        {"label": "Observations Revised", "value": str(summary.get("observations_revised", 0))},
        {
            "label": f"Mean Absolute Rev ({unit_label})",
            "value": f"{mean_abs:.4f}" if mean_abs is not None else "—",
        },
        {
            "label": "Mean Relative Rev (%)",
            "value": f"{mean_rel:.2f}%" if mean_rel is not None else "—",
        },
        {"label": "Avg Revs / Observation", "value": f"{avg_per_obs:.2f}"},
    ]
    render_metric_cards(metrics)

    # 2. Charts Row: Direction Donut & Magnitude Histogram
    c_chart1, c_chart2 = st.columns(2)
    with c_chart1:
        d_counts = api.get_direction_counts(
            series=selected_code,
            true_initial_release_only=true_initial_only,
        )
        donut_fig = create_direction_donut_chart(
            upward=d_counts.get("upward", 0),
            downward=d_counts.get("downward", 0),
            became_available=d_counts.get("became_available", 0),
            became_missing=d_counts.get("became_missing", 0),
        )
        st.plotly_chart(donut_fig, use_container_width=True)

    with c_chart2:
        mag_dist = api.get_magnitude_distribution(
            series=selected_code,
            num_bins=10,
            true_initial_release_only=true_initial_only,
        )
        hist_fig = create_revision_magnitude_histogram(
            bins=mag_dist.get("bins", []),
            unit_label=unit_label,
        )
        st.plotly_chart(hist_fig, use_container_width=True)

    # 3. Charts Row: Lag Buckets & Period Revision Trends
    c_chart3, c_chart4 = st.columns(2)
    with c_chart3:
        lag_data = api.get_revisions_by_lag(
            series=selected_code,
            true_initial_release_only=true_initial_only,
        )
        lag_fig = create_lag_bucket_chart(lag_metrics=lag_data.get("items", []))
        st.plotly_chart(lag_fig, use_container_width=True)

    with c_chart4:
        period_data = api.get_revisions_by_period(
            series=selected_code,
            true_initial_release_only=true_initial_only,
        )
        period_fig = create_period_revisions_bar_chart(
            period_metrics=period_data.get("items", []),
            unit_label=unit_label,
        )
        st.plotly_chart(period_fig, use_container_width=True)

    # 4. First Release vs Latest Known Vintage Comparison
    st.markdown("### ⚖️ First Release vs. Latest Known Vintage (Directional Bias)")
    f_vs_l = api.get_first_vs_latest(
        series=selected_code,
        true_initial_release_only=true_initial_only,
    )

    f_col1, f_col2, f_col3, f_col4 = st.columns(4)
    with f_col1:
        st.metric("Compared Observations", f_vs_l.get("observations_compared", 0))
    with f_col2:
        st.metric("Upward Revised (%)", f"{f_vs_l.get('share_revised_upward_pct', 0.0):.1f}%")
    with f_col3:
        st.metric("Downward Revised (%)", f"{f_vs_l.get('share_revised_downward_pct', 0.0):.1f}%")
    with f_col4:
        st.metric("Unchanged (%)", f"{f_vs_l.get('share_unchanged_pct', 0.0):.1f}%")

    mean_signed_abs = f_vs_l.get("mean_signed_absolute_revision")
    mean_signed_rel = f_vs_l.get("mean_signed_relative_revision_pct")
    st.caption(
        f"Mean Signed Revision: `{mean_signed_abs or '—'} {unit_label}` | "
        f"Mean Signed Relative Revision: `{mean_signed_rel or '—'}%`"
    )

except ApiClientError as exc:
    render_error_state(exc.message, exc.code)
