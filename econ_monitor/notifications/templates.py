"""Unit-aware HTML message templates for economic data event notifications."""

from typing import Any, Dict

from econ_monitor.notifications.telegram_client import escape_html


def format_new_observation_message(payload: Dict[str, Any]) -> str:
    """Format notification for a newly published observation."""
    series_code = escape_html(str(payload.get("series_code", "")))
    title = escape_html(str(payload.get("title", series_code)))
    obs_date = escape_html(str(payload.get("observation_date", "")))
    vintage_date = escape_html(str(payload.get("initial_vintage_date", "")))
    val = payload.get("initial_value")
    val_str = escape_html(str(val)) if val is not None else "N/A"
    units = escape_html(str(payload.get("units") or payload.get("change_unit_label") or ""))

    unit_suffix = f" {units}" if units else ""
    return (
        f"📊 <b>New Economic Observation</b>\n\n"
        f"<b>Indicator:</b> {series_code} ({title})\n"
        f"<b>Observation Period:</b> {obs_date}\n"
        f"<b>Initial Value:</b> {val_str}{unit_suffix}\n"
        f"<b>Vintage Date:</b> {vintage_date}\n"
    )


def format_revision_batch_message(payload: Dict[str, Any]) -> str:
    """Format notification for a batch of revised historical observations."""
    series_code = escape_html(str(payload.get("series_code", "")))
    vintage_date = escape_html(str(payload.get("vintage_date", "")))
    total_rev = payload.get("total_revisions", 0)
    up_cnt = payload.get("up_revisions_count", 0)
    down_cnt = payload.get("down_revisions_count", 0)
    unit_label = escape_html(str(payload.get("change_unit_label") or payload.get("units") or ""))
    unit_suffix = f" {unit_label}" if unit_label else ""

    lines = [
        "🔄 <b>Economic Data Revision</b>\n",
        f"<b>Indicator:</b> {series_code}",
        f"<b>Vintage Date:</b> {vintage_date}",
        (
            f"<b>Summary:</b> {total_rev} observation(s) revised "
            f"({up_cnt} upward, {down_cnt} downward)\n"
        ),
        "<b>Notable Revisions:</b>",
    ]

    top_revisions = payload.get("top_5_revisions", [])
    if top_revisions:
        for rev in top_revisions:
            o_date = escape_html(str(rev.get("observation_date", "")))
            prev = escape_html(str(rev.get("previous_value", "")))
            new = escape_html(str(rev.get("new_value", "")))
            direction = rev.get("direction")
            dir_str = (
                "upward" if direction == "up" else "downward" if direction == "down" else "changed"
            )
            abs_chg = escape_html(str(rev.get("absolute_revision", "")))
            rel_chg = rev.get("relative_revision_pct")
            rel_str = f", {rel_chg}%" if rel_chg is not None else ""

            lines.append(
                f"• <b>{o_date}:</b> {prev} → {new} "
                f"(revised {dir_str} by {abs_chg}{unit_suffix}{rel_str})"
            )
    else:
        lines.append("• <i>No detailed item breakdowns available.</i>")

    return "\n".join(lines) + "\n"


def format_anomaly_message(payload: Dict[str, Any]) -> str:
    """Format notification for an observation flagged as an anomaly."""
    series_code = escape_html(str(payload.get("series_code", "")))
    title = escape_html(str(payload.get("title", series_code)))
    obs_date = escape_html(str(payload.get("observation_date", "")))
    vintage_date = escape_html(str(payload.get("vintage_date", "")))
    method = escape_html(str(payload.get("method", "")))
    score = escape_html(str(payload.get("score", "")))
    threshold = escape_html(str(payload.get("threshold", "N/A")))
    current_val = escape_html(str(payload.get("current_value", "")))
    change = escape_html(str(payload.get("transformed_change", "")))
    unit_label = escape_html(str(payload.get("change_unit_label") or payload.get("units") or ""))
    unit_suffix = f" {unit_label}" if unit_label else ""

    method_display = "Z-Score Outlier" if method == "zscore" else "Isolation Forest"

    return (
        f"⚠️ <b>Economic Anomaly Flagged for Review</b>\n\n"
        f"<i>Note: Flagged for investigation — genuine shocks or outliers, "
        f"not confirmed bad data.</i>\n\n"
        f"<b>Indicator:</b> {series_code} ({title})\n"
        f"<b>Observation Period:</b> {obs_date}\n"
        f"<b>Vintage Date:</b> {vintage_date}\n"
        f"<b>Method:</b> {method_display}\n"
        f"<b>Score:</b> {score} (threshold: {threshold})\n"
        f"<b>Current Value:</b> {current_val}\n"
        f"<b>Transformed Change:</b> {change}{unit_suffix}\n"
    )


def format_quality_issue_message(payload: Dict[str, Any]) -> str:
    """Format notification for a newly detected data quality violation."""
    series_code = escape_html(str(payload.get("series_code", "")))
    check_name = escape_html(str(payload.get("check_name", "")))
    msg = escape_html(str(payload.get("message", "Data quality check failed.")))

    return (
        f"🛠️ <b>Data Quality Alert</b>\n\n"
        f"<b>Indicator:</b> {series_code}\n"
        f"<b>Check Name:</b> <code>{check_name}</code>\n"
        f"<b>Details:</b> {msg}\n"
    )


def format_event_message(event_type: str, payload: Dict[str, Any]) -> str:
    """Route event payload to its corresponding unit-aware HTML template."""
    if event_type == "new_observation":
        return format_new_observation_message(payload)
    elif event_type == "revision_batch":
        return format_revision_batch_message(payload)
    elif event_type == "anomaly":
        return format_anomaly_message(payload)
    elif event_type == "quality_issue":
        return format_quality_issue_message(payload)
    else:
        return (
            f"ℹ️ <b>Alert ({escape_html(event_type)})</b>\n\n<pre>{escape_html(str(payload))}</pre>"
        )
