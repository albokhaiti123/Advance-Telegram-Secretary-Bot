import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional
from aiogram import Router
from aiogram.types import BusinessMessagesDeleted, Message
from sqlalchemy import select, update

from database.database import DatabaseManager
from database.models import Conversation, ConversationState, ManagedResponse

logger = logging.getLogger("secretary_bot.business.updates")
business_updates_router = Router(name="business_updates_router")

MessageProcessorCallback = Callable[[Message], Awaitable[None]]


class BusinessUpdateProcessor:
    """
    معالجة الرسائل الواردة عبر اتصال Telegram Business:
    - يسجل المحادثة ويحدث نشاطها في جدول conversations.
    - يتجاهل الرسائل الصادرة من المالك نفسه (الرسائل اليدوية) لمنع الرد على الذات.
    - يمرر رسائل العملاء المستوفية للشروط إلى محرك السكرتير (Secretary Engine).
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        owner_id: int,
        on_customer_message: Optional[MessageProcessorCallback] = None,
    ) -> None:
        self.db_manager = db_manager
        self.owner_id = owner_id
        self.on_customer_message = on_customer_message

    async def handle_incoming_business_message(self, message: Message) -> bool:
        """
        يرجع True إذا تمت معالجة الرسالة كرسالة عميل واردة،
        وFalse إذا تم تجاهلها (مثل رسائل المالك اليدوية).
        """
        if not message.business_connection_id:
            return False

        sender_id = message.from_user.id if message.from_user else None

        # تجاهل الرسائل اليدوية الصادرة من مالك الحساب الشخصي نفسه
        if sender_id is None or sender_id == self.owner_id:
            logger.debug(
                "تجاهل رسالة صادرة من المالك نفسه داخل محادثة Business: chat_id=%s",
                message.chat.id,
            )
            return False

        now = datetime.now(timezone.utc)
        title_or_name = (
            message.chat.full_name
            or message.chat.title
            or (message.from_user.full_name if message.from_user else "مستخدم")
        )
        username = message.chat.username or (
            message.from_user.username if message.from_user else None
        )

        async with self.db_manager.session() as session:
            conv = await session.scalar(
                select(Conversation).where(Conversation.chat_id == message.chat.id)
            )
            if conv is None:
                conv = Conversation(
                    chat_id=message.chat.id,
                    chat_type=message.chat.type,
                    title_or_name=title_or_name,
                    username=username,
                    business_connection_id=message.business_connection_id,
                    auto_reply_enabled=True,
                    message_count=1,
                    reply_count=0,
                    last_activity_at=now,
                )
                session.add(conv)
                session.add(
                    ConversationState(
                        chat_id=message.chat.id,
                        first_message_handled=False,
                    )
                )
            else:
                conv.title_or_name = title_or_name
                conv.username = username
                conv.business_connection_id = message.business_connection_id
                conv.message_count = (conv.message_count or 0) + 1
                conv.last_activity_at = now

        if self.on_customer_message is not None:
            await self.on_customer_message(message)

        return True

    async def handle_deleted_business_messages(
        self, event: BusinessMessagesDeleted
    ) -> None:
        """تحديث حالة الردود التلقائية إذا حُذفت من المحادثة."""
        if not event.message_ids:
            return
        now = datetime.now(timezone.utc)
        async with self.db_manager.session() as session:
            await session.execute(
                update(ManagedResponse)
                .where(
                    ManagedResponse.chat_id == event.chat.id,
                    ManagedResponse.message_id.in_(event.message_ids),
                )
                .values(
                    status="deleted",
                    is_latest_in_chat=False,
                    deleted_at=now,
                )
            )


@business_updates_router.business_message()
async def on_business_message(
    message: Message,
    business_update_processor: BusinessUpdateProcessor,
) -> None:
    await business_update_processor.handle_incoming_business_message(message)


@business_updates_router.deleted_business_messages()
async def on_deleted_business_messages(
    event: BusinessMessagesDeleted,
    business_update_processor: BusinessUpdateProcessor,
) -> None:
    await business_update_processor.handle_deleted_business_messages(event)
