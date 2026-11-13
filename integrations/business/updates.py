import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional
from aiogram import Router
from aiogram.types import BusinessMessagesDeleted, Message
from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

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
            # UPSERT ذري لمنع تعارض الإدراج المتزامن (Race Condition)
            # إدراج أو تحديث سجل المحادثة بشكل ذري (ON CONFLICT DO UPDATE)
            conv_stmt = (
                sqlite_insert(Conversation)
                .values(
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
                .on_conflict_do_update(
                    index_elements=[Conversation.chat_id],
                    set_={
                        "title_or_name": title_or_name,
                        "username": username,
                        "business_connection_id": message.business_connection_id,
                        "message_count": Conversation.message_count + 1,
                        "last_activity_at": now,
                    },
                )
            )
            await session.execute(conv_stmt)

            # إدراج حالة المحادثة عند الإنشاء الأول فقط (ON CONFLICT DO NOTHING)
            state_stmt = (
                sqlite_insert(ConversationState)
                .values(
                    chat_id=message.chat.id,
                    first_message_handled=False,
                )
                .on_conflict_do_nothing(index_elements=[ConversationState.chat_id])
            )
            await session.execute(state_stmt)

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
