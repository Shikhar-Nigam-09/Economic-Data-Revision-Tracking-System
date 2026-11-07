"""Telegram bot interactive commands parser and handler."""

from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from econ_monitor.core.errors import SeriesNotFoundError
from econ_monitor.core.logging import get_logger
from econ_monitor.db.models import EconomicSeries, SubscriberPreference
from econ_monitor.notifications.subscribers import (
    deactivate_subscriber,
    get_or_create_subscriber,
    get_subscriber,
    update_subscriber_preference,
)
from econ_monitor.notifications.telegram_client import TelegramClient, escape_html

logger = get_logger("econ_monitor.notifications.bot_commands")

HELP_TEXT = (
    "🤖 <b>Economic Data Monitor Bot Commands</b>\n\n"
    "• <code>/start</code> - Register and activate alerts\n"
    "• <code>/help</code> - Show this command reference\n"
    "• <code>/indicators</code> - List tracked economic indicators\n"
    "• <code>/subscribe &lt;CODE|all&gt;</code> - Enable alerts "
    "(e.g. <code>/subscribe GDP</code>)\n"
    "• <code>/unsubscribe &lt;CODE|all&gt;</code> - Disable alerts "
    "(e.g. <code>/unsubscribe all</code>)\n"
    "• <code>/preferences</code> - View current alert settings\n"
    "• <code>/status</code> - View subscription status\n"
    "• <code>/stop</code> - Pause and deactivate all alerts\n"
)


