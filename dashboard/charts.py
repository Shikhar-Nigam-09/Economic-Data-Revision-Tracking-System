"""Plotly visualization builders for economic observations, revisions, and anomalies."""

from typing import Any, Dict, List

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


def create_observation_chart(
    observations: List[Dict[str, Any]],
    series_code: str,
    unit_label: str = "",
    view_mode: str = "latest",
) -> go.Figure:
    """Create a line chart with markers showing observation history."""
    if not observations:
        fig = go.Figure()
        fig.update_layout(
            title=f"{series_code} — No observation data available",
            xaxis_title="Date",
            yaxis_title=unit_label,
            template="plotly_white",
        )
        return fig

    df = pd.DataFrame(observations)
    df["observation_date"] = pd.to_datetime(df["observation_date"])
    df = df.sort_values("observation_date")

    fig = px.line(
        df,
        x="observation_date",
        y="value",
        markers=True,
        title=f"{series_code} Historical Observations ({view_mode.capitalize()} View)",
        labels={"observation_date": "Observation Date", "value": f"Value ({unit_label})"},
        template="plotly_white",
    )
    fig.update_traces(line=dict(width=2, color="#1f77b4"), marker=dict(size=6))
    fig.update_layout(hovermode="x unified", margin=dict(l=40, r=40, t=50, b=40))
    return fig


def create_vintage_fan_chart(
    observation_date: str,
    vintages: List[Dict[str, Any]],
    series_code: str = "",
    unit_label: str = "",
) -> go.Figure:
    """Create a stair-step chart showing historical revisions to a specific observation."""
    if not vintages:
        fig = go.Figure()
        fig.update_layout(
            title=f"Observation {observation_date} — No vintages recorded",
            template="plotly_white",
        )
        return fig

    df = pd.DataFrame(vintages)
    df["vintage_date"] = pd.to_datetime(df["vintage_date"])
    df = df.sort_values("vintage_date")

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["vintage_date"],
            y=df["value"],
            mode="lines+markers",
            line=dict(shape="hv", width=2, color="#2ca02c"),
            marker=dict(size=8, color="#d62728"),
            name="Published Vintage",
        )
    )

    fig.update_layout(
        title=f"{series_code} — Revision History for Period {observation_date}",
        xaxis_title="Vintage Release Date",
        yaxis_title=f"Reported Value ({unit_label})",
        template="plotly_white",
        hovermode="x unified",
        margin=dict(l=40, r=40, t=50, b=40),
    )
    return fig


def create_revision_magnitude_histogram(
    bins: List[Dict[str, Any]],
    unit_label: str = "",
) -> go.Figure:
    """Create a bar chart showing absolute revision magnitude distribution."""
    if not bins:
        fig = go.Figure()
        fig.update_layout(title="No revision magnitude data", template="plotly_white")
        return fig

    labels = [f"[{b['bin_start']:.2f}, {b['bin_end']:.2f})" for b in bins]
    counts = [b["count"] for b in bins]

    fig = go.Figure(
        data=[
            go.Bar(
                x=labels,
                y=counts,
                marker_color="#ff7f0e",
                text=counts,
                textposition="auto",
            )
        ]
    )
    fig.update_layout(
        title="Revision Magnitude Distribution",
        xaxis_title=f"Absolute Revision ({unit_label})",
        yaxis_title="Count of Revisions",
        template="plotly_white",
        margin=dict(l=40, r=40, t=50, b=40),
    )
    return fig


def create_lag_bucket_chart(lag_metrics: List[Dict[str, Any]]) -> go.Figure:
    """Create a bar chart showing revisions by lag buckets."""
    if not lag_metrics:
        fig = go.Figure()
        fig.update_layout(title="No revision lag data", template="plotly_white")
        return fig

    labels = [m["bucket_label"] for m in lag_metrics]
    counts = [m["revision_count"] for m in lag_metrics]
    pcts = [m.get("percentage_of_total", 0.0) for m in lag_metrics]

    fig = go.Figure(
        data=[
            go.Bar(
                x=labels,
                y=counts,
                marker_color="#9467bd",
                text=[f"{c} ({p:.1f}%)" for c, p in zip(counts, pcts, strict=False)],
                textposition="auto",
            )
        ]
    )
    fig.update_layout(
        title="Revisions by Lag Bucket",
        xaxis_title="Revision Lag (Days)",
        yaxis_title="Count of Revisions",
        template="plotly_white",
        margin=dict(l=40, r=40, t=50, b=40),
    )
    return fig


def create_direction_donut_chart(
    upward: int,
    downward: int,
    became_available: int = 0,
    became_missing: int = 0,
) -> go.Figure:
    """Create a donut pie chart of revision directions."""
    labels = []
    values = []
    colors = []

    if upward > 0:
        labels.append("Upward")
        values.append(upward)
        colors.append("#2ca02c")
    if downward > 0:
        labels.append("Downward")
        values.append(downward)
        colors.append("#d62728")
    if became_available > 0:
        labels.append("Became Available")
        values.append(became_available)
        colors.append("#1f77b4")
    if became_missing > 0:
        labels.append("Became Missing")
        values.append(became_missing)
        colors.append("#7f7f7f")

    if not values:
        fig = go.Figure()
        fig.update_layout(title="No revisions recorded", template="plotly_white")
        return fig

    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=values,
                hole=0.4,
                marker=dict(colors=colors),
                textinfo="label+percent",
            )
        ]
    )
    fig.update_layout(
        title="Revision Direction Split",
        template="plotly_white",
        margin=dict(l=40, r=40, t=50, b=40),
    )
    return fig


def create_period_revisions_bar_chart(
    period_metrics: List[Dict[str, Any]],
    unit_label: str = "",
) -> go.Figure:
    """Create a bar chart of mean relative revision percentage by observation year/period."""
    if not period_metrics:
        fig = go.Figure()
        fig.update_layout(title="No period revision data", template="plotly_white")
        return fig

    df = pd.DataFrame(period_metrics)
    fig = px.bar(
        df,
        x="period",
        y="mean_relative_revision_pct",
        color="mean_relative_revision_pct",
        color_continuous_scale="RdBu_r",
        title="Mean Relative Revision (%) by Period",
        labels={
            "period": "Observation Period",
            "mean_relative_revision_pct": "Mean Relative Revision (%)",
        },
        template="plotly_white",
    )
    fig.update_layout(margin=dict(l=40, r=40, t=50, b=40))
    return fig
