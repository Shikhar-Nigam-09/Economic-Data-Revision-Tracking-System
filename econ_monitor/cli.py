"""Command-line interface (CLI) for the Economic Data Monitoring system."""

import argparse
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from sqlalchemy import select, text

from econ_monitor.anomalies.service import run_anomaly_detection
from econ_monitor.core.clock import get_utc_now
from econ_monitor.core.config import get_settings
from econ_monitor.core.errors import ConfigurationError
from econ_monitor.core.logging import setup_logging
from econ_monitor.core.yaml_config import load_indicators_config
from econ_monitor.db.models import AppState, IngestionMode, IngestionTrigger, Subscriber
from econ_monitor.db.repositories import seed_economic_series
from econ_monitor.db.session import get_engine, session_scope
from econ_monitor.ingestion.fred_client import FredClient
from econ_monitor.ingestion.service import (
    DatabaseSink,
    IngestionService,
    SnapshotSink,
)
from econ_monitor.ingestion.storage import update_series_fred_metadata
from econ_monitor.notifications.bot_commands import handle_update
from econ_monitor.notifications.dispatcher import dispatch_notifications
from econ_monitor.notifications.telegram_client import TelegramClient
from econ_monitor.pipeline import run_pipeline

# Registry for subcommands to allow seamless multi-phase extension
CommandFunc = Callable[[argparse.Namespace], int]
COMMAND_REGISTRY: Dict[str, Dict[str, object]] = {}


def register_command(name: str, help_text: str):
    """Decorator to register CLI subcommands."""

    def decorator(func: CommandFunc):
        COMMAND_REGISTRY[name] = {"func": func, "help": help_text}
        return func

    return decorator


