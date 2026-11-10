import logging
from datetime import datetime, timezone
from typing import Optional
from aiogram import Bot, Router
from aiogram.types import BusinessConnection as TgBusinessConnection
from sqlalchemy import select

from database.database import DatabaseManager
from database.models import ActivityLog, BusinessConnection, Notification
from integrations.business.permissions import BusinessPermissionChecker

logger = logging.getLogger("secretary_bot.business.connection")
business_connection_router = Router(name="business_connection_router")


class BusinessConnectionManager:
    """إدارة دورة حياة اتصال Telegram Business وتوثيق حالته."""

    def __init__(self, db_manager: DatabaseManager, owner_id: int) -> None:
        self.db_manager = db_manager
        self.owner_id = owner_id
        self.permission_checker = BusinessPermissionChecker(db_manager)

    async def handle_connection_update(
        self, event: TgBusinessConnection, bot: Optional[Bot] = None
    ) -> BusinessConnection:
        """معالجة حدث ربط أو تعديل أو إلغاء اتصال Telegram Business."""
        # التحقق الأمني: لا نقبل ربط اتصال Business إلا من حساب مالك النظام نفسه
        if event.user.id != self.owner_id:
            logger.warning(
                "رفض محاولة ربط Telegram Business من حساب غير المالك: user_id=%s",
                event.user.id,
            )
            raise PermissionError("محاولة ربط Telegram Business من مستخدم غير المالك.")

        rights_dict = self.permission_checker.extract_rights_dict(event)
        can_reply = bool(rights_dict.get("can_reply", False))
        is_enabled = bool(event.is_enabled)

        async with self.db_manager.session() as session:
            existing = await session.scalar(
                select(BusinessConnection).where(
                    BusinessConnection.connection_id == event.id
                )
            )
            now = datetime.now(timezone.utc)

            if existing:
                existing.user_id = event.user.id
                existing.user_chat_id = event.user_chat_id
                existing.can_reply = can_reply
                existing.rights_json = rights_dict
                existing.is_active = is_enabled
                existing.updated_at = now
                record = existing
            else:
                record = BusinessConnection(
                    connection_id=event.id,
                    user_id=event.user.id,
                    user_chat_id=event.user_chat_id,
                    can_reply=can_reply,
                    rights_json=rights_dict,
                    is_active=is_enabled,
                    connected_at=now,
                    updated_at=now,
                )
                session.add(record)

            action_name = "business_connected" if is_enabled else "business_disconnected"
            session.add(
                ActivityLog(
                    category="connection",
                    action=action_name,
                    actor_id=event.user.id,
                    target_id=event.id,
                    details_json={
                        "is_active": is_enabled,
                        "can_reply": can_reply,
                        "rights": rights_dict,
                    },
                )
            )

            notif_title = (
                "تم تفعيل اتصال Telegram Business"
                if is_enabled
                else "تم فصل اتصال Telegram Business"
            )
            notif_level = "info" if is_enabled else "warning"
            notif_msg = (
                f"معرف الاتصال: {event.id}\n"
                f"صلاحية الرد: {'مفعلة ✅' if can_reply else 'غير مفعلة ❌'}\n"
                f"صلاحية حذف الردود: {'مفعلة ✅' if rights_dict.get('can_delete_outgoing_messages') else 'غير مفعلة ❌'}"
            )
            session.add(
                Notification(
                    level=notif_level,
                    category="business_connection",
                    title=notif_title,
                    message=notif_msg,
                    is_sent_to_owner=bot is not None,
                )
            )

        if bot is not None:
            try:
                status_header = "🟢 <b>تم تحديث ربط Telegram Business</b>" if is_enabled else "🔴 <b>تم إلغاء ربط Telegram Business</b>"
                await bot.send_message(
                    chat_id=self.owner_id,
                    text=f"{status_header}\n━━━━━━━━━━━━━━━━━━━━\n{notif_msg}",
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.error("تعذر إرسال إشعار حالة Business للمالك: %s", exc)

        return record


@business_connection_router.business_connection()
async def on_business_connection_event(
    event: TgBusinessConnection,
    bot: Bot,
    business_connection_manager: BusinessConnectionManager,
) -> None:
    """مستقبل تحديثات الاتصال الرسمي من aiogram."""
    try:
        await business_connection_manager.handle_connection_update(event, bot=bot)
    except PermissionError:
        pass
