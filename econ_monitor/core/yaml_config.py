"""YAML configuration models, validation, and loaders."""

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from econ_monitor.core.errors import ConfigurationError

AnomalyTransform = Literal["pct_change", "diff"]


class IndicatorConfig(BaseModel):
    """Configuration for an individual tracked economic indicator."""

    code: str = Field(..., min_length=1, description="Internal unique code, e.g. GDP")
    fred_series_id: str = Field(..., min_length=1, description="Official FRED series identifier")
    display_name: str
    category: str
    active: bool = True
    anomaly_transform: AnomalyTransform
    expected_min: float
    expected_max: float
    max_stale_lag_days: int = Field(..., gt=0)
    initial_release_max_lag_days: int = Field(..., gt=0)
    history_start: date
    change_unit_label: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "IndicatorConfig":
        if self.expected_min >= self.expected_max:
            raise ValueError(
                f"expected_min ({self.expected_min}) must be strictly less than "
                f"expected_max ({self.expected_max}) for indicator '{self.code}'"
            )
        return self


class IndicatorsRootConfig(BaseModel):
    """Root model for indicators.yaml containing a list of indicator specifications."""

    indicators: list[IndicatorConfig]

    @model_validator(mode="after")
    def validate_uniqueness(self) -> "IndicatorsRootConfig":
        codes = [item.code for item in self.indicators]
        if len(codes) != len(set(codes)):
            duplicates = [c for c in set(codes) if codes.count(c) > 1]
            raise ValueError(f"Duplicate indicator codes detected in configuration: {duplicates}")

        fred_ids = [item.fred_series_id for item in self.indicators]
        if len(fred_ids) != len(set(fred_ids)):
            duplicates = [fid for fid in set(fred_ids) if fred_ids.count(fid) > 1]
            raise ValueError(f"Duplicate FRED series IDs detected in configuration: {duplicates}")

        return self


class QualityWeights(BaseModel):
    completeness: int = Field(..., ge=0)
    validity: int = Field(..., ge=0)
    uniqueness: int = Field(..., ge=0)
    frequency_consistency: int = Field(..., ge=0)
    timeliness: int = Field(..., ge=0)
    schema_conformance: int = Field(..., ge=0)

    @model_validator(mode="after")
    def validate_sum(self) -> "QualityWeights":
        total = (
            self.completeness
            + self.validity
            + self.uniqueness
            + self.frequency_consistency
            + self.timeliness
            + self.schema_conformance
        )
        if total != 100:
            raise ValueError(f"Data quality weights must sum to exactly 100, got {total}")
        return self


class QualityThresholds(BaseModel):
    healthy_score_min: float = Field(..., ge=0.0, le=100.0)
    degraded_score_min: float = Field(..., ge=0.0, le=100.0)


class QualityConfig(BaseModel):
    weights: QualityWeights
    thresholds: QualityThresholds


class ZScoreConfig(BaseModel):
    threshold: float = Field(..., gt=0.0)


class IsolationForestConfig(BaseModel):
    n_estimators: int = Field(..., gt=0)
    contamination: float = Field(..., gt=0.0, lt=0.5)
    random_state: int


class AnomaliesConfig(BaseModel):
    zscore: ZScoreConfig
    isolation_forest: IsolationForestConfig
    windows: dict[str, int]
    min_history_required: dict[str, int]


class NotificationDefaults(BaseModel):
    notify_new_observation: bool = False
    notify_revision: bool = True
    notify_anomaly: bool = True
    notify_quality_issue: bool = False


class NotificationsConfig(BaseModel):
    defaults: NotificationDefaults
    max_retry_attempts: int = Field(default=3, ge=1)
    rate_limit_pause_seconds: float = Field(default=1.0, ge=0.0)


class FrequencyDetail(BaseModel):
    name: str
    offset: str


class MonitoringConfig(BaseModel):
    """Root model for monitoring.yaml parameters."""

    quality: QualityConfig
    anomalies: AnomaliesConfig
    notifications: NotificationsConfig
    frequencies: dict[str, FrequencyDetail]


def load_indicators_config(
    config_path: str | Path = "config/indicators.yaml",
) -> IndicatorsRootConfig:
    """Load and validate the indicators configuration file."""
    path = Path(config_path)
    if not path.is_file():
        raise ConfigurationError(f"Indicators configuration file not found at: {path.resolve()}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return IndicatorsRootConfig.model_validate(data)
    except Exception as exc:
        raise ConfigurationError(
            f"Failed to validate indicators config '{path}': {exc}", details={"error": str(exc)}
        ) from exc


def load_monitoring_config(config_path: str | Path = "config/monitoring.yaml") -> MonitoringConfig:
    """Load and validate the monitoring configuration file."""
    path = Path(config_path)
    if not path.is_file():
        raise ConfigurationError(f"Monitoring configuration file not found at: {path.resolve()}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return MonitoringConfig.model_validate(data)
    except Exception as exc:
        raise ConfigurationError(
            f"Failed to validate monitoring config '{path}': {exc}", details={"error": str(exc)}
        ) from exc
