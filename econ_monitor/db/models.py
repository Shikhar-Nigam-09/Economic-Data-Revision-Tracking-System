"""SQLAlchemy 2.0 ORM models for the Economic Data Monitoring system."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from econ_monitor.db.base import Base

# Universal JSON column that generates native JSONB on PostgreSQL
JSONType = JSON().with_variant(JSONB(), "postgresql")

# BigInteger primary key that maps to Integer on SQLite to support autoincrement in tests
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


# ------------------------------------------------------------------------------
# String Enumerations (backed by String columns with CHECK constraints)
# ------------------------------------------------------------------------------


class RevisionDirection(StrEnum):
    UP = "up"
    DOWN = "down"


class RevisionType(StrEnum):
    VALUE_REVISION = "value_revision"
    BECAME_AVAILABLE = "became_available"
    BECAME_MISSING = "became_missing"


class DataEventType(StrEnum):
    NEW_OBSERVATION = "new_observation"
    REVISION_BATCH = "revision_batch"
    ANOMALY = "anomaly"
    QUALITY_ISSUE = "quality_issue"


class AnomalyMethod(StrEnum):
    ZSCORE = "zscore"
    ISOLATION_FOREST = "isolation_forest"


class AnomalyStatus(StrEnum):
    FLAGGED = "flagged"
    REVIEWED = "reviewed"
    DISMISSED = "dismissed"


class QualitySeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class IngestionMode(StrEnum):
    API = "api"
    SNAPSHOT = "snapshot"


class IngestionTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    DASHBOARD = "dashboard"
    CLI = "cli"


class IngestionStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class IngestionSeriesStatus(StrEnum):
    SUCCESS = "success"
    SKIPPED_NO_NEW_DATA = "skipped_no_new_data"
    FAILED = "failed"


# ------------------------------------------------------------------------------
# ORM Models (Exact Class Names per A15)
# ------------------------------------------------------------------------------


class EconomicSeries(Base):
    """Metadata registry of tracked macroeconomic indicators."""

    __tablename__ = "economic_series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    fred_series_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    frequency: Mapped[str] = mapped_column(String(10), nullable=False)
    units: Mapped[str] = mapped_column(String(100), nullable=False)
    seasonal_adjustment: Mapped[str] = mapped_column(String(100), nullable=False)
    change_unit_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    anomaly_transform: Mapped[str] = mapped_column(String(20), nullable=False)
    expected_min: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    expected_max: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    max_stale_lag_days: Mapped[int] = mapped_column(Integer, nullable=False)
    initial_release_max_lag_days: Mapped[int] = mapped_column(Integer, nullable=False)
    history_start: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fred_last_updated: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (CheckConstraint("expected_min < expected_max", name="ck_series_range"),)

    observations: Mapped[list["Observation"]] = relationship(
        "Observation", back_populates="series", cascade="all, delete-orphan"
    )
    revision_history: Mapped[list["RevisionHistory"]] = relationship(
        "RevisionHistory", back_populates="series", cascade="all, delete-orphan"
    )
    data_events: Mapped[list["DataEvent"]] = relationship(
        "DataEvent", back_populates="series", cascade="all, delete-orphan"
    )
    anomaly_records: Mapped[list["AnomalyRecord"]] = relationship(
        "AnomalyRecord", back_populates="series", cascade="all, delete-orphan"
    )
    data_quality_logs: Mapped[list["DataQualityLog"]] = relationship(
        "DataQualityLog", back_populates="series", cascade="all, delete-orphan"
    )
    data_quality_scores: Mapped[list["DataQualityScore"]] = relationship(
        "DataQualityScore", back_populates="series", cascade="all, delete-orphan"
    )
    subscriber_preferences: Mapped[list["SubscriberPreference"]] = relationship(
        "SubscriberPreference", back_populates="series", cascade="all, delete-orphan"
    )
    ingestion_run_series: Mapped[list["IngestionRunSeries"]] = relationship(
        "IngestionRunSeries", back_populates="series", cascade="all, delete-orphan"
    )


class Observation(Base):
    """Observation period identity for a specific economic series."""

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("economic_series.id", ondelete="CASCADE"), nullable=False, index=True
    )
    observation_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingestion_run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ingestion_runs.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("series_id", "observation_date", name="uq_observations_series_date"),
    )

    series: Mapped["EconomicSeries"] = relationship("EconomicSeries", back_populates="observations")
    ingestion_run: Mapped[Optional["IngestionRun"]] = relationship(
        "IngestionRun", back_populates="observations"
    )
    vintages: Mapped[list["ObservationVintage"]] = relationship(
        "ObservationVintage", back_populates="observation", cascade="all, delete-orphan"
    )
    revision_history: Mapped[list["RevisionHistory"]] = relationship(
        "RevisionHistory", back_populates="observation", cascade="all, delete-orphan"
    )
    anomaly_records: Mapped[list["AnomalyRecord"]] = relationship(
        "AnomalyRecord", back_populates="observation", cascade="all, delete-orphan"
    )
    data_events: Mapped[list["DataEvent"]] = relationship(
        "DataEvent", back_populates="observation", cascade="all, delete-orphan"
    )


class ObservationVintage(Base):
    """Append-only immutable historical version of an observation."""

    __tablename__ = "observation_vintages"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    observation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("observations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vintage_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_realtime_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    is_missing: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ingestion_run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ingestion_runs.id", ondelete="SET NULL"), nullable=True
    )
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "observation_id", "vintage_date", name="uq_observation_vintages_obs_vintage"
        ),
        CheckConstraint(
            "(is_missing = TRUE AND value IS NULL) OR (is_missing = FALSE AND value IS NOT NULL)",
            name="ck_vintages_missing_value",
        ),
        Index("ix_observation_vintages_obs_vintage_desc", "observation_id", vintage_date.desc()),
    )

    observation: Mapped["Observation"] = relationship("Observation", back_populates="vintages")
    ingestion_run: Mapped[Optional["IngestionRun"]] = relationship(
        "IngestionRun", back_populates="vintages"
    )
    from_revisions: Mapped[list["RevisionHistory"]] = relationship(
        "RevisionHistory",
        foreign_keys="RevisionHistory.from_vintage_id",
        back_populates="from_vintage",
    )
    to_revisions: Mapped[list["RevisionHistory"]] = relationship(
        "RevisionHistory", foreign_keys="RevisionHistory.to_vintage_id", back_populates="to_vintage"
    )
    anomaly_records: Mapped[list["AnomalyRecord"]] = relationship(
        "AnomalyRecord", back_populates="vintage", cascade="all, delete-orphan"
    )


class RevisionHistory(Base):
    """Append-only record of a detected data revision between two consecutive vintages."""

    __tablename__ = "revision_history"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    observation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("observations.id", ondelete="CASCADE"), nullable=False
    )
    series_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("economic_series.id", ondelete="CASCADE"), nullable=False
    )
    from_vintage_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("observation_vintages.id", ondelete="RESTRICT"), nullable=False
    )
    to_vintage_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("observation_vintages.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    observation_date: Mapped[date] = mapped_column(Date, nullable=False)
    revision_date: Mapped[date] = mapped_column(Date, nullable=False)
    previous_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    new_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    absolute_revision: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    relative_revision_pct: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    revision_type: Mapped[str] = mapped_column(String(30), nullable=False)
    revision_lag_days: Mapped[int] = mapped_column(Integer, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("revision_number >= 1", name="ck_revisions_number_ge_one"),
        CheckConstraint(
            "direction IN ('up', 'down') OR direction IS NULL", name="ck_revisions_direction"
        ),
        CheckConstraint(
            "revision_type IN ('value_revision', 'became_available', 'became_missing')",
            name="ck_revisions_type",
        ),
        Index("ix_revision_history_series_date", "series_id", "revision_date"),
        Index("ix_revision_history_obs_num", "observation_id", "revision_number"),
    )

    observation: Mapped["Observation"] = relationship(
        "Observation", back_populates="revision_history"
    )
    series: Mapped["EconomicSeries"] = relationship(
        "EconomicSeries", back_populates="revision_history"
    )
    from_vintage: Mapped["ObservationVintage"] = relationship(
        "ObservationVintage", foreign_keys=[from_vintage_id], back_populates="from_revisions"
    )
    to_vintage: Mapped["ObservationVintage"] = relationship(
        "ObservationVintage", foreign_keys=[to_vintage_id], back_populates="to_revisions"
    )


class DataEvent(Base):
    """The atomic unit of system alerts driving push notifications."""

    __tablename__ = "data_events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    series_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("economic_series.id", ondelete="CASCADE"), nullable=False, index=True
    )
    observation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("observations.id", ondelete="CASCADE"), nullable=True
    )
    vintage_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    is_backfill: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('new_observation', 'revision_batch', 'anomaly', 'quality_issue')",
            name="ck_data_events_type",
        ),
    )

    series: Mapped["EconomicSeries"] = relationship("EconomicSeries", back_populates="data_events")
    observation: Mapped[Optional["Observation"]] = relationship(
        "Observation", back_populates="data_events"
    )
    notifications: Mapped[list["NotificationHistory"]] = relationship(
        "NotificationHistory", back_populates="event", cascade="all, delete-orphan"
    )


class AnomalyRecord(Base):
    """Flagged observations detected by statistical or machine learning models."""

    __tablename__ = "anomaly_records"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    series_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("economic_series.id", ondelete="CASCADE"), nullable=False, index=True
    )
    observation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("observations.id", ondelete="CASCADE"), nullable=False
    )
    vintage_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("observation_vintages.id", ondelete="CASCADE"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(30), nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    features: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="flagged", nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("vintage_id", "method", name="uq_anomaly_records_vintage_method"),
        CheckConstraint(
            "status IN ('flagged', 'reviewed', 'dismissed')", name="ck_anomaly_records_status"
        ),
        CheckConstraint(
            "method IN ('zscore', 'isolation_forest')", name="ck_anomaly_records_method"
        ),
    )

    series: Mapped["EconomicSeries"] = relationship(
        "EconomicSeries", back_populates="anomaly_records"
    )
    observation: Mapped["Observation"] = relationship(
        "Observation", back_populates="anomaly_records"
    )
    vintage: Mapped["ObservationVintage"] = relationship(
        "ObservationVintage", back_populates="anomaly_records"
    )


class DataQualityLog(Base):
    """Execution audit of each quality check per pipeline execution."""

    __tablename__ = "data_quality_logs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    run_uuid: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    series_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("economic_series.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ingestion_run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ingestion_runs.id", ondelete="SET NULL"), nullable=True
    )
    check_name: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    affected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "severity IN ('info', 'warning', 'error')", name="ck_quality_logs_severity"
        ),
        Index("ix_data_quality_logs_series_checked", "series_id", "checked_at"),
    )

    series: Mapped["EconomicSeries"] = relationship(
        "EconomicSeries", back_populates="data_quality_logs"
    )
    ingestion_run: Mapped[Optional["IngestionRun"]] = relationship(
        "IngestionRun", back_populates="quality_logs"
    )


class DataQualityScore(Base):
    """Periodic composite quality health scores per economic series."""

    __tablename__ = "data_quality_scores"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    run_uuid: Mapped[str] = mapped_column(String(36), nullable=False)
    series_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("economic_series.id", ondelete="CASCADE"), nullable=False, index=True
    )
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    component_scores: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("run_uuid", "series_id", name="uq_quality_scores_run_series"),
    )

    series: Mapped["EconomicSeries"] = relationship(
        "EconomicSeries", back_populates="data_quality_scores"
    )


class Subscriber(Base):
    """Subscribed Telegram recipient for economic alerts."""

    __tablename__ = "subscribers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    telegram_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    preferences: Mapped[list["SubscriberPreference"]] = relationship(
        "SubscriberPreference", back_populates="subscriber", cascade="all, delete-orphan"
    )
    notifications: Mapped[list["NotificationHistory"]] = relationship(
        "NotificationHistory", back_populates="subscriber", cascade="all, delete-orphan"
    )


class SubscriberPreference(Base):
    """Per-subscriber notification filters and thresholds per indicator."""

    __tablename__ = "subscriber_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscriber_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("subscribers.id", ondelete="CASCADE"), nullable=False
    )
    series_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("economic_series.id", ondelete="CASCADE"), nullable=False
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_new_observation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notify_revision: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_anomaly: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_quality_issue: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    min_revision_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)

    __table_args__ = (
        UniqueConstraint("subscriber_id", "series_id", name="uq_subscriber_preferences_sub_series"),
    )

    subscriber: Mapped["Subscriber"] = relationship("Subscriber", back_populates="preferences")
    series: Mapped["EconomicSeries"] = relationship(
        "EconomicSeries", back_populates="subscriber_preferences"
    )

    @property
    def series_code(self) -> str:
        """Return the economic series code if loaded."""
        return self.series.code if self.series else ""


class NotificationHistory(Base):
    """Audit ledger of all generated notification alerts and dispatch attempts."""

    __tablename__ = "notification_history"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    subscriber_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("subscribers.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("data_events.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(20), default="telegram", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "subscriber_id", "event_id", "channel", name="uq_notification_history_sub_event_chan"
        ),
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'skipped')", name="ck_notification_status"
        ),
    )

    subscriber: Mapped["Subscriber"] = relationship("Subscriber", back_populates="notifications")
    event: Mapped["DataEvent"] = relationship("DataEvent", back_populates="notifications")


class IngestionRun(Base):
    """Audit header tracking an ingestion execution."""

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    series_requested: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    series_succeeded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_fetched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    git_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)

    __table_args__ = (
        CheckConstraint("mode IN ('api', 'snapshot')", name="ck_ingestion_runs_mode"),
        CheckConstraint(
            "trigger IN ('manual', 'scheduled', 'dashboard', 'cli')",
            name="ck_ingestion_runs_trigger",
        ),
        CheckConstraint(
            "status IN ('running', 'success', 'partial', 'failed')", name="ck_ingestion_runs_status"
        ),
    )

    series_runs: Mapped[list["IngestionRunSeries"]] = relationship(
        "IngestionRunSeries", back_populates="run", cascade="all, delete-orphan"
    )
    observations: Mapped[list["Observation"]] = relationship(
        "Observation", back_populates="ingestion_run"
    )
    vintages: Mapped[list["ObservationVintage"]] = relationship(
        "ObservationVintage", back_populates="ingestion_run"
    )
    quality_logs: Mapped[list["DataQualityLog"]] = relationship(
        "DataQualityLog", back_populates="ingestion_run"
    )


class IngestionRunSeries(Base):
    """Detailed series-level breakdown of an ingestion execution."""

    __tablename__ = "ingestion_run_series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False
    )
    series_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("economic_series.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    vintages_fetched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    observations_new: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    vintages_new: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    vintages_unchanged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    missing_values: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "series_id", name="uq_ingestion_run_series_run_series"),
        CheckConstraint(
            "status IN ('success', 'skipped_no_new_data', 'failed')",
            name="ck_ingestion_run_series_status",
        ),
    )

    run: Mapped["IngestionRun"] = relationship("IngestionRun", back_populates="series_runs")
    series: Mapped["EconomicSeries"] = relationship(
        "EconomicSeries", back_populates="ingestion_run_series"
    )


class AppState(Base):
    """Key-value operational state storage (Telegram offsets, calculation baselines)."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
