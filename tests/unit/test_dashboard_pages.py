from pathlib import Path
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

DASHBOARD_DIR = Path(__file__).resolve().parent.parent.parent / "dashboard"


def _mock_api_client():
    mock = MagicMock()
    # Health
    mock.get_health.return_value = {"status": "ok", "database": "connected"}
    # Series
    mock.list_series.return_value = [
        {
            "id": 1,
            "code": "GDP",
            "fred_series_id": "GDPC1",
            "title": "Real Gross Domestic Product",
            "frequency": "Q",
            "units": "Billions of Chained 2017 Dollars",
            "seasonal_adjustment": "Seasonally Adjusted Annual Rate",
            "change_unit_label": "Billions of Chained Dollars",
            "anomaly_transform": "pct_change",
            "expected_min": "0",
            "expected_max": "200000",
            "max_stale_lag_days": 130,
            "initial_release_max_lag_days": 150,
            "history_start": "1990-01-01",
            "is_active": True,
            "fred_last_updated": "2024-01-01T00:00:00Z",
        }
    ]
    # Revisions
    mock.list_revisions.return_value = {
        "items": [
            {
                "id": 1,
                "series_code": "GDP",
                "observation_date": "2024-01-01",
                "revision_date": "2024-02-01",
                "previous_value": "100.0",
                "new_value": "105.0",
                "absolute_revision": "5.0",
                "relative_revision_pct": "5.0",
                "direction": "up",
                "revision_type": "value_revision",
                "revision_lag_days": 31,
            }
        ],
        "total": 1,
    }
    mock.get_revision.return_value = {
        "id": 1,
        "series_code": "GDP",
        "observation_date": "2024-01-01",
        "revision_date": "2024-02-01",
        "revision_number": 1,
        "previous_value": "100.0",
        "new_value": "105.0",
        "absolute_revision": "5.0",
        "revision_lag_days": 31,
    }
    # Observations
    mock.get_observations.return_value = {
        "items": [
            {
                "observation_id": 1,
                "observation_date": "2024-01-01",
                "vintage_id": 1,
                "vintage_date": "2024-02-01",
                "value": "105.0",
                "is_missing": False,
                "vintage_count": 2,
            }
        ],
        "total": 1,
    }
    mock.get_observation_vintages.return_value = {
        "items": [
            {
                "id": 1,
                "vintage_date": "2024-02-01",
                "value": "100.0",
                "is_missing": False,
            },
            {
                "id": 2,
                "vintage_date": "2024-03-01",
                "value": "105.0",
                "is_missing": False,
            },
        ],
        "total": 2,
    }
    # Analytics
    mock.get_revision_summary.return_value = {
        "series_code": "GDP",
        "change_unit_label": "Billions of Chained Dollars",
        "total_observations": 10,
        "observations_revised": 5,
        "mean_absolute_revision": 2.5,
        "mean_relative_revision_pct": 1.2,
        "average_revisions_per_observation": 1.5,
    }
    mock.get_direction_counts.return_value = {
        "upward": 3,
        "downward": 2,
        "became_available": 0,
        "became_missing": 0,
    }
    mock.get_magnitude_distribution.return_value = {
        "bins": [{"bin_start": 0.0, "bin_end": 5.0, "count": 5}],
    }
    mock.get_revisions_by_lag.return_value = {
        "items": [{"bucket_label": "0-30 days", "revision_count": 5, "percentage_of_total": 100.0}]
    }
    mock.get_revisions_by_period.return_value = {
        "items": [{"period": "2024", "mean_relative_revision_pct": 1.2, "revision_count": 5}]
    }
    mock.get_first_vs_latest.return_value = {
        "observations_compared": 10,
        "share_revised_upward_pct": 50.0,
        "share_revised_downward_pct": 30.0,
        "share_unchanged_pct": 20.0,
        "mean_signed_absolute_revision": "1.5",
        "mean_signed_relative_revision_pct": "0.8",
    }
    # Anomalies
    mock.get_anomalies_status.return_value = {
        "total_flagged": 2,
        "total_new_records": 0,
    }
    mock.list_anomalies.return_value = {
        "items": [
            {
                "id": 1,
                "series_code": "GDP",
                "observation_date": "2024-01-01",
                "vintage_date": "2024-02-01",
                "vintage_value": "100.0",
                "method": "zscore",
                "score": "3.5",
                "status": "flagged",
                "is_superseded_vintage": False,
                "note": "Spike verified",
                "detected_at": "2024-02-01T00:00:00Z",
            }
        ],
        "total": 1,
    }
    # Quality
    mock.get_latest_quality_scores.return_value = [
        {
            "series_code": "GDP",
            "score": "98.50",
            "component_scores": {"completeness": 100.0, "validity": 95.0},
            "computed_at": "2024-02-01T00:00:00Z",
        }
    ]
    mock.get_quality_logs.return_value = {
        "items": [
            {
                "checked_at": "2024-02-01T00:00:00Z",
                "series_code": "GDP",
                "check_name": "missing_values",
                "passed": True,
                "severity": "warning",
                "affected_count": 0,
                "message": "Passed",
            }
        ],
        "total": 1,
    }
    # Subscribers
    mock.list_subscribers.return_value = {
        "items": [
            {
                "id": 1,
                "telegram_chat_id": 123456789,
                "telegram_username": "econuser",
                "display_name": "Econ Analyst",
                "is_active": True,
                "created_at": "2024-01-01T00:00:00Z",
            }
        ],
        "total": 1,
    }
    mock.get_subscriber_preferences.return_value = [
        {
            "series_code": "GDP",
            "is_enabled": True,
            "notify_new_observation": False,
            "notify_revision": True,
            "notify_anomaly": True,
            "notify_quality_issue": False,
            "min_revision_pct": None,
        }
    ]
    # Notifications
    mock.get_notification_summary.return_value = {
        "total_attempts": 10,
        "sent_count": 8,
        "failed_count": 1,
        "pending_count": 1,
        "skipped_count": 0,
    }
    mock.list_notifications.return_value = {
        "items": [
            {
                "id": 1,
                "subscriber_name": "Econ Analyst",
                "series_code": "GDP",
                "event_type": "revision_batch",
                "status": "sent",
                "attempts": 1,
                "telegram_message_id": 1001,
                "last_error": None,
                "created_at": "2024-02-01T00:00:00Z",
                "sent_at": "2024-02-01T00:00:01Z",
            }
        ],
        "total": 1,
    }

    return mock


