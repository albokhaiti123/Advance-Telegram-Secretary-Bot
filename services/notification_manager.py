import logging
from typing import List, Optional
from aiogram import Bot
from sqlalchemy import select

from database.database import DatabaseManager
from database.models import Notification, SystemSetting

logger = logging.getLogger("secretary_bot.services.notifications")

LEVEL_ICONS = {
    "info": "ℹ️",
    "warning": "⚠️",
    "error": "❌",
    "critical": "🚨",
}


class NotificationManager:
    """إدارة الإشعارات والتنبيهات الإدارية الموجهة لمالك النظام."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        owner_id: int,
        bot: Optional[Bot] = None,
    ) -> None:
        self.db_manager = db_manager
        self.owner_id = owner_id
        self.bot = bot

    async def notify_owner(
        self,
        category: str,
        title: str,
        message: str,
        level: str = "info",
    ) -> Notification:
        """حفظ التنبيه في قاعدة البيانات وإرساله إلى المالك إذا كانت الإشعارات مفعلة."""
        should_send = await self._is_notification_enabled(level)
        sent_ok = False

        if should_send and self.bot is not None:
            icon = LEVEL_ICONS.get(level, "🔔")
            formatted = (
                f"{icon} <b>{title}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{message}"
            )
            try:
                await self.bot.send_message(
                    chat_id=self.owner_id,
                    text=formatted,
                    parse_mode="HTML",
                )
                sent_ok = True
            except Exception as exc:
                logger.error("تعذر إرسال التنبيه الفوري للمالك: %s", exc)

        async with self.db_manager.session() as session:
            notif = Notification(
                level=level,
                category=category,
                title=title,
                message=message,
                is_sent_to_owner=sent_ok,
            )
            session.add(notif)
            await session.flush()
            return notif

    async def flush_unsent_notifications(self) -> int:
        """إرسال أي تنبيهات متراكمة لم تُرسل للمالك سابقًا."""
        if self.bot is None:
            return 0

        sent_count = 0
        async with self.db_manager.session() as session:
            unsent = list(
                (
                    await session.scalars(
                        select(Notification)
                        .where(Notification.is_sent_to_owner.is_(False))
                        .order_by(Notification.id.asc())
                        .limit(20)
                    )
                ).all()
            )
            for n in unsent:
                icon = LEVEL_ICONS.get(n.level, "🔔")
                try:
                    await self.bot.send_message(
                        chat_id=self.owner_id,
                        text=f"{icon} <b>{n.title}</b>\n{n.message}",
                        parse_mode="HTML",
                    )
                    n.is_sent_to_owner = True
                    sent_count += 1
                except Exception:
                    break
        return sent_count

    async def list_recent_notifications(self, limit: int = 15) -> List[Notification]:
        async with self.db_manager.session() as session:
            stmt = (
                select(Notification)
                .order_by(Notification.id.desc())
                .limit(limit)
            )
            return list((await session.scalars(stmt)).all())

    async def _is_notification_enabled(self, level: str) -> bool:
        async with self.db_manager.session() as session:
            setting = await session.scalar(
                select(SystemSetting).where(
                    SystemSetting.key == "notifications_config"
                )
            )
            if not setting:
                return True
            cfg = setting.value_json or {}
            if not cfg.get("enabled", True):
                return level == "critical"
            return True