@register_command(
    "db-check", "Verify PostgreSQL connectivity, server version, and applied migrations."
)
def cmd_db_check(args: argparse.Namespace) -> int:
    """Verify database connectivity, server version, and migration state."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            server_version = conn.execute(text("SELECT version();")).scalar()
            migration_version = None
            try:
                migration_version = conn.execute(
                    text("SELECT version_num FROM alembic_version;")
                ).scalar()
            except Exception:
                migration_version = "None (alembic_version table not found)"

        print("=== Database Health Check: SUCCESS ===")
        print("Status: Connected")
        print(f"PostgreSQL Version: {server_version}")
        print(f"Applied Migration Head: {migration_version}")
        return 0
    except ConfigurationError as exc:
        print("=== Database Health Check: MISCONFIGURATION ===", file=sys.stderr)
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print("=== Database Health Check: FAILURE ===", file=sys.stderr)
        print(f"Error connecting to database: {exc}", file=sys.stderr)
        return 1


@register_command(
    "seed-series", "Idempotently seed economic series definitions from config/indicators.yaml."
)
def cmd_seed_series(args: argparse.Namespace) -> int:
    """Idempotently seed indicators from YAML configuration."""
    config_file = getattr(args, "config", "config/indicators.yaml")
    try:
        config = load_indicators_config(config_file)
    except ConfigurationError as exc:
        print("=== Seed Series: MISCONFIGURATION ===", file=sys.stderr)
        print(f"Configuration error: {exc.message}", file=sys.stderr)
        return 2

    try:
        with session_scope() as session:
            inserted, updated = seed_economic_series(session, config)

        print("=== Seed Series: SUCCESS ===")
        print(f"Configuration file: {config_file}")
        print(f"Total indicators defined: {len(config.indicators)}")
        print(f"New indicators inserted: {inserted}")
        print(f"Existing indicators updated: {updated}")
        active_count = sum(1 for ind in config.indicators if ind.active)
        inactive_count = len(config.indicators) - active_count
        print(f"Active indicators: {active_count} | Inactive indicators: {inactive_count}")
        return 0
    except ConfigurationError as exc:
        print("=== Seed Series: MISCONFIGURATION ===", file=sys.stderr)
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print("=== Seed Series: FAILURE ===", file=sys.stderr)
        print(f"Error seeding indicators into database: {exc}", file=sys.stderr)
        return 1


@register_command("verify-series", "Verify active indicators against live FRED API metadata.")
def cmd_verify_series(args: argparse.Namespace) -> int:
    """Verify active indicators against FRED metadata and empirical ALFRED semantics."""
    config_file = getattr(args, "config", "config/indicators.yaml")
    try:
        config = load_indicators_config(config_file)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc.message}", file=sys.stderr)
        return 2

    try:
        client = FredClient()
    except ConfigurationError as exc:
        print("=== Verify Series: MISCONFIGURATION ===", file=sys.stderr)
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2

    print("=== FRED Series Live Verification ===")
    verified_count = 0
    errors = []

    # Optional DB session to update metadata if DATABASE_URL is configured
    session = None
    try:
        settings = get_settings()
        if settings.DATABASE_URL and settings.DATABASE_URL.get_secret_value().strip():
            engine = get_engine()
            session = session_scope(engine).__enter__()
    except Exception:
        session = None

    for ind in [i for i in config.indicators if i.active]:
        try:
            meta = client.get_series_metadata(ind.fred_series_id)
            vintages = client.get_vintage_dates(ind.fred_series_id)

            print(f"\nIndicator: {ind.code} (FRED ID: {ind.fred_series_id})")
            print(f"  Title: {meta.get('title')}")
            print(f"  Frequency: {meta.get('frequency_short')} (Config expected: M/Q)")
            print(f"  Units: {meta.get('units')}")
            print(f"  Seasonal Adjustment: {meta.get('seasonal_adjustment')}")
            print(f"  Last Updated: {meta.get('last_updated')}")
            print(f"  Total Historical Vintages: {len(vintages)}")
            if vintages:
                print(f"  Earliest Vintage: {vintages[0]} | Latest Vintage: {vintages[-1]}")

            if session:
                from econ_monitor.db.repositories import get_series_by_code

                series_obj = get_series_by_code(session, ind.code)
                if series_obj:
                    update_series_fred_metadata(session, series_obj, meta)

            verified_count += 1
        except Exception as exc:
            err_msg = f"Failed verification for {ind.code} ({ind.fred_series_id}): {exc}"
            print(f"  ERROR: {err_msg}", file=sys.stderr)
            errors.append(err_msg)

    if session:
        try:
            session.commit()
            session.close()
        except Exception:
            pass

    print("\n--------------------------------------------------")
    print(f"Verification Summary: {verified_count} verified, {len(errors)} failed.")
    return 0 if not errors else 1


@register_command("snapshot", "Harvest historical series from FRED/ALFRED into Parquet snapshots.")
def cmd_snapshot(args: argparse.Namespace) -> int:
    """Harvest data from FRED into Parquet snapshots without requiring a database."""
    config_file = getattr(args, "config", "config/indicators.yaml")
    out_dir = getattr(args, "out_dir", "data/snapshots")
    series_codes = getattr(args, "series", None)
    force_full = getattr(args, "full", False)

    try:
        config = load_indicators_config(config_file)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc.message}", file=sys.stderr)
        return 2

    try:
        client = FredClient()
    except ConfigurationError as exc:
        print("=== Snapshot Ingestion: MISCONFIGURATION ===", file=sys.stderr)
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2

    sink = SnapshotSink(snapshots_dir=out_dir)
    service = IngestionService(fred_client=client)

    print("=== Snapshot Collection Starting ===")
    print(f"Output directory: {Path(out_dir).resolve()}")
    print(f"Target series: {series_codes or 'All active'}")
    print(f"Mode: {'Full re-fetch' if force_full else 'Incremental'}")

    result = service.run_ingestion(
        indicators_config=config,
        sink=sink,
        selected_series_codes=series_codes,
        force_full=force_full,
        mode=IngestionMode.SNAPSHOT,
        trigger=IngestionTrigger.CLI,
    )

    print("\n=== Snapshot Collection Results ===")
    print(f"Status: {result.status.upper()}")
    print(f"Series Requested: {result.series_requested} | Succeeded: {result.series_succeeded}")
    print(f"Total Rows Fetched: {result.rows_fetched} | New Rows Added: {result.rows_inserted}")

    for code, stat in result.series_stats.items():
        print(
            f"  {code} ({stat.fred_series_id}): status={stat.status}, "
            f"fetched={stat.vintages_fetched}, added={stat.vintages_new}, "
            f"unchanged={stat.vintages_unchanged}, missing={stat.missing_values}"
            + (f" [Error: {stat.error_message}]" if stat.error_message else "")
        )

    return 0 if result.status in ("success", "partial") else 1


@register_command(
    "ingest", "Ingest historical observations directly from FRED/ALFRED into PostgreSQL."
)
def cmd_ingest(args: argparse.Namespace) -> int:
    """Ingest series directly from FRED/ALFRED into PostgreSQL."""
    config_file = getattr(args, "config", "config/indicators.yaml")
    series_codes = getattr(args, "series", None)
    force_full = getattr(args, "full", False)
    dry_run = getattr(args, "dry_run", False)

    try:
        config = load_indicators_config(config_file)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc.message}", file=sys.stderr)
        return 2

    try:
        client = FredClient()
    except ConfigurationError as exc:
        print("=== Direct Ingest: MISCONFIGURATION ===", file=sys.stderr)
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2

    try:
        with session_scope() as session:
            sink = DatabaseSink(session=session, ingestion_run_id=None, run_uuid="direct_cli")
            service = IngestionService(fred_client=client)

            print("=== Direct Ingestion Starting ===")
            print(f"Target series: {series_codes or 'All active'}")
            print(f"Mode: {'Full re-fetch' if force_full else 'Incremental'}")
            if dry_run:
                print("DRY RUN: No database changes will be committed.")

            result = service.run_ingestion(
                indicators_config=config,
                sink=sink,
                selected_series_codes=series_codes,
                force_full=force_full,
                mode=IngestionMode.API,
                trigger=IngestionTrigger.CLI,
                session=session,
            )

            if dry_run:
                session.rollback()

        print("\n=== Direct Ingestion Results ===")
        print(f"Status: {result.status.upper()}")
        print(f"Series Requested: {result.series_requested} | Succeeded: {result.series_succeeded}")
        print(
            f"Rows Fetched: {result.rows_fetched} | New Vintages Inserted: {result.rows_inserted}"
        )

        for code, stat in result.series_stats.items():
            print(
                f"  {code} ({stat.fred_series_id}): status={stat.status}, "
                f"fetched={stat.vintages_fetched}, new_obs={stat.observations_new}, "
                f"new_vintages={stat.vintages_new}, unchanged={stat.vintages_unchanged}, "
                f"missing={stat.missing_values}"
                + (f" [Error: {stat.error_message}]" if stat.error_message else "")
            )

        return 0 if result.status in ("success", "partial") else 1

    except ConfigurationError as exc:
        print("=== Direct Ingest: MISCONFIGURATION ===", file=sys.stderr)
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print("=== Direct Ingest: FAILURE ===", file=sys.stderr)
        print(f"Ingestion error: {exc}", file=sys.stderr)
        return 1


@register_command("import-snapshots", "Import Parquet snapshots and manifest into PostgreSQL.")
def cmd_import_snapshots(args: argparse.Namespace) -> int:
    """Verify and import Parquet snapshots into the local database."""
    snapshots_dir = getattr(args, "dir", "data/snapshots")

    try:
        with session_scope() as session:
            service = IngestionService()
            print(f"=== Importing Snapshots from {Path(snapshots_dir).resolve()} ===")
            result = service.import_snapshots_to_database(
                session=session,
                snapshots_dir=snapshots_dir,
                trigger=IngestionTrigger.CLI,
            )

        print("\n=== Snapshot Import Results ===")
        print(f"Status: {result.status.upper()}")
        print(f"Series Requested: {result.series_requested} | Succeeded: {result.series_succeeded}")
        print(
            f"Total Rows Processed: {result.rows_fetched} | "
            f"New Vintages Inserted: {result.rows_inserted}"
        )

        for series_id, stat in result.series_stats.items():
            print(
                f"  {series_id}: status={stat.status}, "
                f"total_vintages={stat.vintages_fetched}, new_obs={stat.observations_new}, "
                f"new_vintages={stat.vintages_new}, unchanged={stat.vintages_unchanged}"
                + (f" [Error: {stat.error_message}]" if stat.error_message else "")
            )

        return 0 if result.status in ("success", "partial") else 1

    except ConfigurationError as exc:
        print("=== Import Snapshots: MISCONFIGURATION ===", file=sys.stderr)
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print("=== Import Snapshots: FAILURE ===", file=sys.stderr)
        print(f"Import error: {exc}", file=sys.stderr)
        return 1


@register_command(
    "detect-revisions",
    "Detect new observations and revisions from stored vintages, recording events and boundaries.",
)
def cmd_detect_revisions(args: argparse.Namespace) -> int:
    """Detect new and revised observations from stored vintages and record data events."""
    from econ_monitor.revisions.detector import detect_revisions

    try:
        with session_scope() as session:
            series_codes = [c.upper() for c in args.series] if args.series else None
            result = detect_revisions(session=session, series_codes=series_codes)

        print("=== Revision Detection Summary ===")
        print(f"Total Series Processed: {len(result.series_results)}")
        print(f"Total New Observations Detected: {result.total_new_observations}")
        print(f"Total Revisions Created: {result.total_revisions_created}")
        print("-" * 60)

        for code, counts in result.series_results.items():
            print(f"Indicator: {code}")
            print(f"  New Observations: {counts.new_observations_count}")
            print(f"  Revised Observations: {counts.revised_observations_count}")
            print(f"  Unchanged Observations: {counts.unchanged_observations_count}")
            print(f"  Missing Observations: {counts.missing_observations_count}")
            print(
                f"  Revisions Created: {counts.revisions_created_count} "
                f"(Up: {counts.upward_revisions_count}, Down: {counts.downward_revisions_count}, "
                f"Avail: {counts.became_available_count}, Miss: {counts.became_missing_count})"
            )
            print("-" * 60)

        return 0

    except ConfigurationError as exc:
        print("=== Revision Detection: MISCONFIGURATION ===", file=sys.stderr)
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print("=== Revision Detection: FAILURE ===", file=sys.stderr)
        print(f"Detection error: {exc}", file=sys.stderr)
        return 1


@register_command(
    "quality-run",
    "Run data quality framework (A9 checks), emit quality logs and health scores.",
)
def cmd_quality_run(args: argparse.Namespace) -> int:
    """Execute data quality validation framework and record health scores."""
    from econ_monitor.core.yaml_config import load_monitoring_config
    from econ_monitor.quality.service import run_data_quality_service

    try:
        config_path = (
            args.config if hasattr(args, "config") and args.config else "config/monitoring.yaml"
        )
        monitoring_config = load_monitoring_config(config_path)

        with session_scope() as session:
            series_codes = [c.upper() for c in args.series] if args.series else None
            result = run_data_quality_service(
                session=session,
                series_codes=series_codes,
                monitoring_config=monitoring_config,
            )

        print(f"=== Data Quality Evaluation Summary (Run: {result.run_uuid}) ===")
        print(f"Total Checks Evaluated: {result.total_checks_evaluated}")
        print(f"Total Quality Issue Events Emitted: {result.total_issues_emitted}")
        print("-" * 70)
        for code, rep in result.series_reports.items():
            print(f"Indicator: {code} | Composite Health Score: {rep.composite_score}/100")
            print(f"  Component Scores: {rep.component_scores}")
            print(f"  Checks: {rep.checks_passed} Passed, {rep.checks_failed} Failed")

            if rep.checks_failed > 0:
                print("  Failed Checks:")
                for chk_name, chk_res in rep.check_results.items():
                    if not chk_res.passed:
                        print(f"    - [{chk_res.severity.upper()}] {chk_name}: {chk_res.message}")
            print("-" * 70)

        return 0

    except ConfigurationError as exc:
        print("=== Data Quality: MISCONFIGURATION ===", file=sys.stderr)
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print("=== Data Quality: FAILURE ===", file=sys.stderr)
        print(f"Quality run error: {exc}", file=sys.stderr)
        return 1


@register_command("anomaly-run", "Run z-score and Isolation Forest anomaly detection.")
def cmd_anomaly_run(args: argparse.Namespace) -> int:
    """Execute anomaly detection algorithms across active indicators."""
    series_codes = getattr(args, "series", None)
    target_code = series_codes[0] if series_codes and len(series_codes) == 1 else None

    try:
        with session_scope() as session:
            result = run_anomaly_detection(session=session, series_code=target_code)
            session.commit()

        print("=== Anomaly Detection Run Summary ===")
        print(f"Run Executed At: {result.run_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"Total Flags Detected: {result.total_flagged}")
        print(f"New Anomaly Records Saved: {result.total_new_records}")
        print(f"Events Emitted: {result.total_events_emitted}")
        print("-" * 70)
        for code, rep in result.series_reports.items():
            print(f"Indicator: {code}")
            print(
                f"  Z-Score: Ran={rep.zscore_ran}, Flagged={rep.zscore_flagged}, "
                f"New Records={rep.zscore_new_records}"
            )
            if rep.iforest_ran:
                print(
                    f"  Isolation Forest: Ran=True, Flagged={rep.iforest_flagged}, "
                    f"New Records={rep.iforest_new_records}"
                )
            else:
                print(f"  Isolation Forest: Ran=False (Skipped: {rep.iforest_skipped_reason})")
            print(f"  Events Generated: {rep.events_emitted}")
            print("-" * 70)

        return 0

    except ConfigurationError as exc:
        print("=== Anomaly Detection: MISCONFIGURATION ===", file=sys.stderr)
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print("=== Anomaly Detection: FAILURE ===", file=sys.stderr)
        print(f"Anomaly run error: {exc}", file=sys.stderr)
        return 1


@register_command(
    "notify-dispatch", "Dispatch pending notification alerts to Telegram subscribers."
)
def cmd_notify_dispatch(args: argparse.Namespace) -> int:
    """Deliver alerts to active subscribers based on preferences."""
    dry_run = getattr(args, "dry_run", False)

    try:
        with session_scope() as session:
            result = dispatch_notifications(session=session, dry_run=dry_run)
            session.commit()

        print("=== Notification Dispatch Summary ===")
        print(f"Mode: {'Dry Run (Preview Only)' if result.dry_run else 'Live Delivery'}")
        print(f"Eligible Alert Events Found: {result.eligible_events_found}")
        print(f"Subscriber Matches: {result.matched_subscribers}")
        print(f"Sent Successfully: {result.sent_count}")
        print(f"Failed Delivery: {result.failed_count}")
        print(f"Skipped / Already Sent: {result.skipped_count}")
        print(f"Retried Previous Failures: {result.retried_count}")

        return 0

    except ConfigurationError as exc:
        print("=== Notification Dispatch: MISCONFIGURATION ===", file=sys.stderr)
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print("=== Notification Dispatch: FAILURE ===", file=sys.stderr)
        print(f"Dispatch error: {exc}", file=sys.stderr)
        return 1


@register_command(
    "telegram-poll", "Poll Telegram Bot API for updates and process interactive commands."
)
def cmd_telegram_poll(args: argparse.Namespace) -> int:
    """Long poll for Telegram subscriber updates and bot commands."""
    timeout = getattr(args, "timeout", 30)
    once = getattr(args, "once", False)

    try:
        settings = get_settings()
        token = settings.require_telegram_bot_token()
        client = TelegramClient(bot_token=token)

        offset: Optional[int] = None
        with session_scope() as session:
            state = session.get(AppState, "telegram_update_offset")
            if state and state.value and "offset" in state.value:
                offset = state.value["offset"]

        print("=== Starting Telegram Bot Long Polling ===")
        print(f"Polling timeout: {timeout}s | Single iteration: {once}")

        while True:
            try:
                updates = client.get_updates(offset=offset, timeout=timeout)
                if updates:
                    with session_scope() as session:
                        for u in updates:
                            u_id = u["update_id"]
                            offset = u_id + 1
                            handle_update(session=session, update=u, client=client)

                        s_obj = session.get(AppState, "telegram_update_offset")
                        val = {"offset": offset, "updated_at": get_utc_now().isoformat()}
                        if s_obj is None:
                            session.add(
                                AppState(
                                    key="telegram_update_offset",
                                    value=val,
                                    updated_at=get_utc_now(),
                                )
                            )
                        else:
                            s_obj.value = val
                            s_obj.updated_at = get_utc_now()
                        session.commit()
            except KeyboardInterrupt:
                print("\nPolling stopped by user.")
                break
            except Exception as exc:
                print(f"Polling warning: {exc}", file=sys.stderr)
                time.sleep(2.0)

            if once:
                break

        return 0

    except ConfigurationError as exc:
        print("=== Telegram Poll: MISCONFIGURATION ===", file=sys.stderr)
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print("=== Telegram Poll: FAILURE ===", file=sys.stderr)
        print(f"Polling error: {exc}", file=sys.stderr)
        return 1


@register_command("telegram-test", "Send a test alert message to verify Telegram bot credentials.")
def cmd_telegram_test(args: argparse.Namespace) -> int:
    """Send a verification message to confirm Telegram connectivity."""
    chat_id = getattr(args, "chat_id", None)

    try:
        settings = get_settings()
        token = settings.require_telegram_bot_token()
        client = TelegramClient(bot_token=token)

        target_chat_id: int
        if chat_id:
            target_chat_id = int(chat_id)
        elif settings.TELEGRAM_CHAT_ID:
            target_chat_id = int(settings.TELEGRAM_CHAT_ID)
        else:
            with session_scope() as session:
                sub = session.scalars(
                    select(Subscriber).where(Subscriber.is_active == True)  # noqa: E712
                ).first()
                if not sub:
                    raise ConfigurationError(
                        "No recipient chat ID specified. Provide --chat-id, configure "
                        "TELEGRAM_CHAT_ID in .env, or register a subscriber via /start in Telegram."
                    )
                target_chat_id = sub.telegram_chat_id

        test_msg = (
            "🔔 <b>Economic Data Monitoring Test Alert</b>\n\n"
            "This is a verification message from your local Economic Data Monitoring System.\n"
            "Telegram Bot API delivery is functioning correctly."
        )
        msg_ids = client.send_message(chat_id=target_chat_id, text=test_msg)
        msg_id_display = msg_ids[0] if msg_ids else "N/A"
        print(
            f"Test alert sent successfully to chat_id={target_chat_id}! "
            f"Telegram message_id={msg_id_display}"
        )
        return 0

    except ConfigurationError as exc:
        print("=== Telegram Test: MISCONFIGURATION ===", file=sys.stderr)
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print("=== Telegram Test: FAILURE ===", file=sys.stderr)
        print(f"Test alert error: {exc}", file=sys.stderr)
        return 1


@register_command(
    "sync",
    "Execute snapshot sync pipeline: import -> revisions -> quality -> anomalies -> notify.",
)
def cmd_sync(args: argparse.Namespace) -> int:
    """Execute complete snapshot pipeline importing Parquet files into PostgreSQL."""
    snapshots_dir = getattr(args, "dir", "data/snapshots")
    dry_run_notify = getattr(args, "dry_run_notify", False)
    no_notify = getattr(args, "no_notify", False)
    pull = getattr(args, "pull", False)

    if pull:
        print("=== Git Pull (--pull requested) ===")
        import subprocess

        try:
            pull_proc = subprocess.run(
                ["git", "pull", "--ff-only"],
                capture_output=True,
                text=True,
                check=False,
            )
            if pull_proc.returncode != 0:
                print("=== Git Pull Failed ===", file=sys.stderr)
                print(pull_proc.stderr or pull_proc.stdout, file=sys.stderr)
                return 1
            print(pull_proc.stdout.strip() or "Already up to date.")
        except Exception as pull_err:
            print(f"=== Git Pull Execution Error: {pull_err} ===", file=sys.stderr)
            return 1

    try:
        with session_scope() as session:
            result = run_pipeline(
                session=session,
                mode="snapshot",
                notify=not no_notify,
                dry_run_notify=dry_run_notify,
                snapshots_dir=snapshots_dir,
                trigger=IngestionTrigger.CLI,
            )

        print("\n=== Snapshot Sync Pipeline Execution Summary ===")
        print(f"Started At: {result.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"Finished At: {result.finished_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"Overall Result: {'SUCCESS' if result.success else 'FAILED'}")
        print("-" * 70)
        for s in result.steps:
            status_symbol = (
                "✅"
                if s.status == "success"
                else "⚠️"
                if s.status == "partial"
                else "⏭️"
                if s.status == "skipped"
                else "❌"
            )
            print(
                f"{status_symbol} Step: {s.step_name:<25} Status: {s.status.upper():<8} "
                f"Duration: {s.duration_seconds}s"
            )
            if s.details:
                print(f"   Details: {s.details}")
            if s.error:
                print(f"   Error: {s.error}")
        print("-" * 70)

        return 0 if result.success else 1

    except ConfigurationError as exc:
        print("=== Sync Pipeline: MISCONFIGURATION ===", file=sys.stderr)
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print("=== Sync Pipeline: FAILURE ===", file=sys.stderr)
        print(f"Pipeline error: {exc}", file=sys.stderr)
        return 1


@register_command(
    "pipeline",
    "Execute live API pipeline: ingest from FRED -> revisions -> quality -> anomalies -> notify.",
)
def cmd_pipeline(args: argparse.Namespace) -> int:
    """Execute complete live API pipeline against FRED and ALFRED."""
    series_codes = getattr(args, "series", None)
    no_notify = getattr(args, "no_notify", False)
    dry_run_notify = getattr(args, "dry_run_notify", False)

    try:
        with session_scope() as session:
            result = run_pipeline(
                session=session,
                mode="api",
                notify=not no_notify,
                series_codes=series_codes,
                dry_run_notify=dry_run_notify,
                trigger=IngestionTrigger.CLI,
            )

        print("\n=== Live API Pipeline Execution Summary ===")
        print(f"Started At: {result.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"Finished At: {result.finished_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"Overall Result: {'SUCCESS' if result.success else 'FAILED'}")
        print("-" * 70)
        for s in result.steps:
            status_symbol = (
                "✅"
                if s.status == "success"
                else "⚠️"
                if s.status == "partial"
                else "⏭️"
                if s.status == "skipped"
                else "❌"
            )
            print(
                f"{status_symbol} Step: {s.step_name:<25} Status: {s.status.upper():<8} "
                f"Duration: {s.duration_seconds}s"
            )
            if s.details:
                print(f"   Details: {s.details}")
            if s.error:
                print(f"   Error: {s.error}")
        print("-" * 70)

        return 0 if result.success else 1

    except ConfigurationError as exc:
        print("=== Live Pipeline: MISCONFIGURATION ===", file=sys.stderr)
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print("=== Live Pipeline: FAILURE ===", file=sys.stderr)
        print(f"Pipeline error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    """Build the root argument parser and register all configured subcommands."""
    parser = argparse.ArgumentParser(
        prog="econ-monitor",
        description="Economic Data Monitoring and Revision Tracking System CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, info in COMMAND_REGISTRY.items():
        subparser = subparsers.add_parser(name, help=info["help"])

        if name == "seed-series":
            subparser.add_argument(
                "--config",
                default="config/indicators.yaml",
                help="Path to indicators YAML configuration file",
            )
        elif name == "verify-series":
            subparser.add_argument(
                "--config",
                default="config/indicators.yaml",
                help="Path to indicators YAML configuration file",
            )
        elif name == "snapshot":
            subparser.add_argument(
                "--series",
                nargs="+",
                help="Specific series codes to snapshot (default: all active)",
            )
            subparser.add_argument(
                "--out-dir",
                default="data/snapshots",
                help="Output directory for Parquet snapshots and manifest",
            )
            subparser.add_argument(
                "--config",
                default="config/indicators.yaml",
                help="Path to indicators YAML configuration file",
            )
            subparser.add_argument(
                "--full",
                action="store_true",
                help="Force full re-fetch bypassing incremental checks",
            )
        elif name == "ingest":
            subparser.add_argument(
                "--series",
                nargs="+",
                help="Specific series codes to ingest (default: all active)",
            )
            subparser.add_argument(
                "--full",
                action="store_true",
                help="Force full re-fetch bypassing incremental checks",
            )
            subparser.add_argument(
                "--dry-run",
                action="store_true",
                help="Fetch and validate data without committing to database",
            )
            subparser.add_argument(
                "--config",
                default="config/indicators.yaml",
                help="Path to indicators YAML configuration file",
            )
        elif name == "import-snapshots":
            subparser.add_argument(
                "--dir",
                default="data/snapshots",
                help="Directory containing Parquet snapshots and manifest.json",
            )
        elif name == "detect-revisions":
            subparser.add_argument(
                "--series",
                nargs="+",
                help="Specific series codes to detect revisions for (default: all active)",
            )
        elif name == "quality-run":
            subparser.add_argument(
                "--series",
                nargs="+",
                help="Specific series codes to evaluate data quality for (default: all active)",
            )
            subparser.add_argument(
                "--config",
                default="config/monitoring.yaml",
                help="Path to monitoring YAML configuration file",
            )
        elif name == "anomaly-run":
            subparser.add_argument(
                "--series",
                nargs="+",
                help="Specific series codes to detect anomalies for (default: all active)",
            )
        elif name == "notify-dispatch":
            subparser.add_argument(
                "--dry-run",
                action="store_true",
                help="Preview notification matching and rendering without calling Telegram",
            )
        elif name == "telegram-poll":
            subparser.add_argument(
                "--timeout",
                type=int,
                default=30,
                help="Long polling timeout in seconds (default: 30)",
            )
            subparser.add_argument(
                "--once",
                action="store_true",
                help="Run a single poll iteration and exit",
            )
        elif name == "telegram-test":
            subparser.add_argument(
                "--chat-id",
                type=int,
                help="Target Telegram chat ID to receive test message",
            )
        elif name == "sync":
            subparser.add_argument(
                "--dir",
                default="data/snapshots",
                help="Directory containing Parquet snapshots (default: data/snapshots)",
            )
            subparser.add_argument(
                "--pull",
                action="store_true",
                help="Run 'git pull --ff-only' before importing snapshots",
            )
            subparser.add_argument(
                "--no-notify",
                action="store_true",
                help="Disable notification dispatching step",
            )
            subparser.add_argument(
                "--dry-run-notify",
                action="store_true",
                help="Run notification dispatch in dry-run mode (preview without sending)",
            )
        elif name == "pipeline":
            subparser.add_argument(
                "--series",
                nargs="+",
                help="Specific series codes to process (default: all active)",
            )
            subparser.add_argument(
                "--no-notify",
                action="store_true",
                help="Disable notification dispatching step",
            )
            subparser.add_argument(
                "--dry-run-notify",
                action="store_true",
                help="Run notification dispatch in dry-run mode (preview without sending)",
            )

    return parser


def main(argv: List[str] | None = None) -> int:
    """Entry point for CLI execution."""
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    handler_info = COMMAND_REGISTRY.get(args.command)
    if not handler_info:
        parser.print_help()
        return 1

    handler: CommandFunc = handler_info["func"]  # type: ignore
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
