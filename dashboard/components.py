"""Reusable Streamlit UI widgets, KPI metric cards, and empty state banners."""

from typing import Any, Dict, List, Optional

import streamlit as st


def render_page_header(
    title: str,
    description: str,
    last_updated: Optional[str] = None,
    true_initial_release_active: Optional[bool] = None,
) -> None:
    """Render a standardized page title banner with timestamp and filter indicator."""
    st.title(title)
    st.markdown(f"*{description}*")

    cols = st.columns([2, 1])
    with cols[0]:
        if last_updated:
            st.caption(f"🕒 **Last updated:** {last_updated}")
    with cols[1]:
        if true_initial_release_active is not None:
            if true_initial_release_active:
                st.info("🎯 **Filter Active:** True Initial Release Only")
            else:
                st.warning("🌐 **Filter Active:** All Published Releases")
    st.divider()


def render_empty_state(
    title: str = "No data available",
    message: str = "No records were found for the selected indicator or criteria.",
    suggested_command: Optional[str] = None,
) -> None:
    """Render a friendly empty state card with guidance on how to populate data."""
    st.info(f"### ℹ️ {title}\n\n{message}")
    if suggested_command:
        st.markdown("👉 **Suggested CLI command to populate this data:**")
        st.code(suggested_command, language="bash")


def render_error_state(error_message: str, error_code: str = "ERROR") -> None:
    """Render a styled error banner."""
    st.error(f"⚠️ **{error_code}:** {error_message}")


def render_metric_cards(metrics: List[Dict[str, Any]]) -> None:
    """Render a row of st.metric cards.

    Each metric dict should have 'label', 'value', and optional 'delta'.
    """
    cols = st.columns(len(metrics))
    for i, m in enumerate(metrics):
        with cols[i]:
            st.metric(
                label=m.get("label", ""),
                value=m.get("value", "—"),
                delta=m.get("delta"),
                delta_color=m.get("delta_color", "normal"),
            )