@patch("dashboard.api_client.ApiClient")
def test_page_1_overview_render(mock_api_cls):
    mock_api_cls.return_value = _mock_api_client()
    at = AppTest.from_file(str(DASHBOARD_DIR / "pages" / "1_Overview.py"), default_timeout=15)
    at.run()
    assert not at.exception


@patch("dashboard.api_client.ApiClient")
def test_page_2_indicators_render(mock_api_cls):
    mock_api_cls.return_value = _mock_api_client()
    at = AppTest.from_file(
        str(DASHBOARD_DIR / "pages" / "2_Economic_Indicators.py"), default_timeout=15
    )
    at.run()
    assert not at.exception


@patch("dashboard.api_client.ApiClient")
def test_page_3_observations_render(mock_api_cls):
    mock_api_cls.return_value = _mock_api_client()
    at = AppTest.from_file(
        str(DASHBOARD_DIR / "pages" / "3_Historical_Observations.py"), default_timeout=15
    )
    at.run()
    assert not at.exception


@patch("dashboard.api_client.ApiClient")
def test_page_4_revisions_render(mock_api_cls):
    mock_api_cls.return_value = _mock_api_client()
    at = AppTest.from_file(
        str(DASHBOARD_DIR / "pages" / "4_Revision_History.py"), default_timeout=15
    )
    at.run()
    assert not at.exception


@patch("dashboard.api_client.ApiClient")
def test_page_5_analytics_render(mock_api_cls):
    mock_api_cls.return_value = _mock_api_client()
    at = AppTest.from_file(
        str(DASHBOARD_DIR / "pages" / "5_Revision_Analytics.py"), default_timeout=15
    )
    at.run()
    assert not at.exception


@patch("dashboard.api_client.ApiClient")
def test_page_6_anomalies_render(mock_api_cls):
    mock_api_cls.return_value = _mock_api_client()
    at = AppTest.from_file(
        str(DASHBOARD_DIR / "pages" / "6_Anomaly_Detection.py"), default_timeout=15
    )
    at.run()
    assert not at.exception


@patch("dashboard.api_client.ApiClient")
def test_page_7_quality_render(mock_api_cls):
    mock_api_cls.return_value = _mock_api_client()
    at = AppTest.from_file(str(DASHBOARD_DIR / "pages" / "7_Data_Quality.py"), default_timeout=15)
    at.run()
    assert not at.exception


@patch("dashboard.api_client.ApiClient")
def test_page_8_subscribers_render(mock_api_cls):
    mock_api_cls.return_value = _mock_api_client()
    at = AppTest.from_file(
        str(DASHBOARD_DIR / "pages" / "8_Subscriber_Management.py"), default_timeout=15
    )
    at.run()
    assert not at.exception


@patch("dashboard.api_client.ApiClient")
def test_page_9_notifications_render(mock_api_cls):
    mock_api_cls.return_value = _mock_api_client()
    at = AppTest.from_file(
        str(DASHBOARD_DIR / "pages" / "9_Notification_History.py"), default_timeout=15
    )
    at.run()
    assert not at.exception
