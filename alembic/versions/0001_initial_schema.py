"""Initial database schema and latest_observation_values view

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-06 17:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIEW_NAME = "latest_observation_values"

VIEW_SQL = """
CREATE OR REPLACE VIEW latest_observation_values AS
WITH ranked_vintages AS (
    SELECT
        ov.observation_id,
        ov.vintage_date,
        ov.value,
        ROW_NUMBER() OVER (PARTITION BY ov.observation_id ORDER BY ov.vintage_date ASC) AS asc_rank,
        ROW_NUMBER() OVER (
            PARTITION BY ov.observation_id ORDER BY ov.vintage_date DESC
        ) AS desc_rank,
        COUNT(*) OVER (PARTITION BY ov.observation_id) AS v_count
    FROM observation_vintages ov
)
SELECT
    o.id AS observation_id,
    o.series_id,
    o.observation_date,
    fv.vintage_date AS first_vintage_date,
    fv.value AS first_value,
    lv.vintage_date AS latest_vintage_date,
    lv.value AS latest_value,
    COALESCE(lv.v_count, 0) AS vintage_count
FROM observations o
LEFT JOIN ranked_vintages fv ON o.id = fv.observation_id AND fv.asc_rank = 1
LEFT JOIN ranked_vintages lv ON o.id = lv.observation_id AND lv.desc_rank = 1;
"""

DROP_VIEW_SQL = f"DROP VIEW IF EXISTS {VIEW_NAME};"


def upgrade() -> None:
    # 1. economic_series
    op.create_table(
        "economic_series",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("fred_series_id", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("frequency", sa.String(length=10), nullable=False),
        sa.Column("units", sa.String(length=100), nullable=False),
        sa.Column("seasonal_adjustment", sa.String(length=100), nullable=False),
        sa.Column("change_unit_label", sa.String(length=100), nullable=True),
        sa.Column("anomaly_transform", sa.String(length=20), nullable=False),
        sa.Column("expected_min", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("expected_max", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("max_stale_lag_days", sa.Integer(), nullable=False),
        sa.Column("initial_release_max_lag_days", sa.Integer(), nullable=False),
        sa.Column("history_start", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("fred_last_updated", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "expected_min < expected_max", name=op.f("ck_economic_series_ck_series_range")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_economic_series")),
        sa.UniqueConstraint("code", name=op.f("uq_economic_series_code")),
        sa.UniqueConstraint("fred_series_id", name=op.f("uq_economic_series_fred_series_id")),
    )
    op.create_index(op.f("ix_economic_series_code"), "economic_series", ["code"], unique=True)

    # 2. ingestion_runs
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_uuid", sa.String(length=36), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("series_requested", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("series_succeeded", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_fetched", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_inserted", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("snapshot_manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("git_commit", sa.String(length=40), nullable=True),
        sa.CheckConstraint(
            "mode IN ('api', 'snapshot')", name=op.f("ck_ingestion_runs_ck_ingestion_runs_mode")
        ),
        sa.CheckConstraint(
            "trigger IN ('manual', 'scheduled', 'dashboard', 'cli')",
            name=op.f("ck_ingestion_runs_ck_ingestion_runs_trigger"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'partial', 'failed')",
            name=op.f("ck_ingestion_runs_ck_ingestion_runs_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_runs")),
        sa.UniqueConstraint("run_uuid", name=op.f("uq_ingestion_runs_run_uuid")),
    )
    op.create_index(op.f("ix_ingestion_runs_run_uuid"), "ingestion_runs", ["run_uuid"], unique=True)

    # 3. ingestion_run_series
    op.create_table(
        "ingestion_run_series",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("vintages_fetched", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("observations_new", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("vintages_new", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("vintages_unchanged", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("missing_values", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('success', 'skipped_no_new_data', 'failed')",
            name=op.f("ck_ingestion_run_series_ck_ingestion_run_series_status"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["ingestion_runs.id"],
            name=op.f("fk_ingestion_run_series_run_id_ingestion_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["economic_series.id"],
            name=op.f("fk_ingestion_run_series_series_id_economic_series"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_run_series")),
        sa.UniqueConstraint("run_id", "series_id", name=op.f("uq_ingestion_run_series_run_id")),
    )

    # 4. observations
    op.create_table(
        "observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingestion_run_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_runs.id"],
            name=op.f("fk_observations_ingestion_run_id_ingestion_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["economic_series.id"],
            name=op.f("fk_observations_series_id_economic_series"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_observations")),
        sa.UniqueConstraint(
            "series_id", "observation_date", name=op.f("uq_observations_series_id")
        ),
    )
    op.create_index(
        op.f("ix_observations_observation_date"), "observations", ["observation_date"], unique=False
    )
    op.create_index(op.f("ix_observations_series_id"), "observations", ["series_id"], unique=False)

    # 5. observation_vintages
    op.create_table(
        "observation_vintages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("observation_id", sa.Integer(), nullable=False),
        sa.Column("vintage_date", sa.Date(), nullable=False),
        sa.Column("source_realtime_end", sa.Date(), nullable=True),
        sa.Column("value", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("is_missing", sa.Boolean(), nullable=False),
        sa.Column("ingestion_run_id", sa.Integer(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.CheckConstraint(
            "(is_missing = TRUE AND value IS NULL) OR (is_missing = FALSE AND value IS NOT NULL)",
            name=op.f("ck_observation_vintages_ck_vintages_missing_value"),
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_runs.id"],
            name=op.f("fk_observation_vintages_ingestion_run_id_ingestion_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["observations.id"],
            name=op.f("fk_observation_vintages_observation_id_observations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_observation_vintages")),
        sa.UniqueConstraint(
            "observation_id", "vintage_date", name=op.f("uq_observation_vintages_observation_id")
        ),
    )
    op.create_index(
        op.f("ix_observation_vintages_observation_id"),
        "observation_vintages",
        ["observation_id"],
        unique=False,
    )
    op.create_index(
        "ix_observation_vintages_obs_vintage_desc",
        "observation_vintages",
        ["observation_id", sa.text("vintage_date DESC")],
        unique=False,
    )

    # 6. revision_history
    op.create_table(
        "revision_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("observation_id", sa.Integer(), nullable=False),
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("from_vintage_id", sa.BigInteger(), nullable=False),
        sa.Column("to_vintage_id", sa.BigInteger(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("revision_date", sa.Date(), nullable=False),
        sa.Column("previous_value", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("new_value", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("absolute_revision", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("relative_revision_pct", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("direction", sa.String(length=10), nullable=True),
        sa.Column("revision_type", sa.String(length=30), nullable=False),
        sa.Column("revision_lag_days", sa.Integer(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "revision_number >= 1", name=op.f("ck_revision_history_ck_revisions_number_ge_one")
        ),
        sa.CheckConstraint(
            "direction IN ('up', 'down') OR direction IS NULL",
            name=op.f("ck_revision_history_ck_revisions_direction"),
        ),
        sa.CheckConstraint(
            "revision_type IN ('value_revision', 'became_available', 'became_missing')",
            name=op.f("ck_revision_history_ck_revisions_type"),
        ),
        sa.ForeignKeyConstraint(
            ["from_vintage_id"],
            ["observation_vintages.id"],
            name=op.f("fk_revision_history_from_vintage_id_observation_vintages"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["observations.id"],
            name=op.f("fk_revision_history_observation_id_observations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["economic_series.id"],
            name=op.f("fk_revision_history_series_id_economic_series"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_vintage_id"],
            ["observation_vintages.id"],
            name=op.f("fk_revision_history_to_vintage_id_observation_vintages"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_revision_history")),
        sa.UniqueConstraint("to_vintage_id", name=op.f("uq_revision_history_to_vintage_id")),
    )
    op.create_index(
        "ix_revision_history_obs_num",
        "revision_history",
        ["observation_id", "revision_number"],
        unique=False,
    )
    op.create_index(
        "ix_revision_history_series_date",
        "revision_history",
        ["series_id", "revision_date"],
        unique=False,
    )

    # 7. data_events
    op.create_table(
        "data_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("observation_id", sa.Integer(), nullable=True),
        sa.Column("vintage_date", sa.Date(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_backfill", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('new_observation', 'revision_batch', 'anomaly', 'quality_issue')",
            name=op.f("ck_data_events_ck_data_events_type"),
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["observations.id"],
            name=op.f("fk_data_events_observation_id_observations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["economic_series.id"],
            name=op.f("fk_data_events_series_id_economic_series"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_events")),
        sa.UniqueConstraint("event_key", name=op.f("uq_data_events_event_key")),
    )
    op.create_index(op.f("ix_data_events_event_key"), "data_events", ["event_key"], unique=True)
    op.create_index(op.f("ix_data_events_series_id"), "data_events", ["series_id"], unique=False)

    # 8. anomaly_records
    op.create_table(
        "anomaly_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("observation_id", sa.Integer(), nullable=False),
        sa.Column("vintage_id", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(length=30), nullable=False),
        sa.Column("score", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("threshold", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default=sa.text("'flagged'")
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "method IN ('zscore', 'isolation_forest')",
            name=op.f("ck_anomaly_records_ck_anomaly_records_method"),
        ),
        sa.CheckConstraint(
            "status IN ('flagged', 'reviewed', 'dismissed')",
            name=op.f("ck_anomaly_records_ck_anomaly_records_status"),
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["observations.id"],
            name=op.f("fk_anomaly_records_observation_id_observations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["economic_series.id"],
            name=op.f("fk_anomaly_records_series_id_economic_series"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["vintage_id"],
            ["observation_vintages.id"],
            name=op.f("fk_anomaly_records_vintage_id_observation_vintages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anomaly_records")),
        sa.UniqueConstraint("vintage_id", "method", name=op.f("uq_anomaly_records_vintage_id")),
    )
    op.create_index(
        op.f("ix_anomaly_records_series_id"), "anomaly_records", ["series_id"], unique=False
    )

    # 9. data_quality_logs
    op.create_table(
        "data_quality_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_uuid", sa.String(length=36), nullable=False),
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("ingestion_run_id", sa.Integer(), nullable=True),
        sa.Column("check_name", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("affected_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error')",
            name=op.f("ck_data_quality_logs_ck_quality_logs_severity"),
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_runs.id"],
            name=op.f("fk_data_quality_logs_ingestion_run_id_ingestion_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["economic_series.id"],
            name=op.f("fk_data_quality_logs_series_id_economic_series"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_quality_logs")),
    )
    op.create_index(
        "ix_data_quality_logs_series_checked",
        "data_quality_logs",
        ["series_id", "checked_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_data_quality_logs_run_uuid"), "data_quality_logs", ["run_uuid"], unique=False
    )
    op.create_index(
        op.f("ix_data_quality_logs_series_id"), "data_quality_logs", ["series_id"], unique=False
    )

    # 10. data_quality_scores
    op.create_table(
        "data_quality_scores",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_uuid", sa.String(length=36), nullable=False),
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("component_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["economic_series.id"],
            name=op.f("fk_data_quality_scores_series_id_economic_series"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_quality_scores")),
        sa.UniqueConstraint("run_uuid", "series_id", name=op.f("uq_data_quality_scores_run_uuid")),
    )
    op.create_index(
        op.f("ix_data_quality_scores_series_id"), "data_quality_scores", ["series_id"], unique=False
    )

    # 11. subscribers
    op.create_table(
        "subscribers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_username", sa.String(length=100), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscribers")),
        sa.UniqueConstraint("telegram_chat_id", name=op.f("uq_subscribers_telegram_chat_id")),
    )
    op.create_index(
        op.f("ix_subscribers_telegram_chat_id"), "subscribers", ["telegram_chat_id"], unique=True
    )

    # 12. subscriber_preferences
    op.create_table(
        "subscriber_preferences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("subscriber_id", sa.Integer(), nullable=False),
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "notify_new_observation", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("notify_revision", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notify_anomaly", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "notify_quality_issue", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("min_revision_pct", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["economic_series.id"],
            name=op.f("fk_subscriber_preferences_series_id_economic_series"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subscriber_id"],
            ["subscribers.id"],
            name=op.f("fk_subscriber_preferences_subscriber_id_subscribers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriber_preferences")),
        sa.UniqueConstraint(
            "subscriber_id", "series_id", name=op.f("uq_subscriber_preferences_subscriber_id")
        ),
    )

    # 13. notification_history
    op.create_table(
        "notification_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("subscriber_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "channel", sa.String(length=20), nullable=False, server_default=sa.text("'telegram'")
        ),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'skipped')",
            name=op.f("ck_notification_history_ck_notification_status"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["data_events.id"],
            name=op.f("fk_notification_history_event_id_data_events"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subscriber_id"],
            ["subscribers.id"],
            name=op.f("fk_notification_history_subscriber_id_subscribers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_history")),
        sa.UniqueConstraint(
            "subscriber_id",
            "event_id",
            "channel",
            name=op.f("uq_notification_history_subscriber_id"),
        ),
    )

    # 14. app_state
    op.create_table(
        "app_state",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_state")),
    )

    # 15. View: latest_observation_values
    op.execute(VIEW_SQL)


def downgrade() -> None:
    # 1. Drop View
    op.execute(DROP_VIEW_SQL)

    # 2. Drop tables in reverse topological order
    op.drop_table("app_state")
    op.drop_table("notification_history")
    op.drop_table("subscriber_preferences")
    op.drop_table("subscribers")
    op.drop_table("data_quality_scores")
    op.drop_table("data_quality_logs")
    op.drop_table("anomaly_records")
    op.drop_table("data_events")
    op.drop_table("revision_history")
    op.drop_table("observation_vintages")
    op.drop_table("observations")
    op.drop_table("ingestion_run_series")
    op.drop_table("ingestion_runs")
    op.drop_table("economic_series")
