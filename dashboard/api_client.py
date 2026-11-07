"""HTTP client for the Economic Data Monitoring API with error normalization."""

import os
from typing import Any, Dict, List, Optional

import requests

DEFAULT_API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000/api/v1")


class ApiClientError(Exception):
    """Exception raised when an API request fails."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code or "UNKNOWN_API_ERROR"
        self.details = details or {}


class ApiClient:
    """Type-safe client to interact with the FastAPI backend over HTTP."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = 15.0) -> None:
        raw_url = base_url or DEFAULT_API_BASE_URL
        self.base_url = raw_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        clean_path = path.lstrip("/")
        return f"{self.base_url}/{clean_path}"

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = self._url(path)
        try:
            resp = self.session.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApiClientError(
                f"Failed to connect to API backend at {url}: {exc}",
                code="CONNECTION_ERROR",
            ) from exc

        if not resp.ok:
            error_code = "HTTP_ERROR"
            error_msg = f"HTTP {resp.status_code}: {resp.reason}"
            details = {}

            try:
                data = resp.json()
                if "error" in data:
                    err = data["error"]
                    error_code = err.get("code", error_code)
                    error_msg = err.get("message", error_msg)
                    details = err.get("details", {})
                elif "detail" in data:
                    error_msg = str(data["detail"])
            except Exception:
                pass

            raise ApiClientError(
                message=error_msg,
                status_code=resp.status_code,
                code=error_code,
                details=details,
            )

        return resp.json()

    # -------------------------------------------------------------------------
    # Health & System
    # -------------------------------------------------------------------------

    def get_health(self) -> Dict[str, Any]:
        """Check API and database health status."""
        root_url = self.base_url.split("/api/v1")[0]
        url = f"{root_url}/health" if root_url else "http://127.0.0.1:8000/health"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            return resp.json()
        except Exception as exc:
            return {
                "status": "degraded",
                "database": "disconnected",
                "error": str(exc),
            }

    # -------------------------------------------------------------------------
    # Economic Series
    # -------------------------------------------------------------------------

    def list_series(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """List all configured economic series."""
        data = self._request("GET", "/series", params={"active_only": active_only})
        return data.get("items", [])

    def get_series(self, code: str) -> Dict[str, Any]:
        """Get details for a single economic series."""
        return self._request("GET", f"/series/{code.upper()}")

    def sync_series_config(self) -> Dict[str, Any]:
        """Synchronize series catalog from indicators.yaml."""
        return self._request("POST", "/series/sync-config")

    # -------------------------------------------------------------------------
    # Observations & Vintages
    # -------------------------------------------------------------------------

    def get_observations(
        self,
        code: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        view: str = "latest",
        as_of: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Retrieve observations under latest, first, or as-of views."""
        params: Dict[str, Any] = {
            "view": view,
            "limit": limit,
            "offset": offset,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if view == "as_of" and as_of:
            params["as_of"] = as_of

        return self._request("GET", f"/series/{code.upper()}/observations", params=params)

    def get_observation_vintages(
        self,
        observation_id: int,
        limit: int = 1000,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Retrieve vintage revision chain for an observation."""
        params = {"limit": limit, "offset": offset}
        return self._request("GET", f"/observations/{observation_id}/vintages", params=params)

    # -------------------------------------------------------------------------
    # Revisions
    # -------------------------------------------------------------------------

    def list_revisions(
        self,
        series: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        direction: Optional[str] = None,
        revision_type: Optional[str] = None,
        min_abs_pct: Optional[float] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List historical revision events."""
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if series:
            params["series"] = series.upper()
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if direction:
            params["direction"] = direction
        if revision_type:
            params["type"] = revision_type
        if min_abs_pct is not None:
            params["min_abs_pct"] = min_abs_pct

        return self._request("GET", "/revisions", params=params)

    def get_revision(self, revision_id: int) -> Dict[str, Any]:
        """Get single revision event details."""
        return self._request("GET", f"/revisions/{revision_id}")

    # -------------------------------------------------------------------------
    # Revision Analytics
    # -------------------------------------------------------------------------

    def get_revision_summary(
        self,
        series: str,
        true_initial_release_only: bool = True,
    ) -> Dict[str, Any]:
        """Get summary statistics of revisions for a series."""
        params = {
            "series": series.upper(),
            "true_initial_release_only": true_initial_release_only,
        }
        return self._request("GET", "/analytics/revisions/summary", params=params)

    def get_revisions_by_period(
        self,
        series: str,
        true_initial_release_only: bool = True,
    ) -> Dict[str, Any]:
        """Get revisions grouped by period."""
        params = {
            "series": series.upper(),
            "true_initial_release_only": true_initial_release_only,
        }
        return self._request("GET", "/analytics/revisions/by-period", params=params)

    def get_direction_counts(
        self,
        series: str,
        true_initial_release_only: bool = True,
    ) -> Dict[str, Any]:
        """Get revision direction counts."""
        params = {
            "series": series.upper(),
            "true_initial_release_only": true_initial_release_only,
        }
        return self._request("GET", "/analytics/revisions/direction-counts", params=params)

    def get_magnitude_distribution(
        self,
        series: str,
        num_bins: int = 10,
        true_initial_release_only: bool = True,
    ) -> Dict[str, Any]:
        """Get magnitude distribution histogram bins."""
        params = {
            "series": series.upper(),
            "num_bins": num_bins,
            "true_initial_release_only": true_initial_release_only,
        }
        return self._request("GET", "/analytics/revisions/magnitude-distribution", params=params)

    def get_revisions_by_lag(
        self,
        series: str,
        true_initial_release_only: bool = True,
    ) -> Dict[str, Any]:
        """Get revisions by lag bucket."""
        params = {
            "series": series.upper(),
            "true_initial_release_only": true_initial_release_only,
        }
        return self._request("GET", "/analytics/revisions/by-lag", params=params)

    def get_revisions_frequency(
        self,
        series: str,
        true_initial_release_only: bool = True,
    ) -> Dict[str, Any]:
        """Get frequency distribution of revisions per observation."""
        params = {
            "series": series.upper(),
            "true_initial_release_only": true_initial_release_only,
        }
        return self._request("GET", "/analytics/revisions/frequency", params=params)

    def get_first_vs_latest(
        self,
        series: str,
        true_initial_release_only: bool = True,
    ) -> Dict[str, Any]:
        """Get first release vs latest vintage comparison."""
        params = {
            "series": series.upper(),
            "true_initial_release_only": true_initial_release_only,
        }
        return self._request("GET", "/analytics/revisions/first-vs-latest", params=params)

    # -------------------------------------------------------------------------
    # Anomalies
    # -------------------------------------------------------------------------

    def list_anomalies(
        self,
        series: Optional[str] = None,
        method: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List anomaly detection records."""
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if series:
            params["series"] = series.upper()
        if method:
            params["method"] = method
        if status:
            params["status"] = status
        return self._request("GET", "/anomalies", params=params)

    def get_anomalies_status(self) -> Dict[str, Any]:
        """Get anomaly execution status."""
        return self._request("GET", "/anomalies/status")

    def patch_anomaly(
        self,
        anomaly_id: int,
        status: str,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update review status and note for an anomaly."""
        payload = {"status": status, "note": note}
        return self._request("PATCH", f"/anomalies/{anomaly_id}", json_data=payload)

    def trigger_anomaly_run(self, series: Optional[str] = None) -> Dict[str, Any]:
        """Trigger synchronous anomaly detection run."""
        params = {}
        if series:
            params["series"] = series.upper()
        return self._request("POST", "/anomalies/run", params=params)

    # -------------------------------------------------------------------------
    # Data Quality
    # -------------------------------------------------------------------------

    def get_latest_quality_scores(self) -> List[Dict[str, Any]]:
        """Get latest health scores for active indicators."""
        data = self._request("GET", "/quality/scores/latest")
        return data.get("items", [])

    def get_quality_scores_history(
        self,
        series: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Get health score history."""
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if series:
            params["series"] = series.upper()
        return self._request("GET", "/quality/scores/history", params=params)

    def get_quality_logs(
        self,
        series: Optional[str] = None,
        check_name: Optional[str] = None,
        severity: Optional[str] = None,
        passed: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Get detailed data quality evaluation logs."""
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if series:
            params["series"] = series.upper()
        if check_name:
            params["check_name"] = check_name
        if severity:
            params["severity"] = severity
        if passed is not None:
            params["passed"] = passed
        return self._request("GET", "/quality/logs", params=params)

    def trigger_quality_run(self, series: Optional[str] = None) -> Dict[str, Any]:
        """Trigger synchronous data quality check execution."""
        params = {}
        if series:
            params["series"] = series.upper()
        return self._request("POST", "/quality/run", params=params)

    # -------------------------------------------------------------------------
    # Subscribers & Notifications
    # -------------------------------------------------------------------------

    def list_subscribers(
        self,
        is_active: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List Telegram subscribers."""
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if is_active is not None:
            params["is_active"] = is_active
        return self._request("GET", "/subscribers", params=params)

    def create_subscriber(
        self,
        telegram_chat_id: int,
        telegram_username: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or reactivate a subscriber."""
        payload = {
            "telegram_chat_id": telegram_chat_id,
            "telegram_username": telegram_username,
            "display_name": display_name,
        }
        return self._request("POST", "/subscribers", json_data=payload)

    def get_subscriber(self, subscriber_id: int) -> Dict[str, Any]:
        """Get single subscriber details."""
        return self._request("GET", f"/subscribers/{subscriber_id}")

    def patch_subscriber(
        self,
        subscriber_id: int,
        is_active: Optional[bool] = None,
        telegram_username: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update subscriber fields."""
        payload: Dict[str, Any] = {}
        if is_active is not None:
            payload["is_active"] = is_active
        if telegram_username is not None:
            payload["telegram_username"] = telegram_username
        if display_name is not None:
            payload["display_name"] = display_name
        return self._request("PATCH", f"/subscribers/{subscriber_id}", json_data=payload)

    def deactivate_subscriber(self, subscriber_id: int) -> Dict[str, Any]:
        """Deactivate a subscriber."""
        return self._request("DELETE", f"/subscribers/{subscriber_id}")

    def get_subscriber_preferences(self, subscriber_id: int) -> List[Dict[str, Any]]:
        """Get subscriber preferences."""
        data = self._request("GET", f"/subscribers/{subscriber_id}/preferences")
        return data.get("items", [])

    def update_subscriber_preference(
        self,
        subscriber_id: int,
        series_code: str,
        is_enabled: Optional[bool] = None,
        notify_new_observation: Optional[bool] = None,
        notify_revision: Optional[bool] = None,
        notify_anomaly: Optional[bool] = None,
        notify_quality_issue: Optional[bool] = None,
        min_revision_pct: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Update notification preferences for an indicator or 'all'."""
        payload: Dict[str, Any] = {"series_code": series_code}
        if is_enabled is not None:
            payload["is_enabled"] = is_enabled
        if notify_new_observation is not None:
            payload["notify_new_observation"] = notify_new_observation
        if notify_revision is not None:
            payload["notify_revision"] = notify_revision
        if notify_anomaly is not None:
            payload["notify_anomaly"] = notify_anomaly
        if notify_quality_issue is not None:
            payload["notify_quality_issue"] = notify_quality_issue
        if min_revision_pct is not None:
            payload["min_revision_pct"] = min_revision_pct

        data = self._request("PUT", f"/subscribers/{subscriber_id}/preferences", json_data=payload)
        return data.get("items", [])

    def send_test_message(self, subscriber_id: int) -> Dict[str, Any]:
        """Send a test alert message to subscriber."""
        return self._request("POST", f"/subscribers/{subscriber_id}/test-message")

    def list_notifications(
        self,
        status: Optional[str] = None,
        subscriber_id: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List notification alert history."""
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if subscriber_id:
            params["subscriber_id"] = subscriber_id
        return self._request("GET", "/notifications", params=params)

    def get_notification_summary(self) -> Dict[str, Any]:
        """Get aggregate notification metrics."""
        return self._request("GET", "/notifications/summary")

    def list_data_events(
        self,
        series: Optional[str] = None,
        event_type: Optional[str] = None,
        is_backfill: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List data events."""
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if series:
            params["series"] = series.upper()
        if event_type:
            params["event_type"] = event_type
        if is_backfill is not None:
            params["is_backfill"] = is_backfill
        return self._request("GET", "/events", params=params)

    def dispatch_notifications(self, dry_run: bool = False) -> Dict[str, Any]:
        """Dispatch notifications manually."""
        return self._request("POST", "/notifications/dispatch", params={"dry_run": dry_run})

    # -------------------------------------------------------------------------
    # Ingestion & Pipeline
    # -------------------------------------------------------------------------

    def trigger_ingestion(self, mode: str = "api", notify: bool = True) -> Dict[str, Any]:
        """Trigger background pipeline execution."""
        return self._request("POST", "/ingestion/run", params={"mode": mode, "notify": notify})

    def list_ingestion_runs(
        self,
        status: Optional[str] = None,
        mode: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List historical ingestion runs."""
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if mode:
            params["mode"] = mode
        return self._request("GET", "/ingestion/runs", params=params)

    def get_ingestion_run(self, run_id: int) -> Dict[str, Any]:
        """Get single ingestion run details."""
        return self._request("GET", f"/ingestion/runs/{run_id}")

    def import_snapshots(self, snapshots_dir: str = "data/snapshots") -> Dict[str, Any]:
        """Import Parquet snapshots."""
        return self._request(
            "POST", "/ingestion/import-snapshots", params={"snapshots_dir": snapshots_dir}
        )
