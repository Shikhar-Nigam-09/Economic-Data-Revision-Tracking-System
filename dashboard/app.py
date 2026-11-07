"""Main entry point for the Economic Data Monitoring Streamlit Dashboard."""

import streamlit as st

st.set_page_config(
    page_title="Economic Data Monitoring",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Define multipage navigation using st.Page and st.navigation
pages = [
    st.Page("pages/1_Overview.py", title="Overview", icon="🏠", default=True),
    st.Page("pages/2_Economic_Indicators.py", title="Economic Indicators", icon="📊"),
    st.Page("pages/3_Historical_Observations.py", title="Historical Observations", icon="📈"),
    st.Page("pages/4_Revision_History.py", title="Revision History", icon="🔄"),
    st.Page("pages/5_Revision_Analytics.py", title="Revision Analytics", icon="📐"),
    st.Page("pages/6_Anomaly_Detection.py", title="Anomaly Detection", icon="🚨"),
    st.Page("pages/7_Data_Quality.py", title="Data Quality", icon="🛡️"),
    st.Page("pages/8_Subscriber_Management.py", title="Subscribers", icon="👥"),
    st.Page("pages/9_Notification_History.py", title="Notifications", icon="🔔"),
]

nav = st.navigation(pages)
nav.run()