def handle_update(
    session: Session,
    update: Dict[str, Any],
    client: Optional[TelegramClient] = None,
) -> Optional[Tuple[int, str]]:
    """Process a single Telegram update dict and generate an HTML reply.

    Returns: (chat_id, response_text) or None if update is not a message.
    """
    msg = update.get("message")
    if not msg:
        return None

    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    if not chat_id:
        return None

    from_user = msg.get("from", {})
    username = from_user.get("username")
    first_name = from_user.get("first_name", "")
    last_name = from_user.get("last_name", "")
    display_name = f"{first_name} {last_name}".strip() or username

    raw_text = msg.get("text", "").strip()
    if not raw_text:
        return None

    parts = raw_text.split()
    cmd = parts[0].lower().split("@")[0]  # strip @botname if present
    args = parts[1:]

    reply: str

    if cmd == "/start":
        sub, created = get_or_create_subscriber(
            session=session,
            telegram_chat_id=chat_id,
            telegram_username=username,
            display_name=display_name,
        )
        action = "Welcome!" if created else "Welcome back!"
        reply = (
            f"👋 <b>{action}</b> You are subscribed to Economic Data Monitoring alerts.\n\n"
            "By default, you will receive notifications for historical <b>data revisions</b> "
            "and <b>anomalies</b> across tracked indicators.\n\n"
            f"{HELP_TEXT}"
        )

    elif cmd == "/help":
        reply = HELP_TEXT

    elif cmd == "/indicators":
        indicators = session.scalars(
            select(EconomicSeries)
            .where(EconomicSeries.is_active.is_(True))
            .order_by(EconomicSeries.code.asc())
        ).all()
        lines = ["📊 <b>Tracked Economic Indicators:</b>\n"]
        for ind in indicators:
            lines.append(
                f"• <b>{escape_html(ind.code)}</b> ({escape_html(ind.title or ind.display_name)})\n"
                f"  Frequency: {ind.frequency or 'N/A'} | Units: {escape_html(ind.units or 'N/A')}"
            )
        reply = "\n".join(lines)

    elif cmd == "/subscribe":
        if not args:
            reply = (
                "⚠️ <b>Usage:</b> <code>/subscribe &lt;CODE|all&gt;</code>\n"
                "Example: <code>/subscribe GDP</code> or <code>/subscribe all</code>"
            )
        else:
            target = args[0].upper()
            sub, _ = get_or_create_subscriber(
                session, chat_id, telegram_username=username, display_name=display_name
            )
            try:
                prefs = update_subscriber_preference(session, sub.id, target, is_enabled=True)
                if target == "ALL":
                    reply = f"✅ <b>Subscribed to all {len(prefs)} active indicators.</b>"
                else:
                    reply = f"✅ <b>Subscribed to alerts for {escape_html(target)}.</b>"
            except SeriesNotFoundError:
                reply = (
                    f"❌ Indicator <code>{escape_html(target)}</code> not found. "
                    "Use <code>/indicators</code> to view valid codes."
                )

    elif cmd == "/unsubscribe":
        if not args:
            reply = (
                "⚠️ <b>Usage:</b> <code>/unsubscribe &lt;CODE|all&gt;</code>\n"
                "Example: <code>/unsubscribe all</code> or <code>/unsubscribe CPI</code>"
            )
        else:
            target = args[0].upper()
            sub = get_subscriber(session, chat_id)
            if not sub:
                reply = "ℹ️ You are not registered. Use <code>/start</code> to begin."
            else:
                try:
                    prefs = update_subscriber_preference(session, sub.id, target, is_enabled=False)
                    if target == "ALL":
                        reply = f"🔕 <b>Unsubscribed from all {len(prefs)} indicators.</b>"
                    else:
                        reply = f"🔕 <b>Unsubscribed from alerts for {escape_html(target)}.</b>"
                except SeriesNotFoundError:
                    reply = f"❌ Indicator <code>{escape_html(target)}</code> not found."

    elif cmd == "/preferences":
        sub = get_subscriber(session, chat_id)
        if not sub:
            reply = "ℹ️ You are not registered. Send <code>/start</code> to begin."
        else:
            prefs = session.scalars(
                select(SubscriberPreference)
                .join(EconomicSeries, SubscriberPreference.series_id == EconomicSeries.id)
                .where(SubscriberPreference.subscriber_id == sub.id)
                .order_by(EconomicSeries.code.asc())
            ).all()

            lines = ["⚙️ <b>Your Notification Preferences:</b>\n"]
            for p in prefs:
                status_icon = "🟢" if p.is_enabled else "⚪"
                rev_icon = "✅" if p.notify_revision else "❌"
                anom_icon = "✅" if p.notify_anomaly else "❌"
                new_icon = "✅" if p.notify_new_observation else "❌"
                lines.append(
                    f"{status_icon} <b>{escape_html(p.series.code)}</b>\n"
                    f"   Revisions: {rev_icon} | Anomalies: {anom_icon} | New Obs: {new_icon}"
                )
            reply = "\n".join(lines)

    elif cmd == "/status":
        sub = get_subscriber(session, chat_id)
        if not sub or not sub.is_active:
            reply = "⚪ <b>Status:</b> Inactive. Send <code>/start</code> to activate alerts."
        else:
            enabled_count = session.scalars(
                select(SubscriberPreference).where(
                    SubscriberPreference.subscriber_id == sub.id,
                    SubscriberPreference.is_enabled == True,  # noqa: E712
                )
            ).all()
            reply = (
                f"🟢 <b>Status:</b> Active\n"
                f"<b>Subscriber ID:</b> {sub.id}\n"
                f"<b>Subscribed Indicators:</b> {len(enabled_count)} active\n"
                f"<b>Registered:</b> {sub.created_at.strftime('%Y-%m-%d %H:%M UTC')}"
            )

    elif cmd == "/stop":
        success = deactivate_subscriber(session, chat_id)
        if success:
            reply = (
                "🛑 <b>Alerts paused.</b> You will receive no further notifications. "
                "Send <code>/start</code> anytime to resume."
            )
        else:
            reply = (
                "ℹ️ Your subscription was already inactive. Send <code>/start</code> to activate."
            )

    else:
        reply = "❓ Unknown command. Send <code>/help</code> for available commands."

    if client:
        try:
            client.send_message(chat_id=chat_id, text=reply, parse_mode="HTML")
        except Exception as exc:
            logger.warning(f"Failed to send bot response to chat_id={chat_id}: {exc}")

    return chat_id, reply
