"""Notification History page: delivery audit trail, summary metrics, and manual dispatch trigger."""

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
    title="Notification Alert History & Dispatch",
    description="Audit trail of sent, failed, and pending alert deliveries.",
    last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
)

try:
    # 1. Trigger Dispatch Buttons Row
    c_btn1, c_btn2 = st.columns([1, 1])
    with c_btn1:
        if st.button("🚀 Dispatch Pending Notifications", type="primary", use_container_width=True):
            try:
                res = api.dispatch_notifications(dry_run=False)
                st.success(
                    f"Dispatch run: {res.get('sent_count')} sent, "
                    f"{res.get('failed_count')} failed, {res.get('skipped_count')} skipped."
                )
                st.rerun()
            except ApiClientError as exc:
                render_error_state(exc.message, exc.code)

    with c_btn2:
        if st.button("🔍 Dry Run Dispatch (Preview)", use_container_width=True):
            try:
                res = api.dispatch_notifications(dry_run=True)
                st.info(
                    f"Dry run preview: {res.get('eligible_events_found')} eligible events matched "
                    f"{res.get('matched_subscribers')} subscribers."
                )
            except ApiClientError as exc:
                render_error_state(exc.message, exc.code)

    # 2. Notification Delivery Metrics
    summary = api.get_notification_summary()
    metrics = [
        {"label": "Total Delivery Attempts", "value": str(summary.get("total_attempts", 0))},
        {
            "label": "Delivered (Sent)",
            "value": str(summary.get("sent_count", 0)),
            "delta_color": "normal",
        },
        {
            "label": "Failed Deliveries",
            "value": str(summary.get("failed_count", 0)),
            "delta_color": "inverse",
        },
        {"label": "Pending Retries", "value": str(summary.get("pending_count", 0))},
        {"label": "Skipped Deliveries", "value": str(summary.get("skipped_count", 0))},
    ]
    render_metric_cards(metrics)

    # 3. Notification History Table
    st.markdown("### 📜 Delivery Audit Trail")
    f1, f2 = st.columns(2)
    with f1:
        filter_status = st.selectbox(
            "Filter Status", ["All", "sent", "failed", "pending", "skipped"], index=0
        )
    with f2:
        max_rows = st.slider("Max Notifications", min_value=50, max_value=500, value=100, step=50)

    st_param = None if filter_status == "All" else filter_status
    notif_resp = api.list_notifications(status=st_param, limit=max_rows)
    notifs = notif_resp.get("items", [])

    if not notifs:
        render_empty_state(
            title="No notification records found",
            message="No notification events have been logged yet.",
            suggested_command="python -m econ_monitor.cli notify",
        )
    else:
        st.markdown(
            f"**Showing {len(notifs)} records (total: {notif_resp.get('total', len(notifs))}):**"
        )
        n_table = []
        for n in notifs:
            status_badge = (
                "🟢 Sent" if n.get("status") == "sent" else f"🔴 {n.get('status').capitalize()}"
            )
            n_table.append(
                {
                    "ID": n.get("id"),
                    "Subscriber": n.get("subscriber_name") or f"Chat {n.get('telegram_chat_id')}",
                    "Indicator": n.get("series_code"),
                    "Event Type": n.get("event_type"),
                    "Status": status_badge,
                    "Attempts": n.get("attempts"),
                    "Telegram Msg ID": n.get("telegram_message_id") or "—",
                    "Last Error": n.get("last_error") or "None",
                    "Created At": n.get("created_at"),
                    "Sent At": n.get("sent_at") or "—",
                }
            )
        st.dataframe(n_table, use_container_width=True, hide_index=True)

except ApiClientError as exc:
    render_error_state(exc.message, exc.code)
