"""Subscriber Management page: registration, preferences, and test alerts."""

from datetime import datetime

import streamlit as st

from dashboard.api_client import ApiClient, ApiClientError
from dashboard.components import render_empty_state, render_error_state, render_page_header

api = ApiClient()

render_page_header(
    title="Telegram Subscriber Management",
    description="Manage alert subscribers, customize event subscriptions, and test alert delivery.",
    last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
)

try:
    # 1. Subscriber List & Registration
    col_reg, col_list = st.columns([1, 2])

    with col_reg:
        st.markdown("### ➕ Register Subscriber")
        with st.form("new_subscriber_form"):
            new_chat_id = st.number_input("Telegram Chat ID", min_value=1, step=1, value=123456789)
            new_username = st.text_input("Username (Optional, without @)")
            new_display = st.text_input("Display Name (Optional)")
            reg_submit = st.form_submit_button("Register / Reactivate")

            if reg_submit:
                try:
                    res = api.create_subscriber(
                        telegram_chat_id=int(new_chat_id),
                        telegram_username=new_username or None,
                        display_name=new_display or None,
                    )
                    st.success(f"Subscriber registered: ID #{res.get('id')}")
                    st.rerun()
                except ApiClientError as exc:
                    render_error_state(exc.message, exc.code)

    sub_resp = api.list_subscribers()
    subscribers = sub_resp.get("items", [])

    with col_list:
        st.markdown("### 👥 Active Subscribers")
        if not subscribers:
            render_empty_state(
                title="No subscribers registered",
                message="Subscribers can also register by sending /start to the Telegram bot.",
                suggested_command="python -m econ_monitor.cli telegram-poll",
            )
        else:
            sub_table = []
            for s in subscribers:
                sub_table.append(
                    {
                        "ID": s.get("id"),
                        "Chat ID": s.get("telegram_chat_id"),
                        "Username": s.get("telegram_username") or "—",
                        "Display Name": s.get("display_name") or "—",
                        "Active": "🟢 Yes" if s.get("is_active") else "🔴 No",
                        "Registered At": s.get("created_at"),
                    }
                )
            st.dataframe(sub_table, use_container_width=True, hide_index=True)

    # 2. Preferences & Alert Testing Section
    if subscribers:
        st.divider()
        st.markdown("### ⚙️ Subscriber Preferences & Alert Testing")

        sub_options = {}
        for s in subscribers:
            name_part = s.get("display_name") or s.get("telegram_username") or s["telegram_chat_id"]
            sub_options[f"#{s['id']} — {name_part}"] = s["id"]
        chosen_label = st.selectbox("Select Subscriber", list(sub_options.keys()))
        selected_sub_id = sub_options[chosen_label]

        action_c1, action_c2 = st.columns([1, 1])
        with action_c1:
            if st.button(
                "📲 Send Test Alert to Telegram", type="primary", use_container_width=True
            ):
                try:
                    test_res = api.send_test_message(selected_sub_id)
                    if test_res.get("success"):
                        st.success(test_res.get("message"))
                    else:
                        st.warning(f"Delivery notice: {test_res.get('message')}")
                except ApiClientError as exc:
                    render_error_state(exc.message, exc.code)

        with action_c2:
            if st.button("🚫 Deactivate Subscriber", use_container_width=True):
                try:
                    api.deactivate_subscriber(selected_sub_id)
                    st.success(f"Subscriber #{selected_sub_id} deactivated.")
                    st.rerun()
                except ApiClientError as exc:
                    render_error_state(exc.message, exc.code)

        # Indicator Preferences Table & Editor
        prefs = api.get_subscriber_preferences(selected_sub_id)
        if prefs:
            st.markdown("#### Notification Event Settings")
            pref_table = []
            for p in prefs:
                pref_table.append(
                    {
                        "Indicator": p.get("series_code"),
                        "Enabled": "✅ Yes" if p.get("is_enabled") else "❌ No",
                        "New Obs": "✅" if p.get("notify_new_observation") else "❌",
                        "Revisions": "✅" if p.get("notify_revision") else "❌",
                        "Anomalies": "✅" if p.get("notify_anomaly") else "❌",
                        "Quality": "✅" if p.get("notify_quality_issue") else "❌",
                        "Min Rev %": f"{p.get('min_revision_pct')}%"
                        if p.get("min_revision_pct") is not None
                        else "All",
                    }
                )
            st.dataframe(pref_table, use_container_width=True, hide_index=True)

            # Update preference form
            with st.expander("✏️ Update Notification Preferences"):
                with st.form("update_pref_form"):
                    target_code = st.selectbox(
                        "Apply To", ["all"] + [p["series_code"] for p in prefs]
                    )
                    p_enabled = st.checkbox("Enable Alerts", value=True)
                    p_rev = st.checkbox("Notify on Revisions", value=True)
                    p_anom = st.checkbox("Notify on Anomalies", value=True)
                    p_qual = st.checkbox("Notify on Quality Issues", value=False)
                    p_obs = st.checkbox("Notify on New Observations", value=False)
                    p_min_pct = st.number_input(
                        "Min Revision (%) Threshold", min_value=0.0, value=0.0, step=0.1
                    )

                    save_pref = st.form_submit_button("Save Preference Settings")
                    if save_pref:
                        try:
                            api.update_subscriber_preference(
                                subscriber_id=selected_sub_id,
                                series_code=target_code,
                                is_enabled=p_enabled,
                                notify_revision=p_rev,
                                notify_anomaly=p_anom,
                                notify_quality_issue=p_qual,
                                notify_new_observation=p_obs,
                                min_revision_pct=p_min_pct if p_min_pct > 0 else None,
                            )
                            st.success("Preferences updated successfully.")
                            st.rerun()
                        except ApiClientError as exc:
                            render_error_state(exc.message, exc.code)

except ApiClientError as exc:
    render_error_state(exc.message, exc.code)
