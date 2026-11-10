import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from database.database import DatabaseManager
from database.models import (
    ActivityLog,
    Conversation,
    ConversationState,
    IgnoredConversation,
    IgnoredUser,
    ManagedResponse,
)


@dataclass
class ConversationDetailSummary:
    conversation: Conversation
    state: Optional[ConversationState]
    latest_managed_response: Optional[ManagedResponse]
    is_ignored: bool


class ConversationManager:
    """
    إدارة المحادثات النشطة، التحكم بتفعيل الرد التلقائي لكل محادثة،
    إعادة ضبط حالة المحادثة، وإدارة قوائم الاستثناءات (السماح والمنع) للمستخدمين والمحادثات.
    """

    def __init__(self, db_manager: DatabaseManager) -> None:
        self.db_manager = db_manager

    async def list_conversations(
        self, page: int = 1, page_size: int = 8
    ) -> Tuple[List[Conversation], int, int]:
        """جلب قائمة المحادثات مرتبة حسب آخر نشاط مع الترقيم."""
        async with self.db_manager.session() as session:
            total = (
                await session.scalar(select(func.count()).select_from(Conversation))
                or 0
            )
            total_pages = max(1, math.ceil(total / page_size))
            stmt = (
                select(Conversation)
                .order_by(Conversation.last_activity_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            items = list((await session.scalars(stmt)).all())
            return items, total, total_pages

    async def get_conversation_details(
        self, chat_id: int
    ) -> Optional[ConversationDetailSummary]:
        """جلب تفاصيل المحادثة وحالتها وآخر رد تلقائي أرسله النظام فيها."""
        async with self.db_manager.session() as session:
            stmt = (
                select(Conversation)
                .options(selectinload(Conversation.state))
                .where(Conversation.chat_id == chat_id)
            )
            conv = await session.scalar(stmt)
            if not conv:
                return None

            latest_resp = await session.scalar(
                select(ManagedResponse)
                .where(
                    ManagedResponse.chat_id == chat_id,
                    ManagedResponse.is_latest_in_chat.is_(True),
                )
                .order_by(ManagedResponse.id.desc())
            )
            ignored = await session.scalar(
                select(IgnoredConversation).where(
                    IgnoredConversation.chat_id == chat_id,
                    IgnoredConversation.list_type == "blacklist",
                )
            )
            return ConversationDetailSummary(
                conversation=conv,
                state=conv.state,
                latest_managed_response=latest_resp,
                is_ignored=ignored is not None,
            )

    async def toggle_conversation_auto_reply(self, chat_id: int) -> Optional[bool]:
        """تفعيل أو تعطيل الرد التلقائي لمحادثة محددة."""
        async with self.db_manager.session() as session:
            conv = await session.get(Conversation, chat_id)
            if not conv:
                return None
            conv.auto_reply_enabled = not conv.auto_reply_enabled
            new_state = conv.auto_reply_enabled
            session.add(
                ActivityLog(
                    category="admin",
                    action="toggle_conversation_auto_reply",
                    target_id=str(chat_id),
                    details_json={"auto_reply_enabled": new_state},
                )
            )
            return new_state

    async def reset_conversation_state(self, chat_id: int) -> bool:
        """إعادة ضبط حالة المحادثة (تصفير Cooldown، عدادات القواعد، وحالة أول رسالة)."""
        now = datetime.now(timezone.utc)
        async with self.db_manager.session() as session:
            state = await session.get(ConversationState, chat_id)
            if not state:
                return False
            state.first_message_handled = False
            state.last_rule_id = None
            state.last_reply_at = None
            state.rule_cooldowns_json = {}
            state.rule_counts_json = {}
            state.away_last_sent_at = None
            state.pending_operation_json = None
            state.updated_at = now

            session.add(
                ActivityLog(
                    category="admin",
                    action="reset_conversation_state",
                    target_id=str(chat_id),
                )
            )
            return True

    async def clear_all_pending_operations(self) -> int:
        """فك تعليق أي عمليات معلقة في جميع المحادثات."""
        cleared = 0
        async with self.db_manager.session() as session:
            states = list(
                (
                    await session.scalars(
                        select(ConversationState).where(
                            ConversationState.pending_operation_json.is_not(None)
                        )
                    )
                ).all()
            )
            for st in states:
                st.pending_operation_json = None
                cleared += 1
        return cleared

    # --- إدارة قوائم الاستثناءات للمستخدمين والمحادثات ---
    async def set_user_exception(
        self,
        user_id: int,
        list_type: str = "blacklist",
        username: Optional[str] = None,
        excluded_rule_ids: Optional[List[int]] = None,
        reason: Optional[str] = None,
    ) -> IgnoredUser:
        """إضافة أو تحديث مستخدم في قائمة المنع (blacklist) أو السماح (whitelist)."""
        async with self.db_manager.session() as session:
            rec = await session.get(IgnoredUser, user_id)
            if rec:
                rec.list_type = list_type
                if username:
                    rec.username = username
                if excluded_rule_ids is not None:
                    rec.excluded_rule_ids_json = excluded_rule_ids
                if reason:
                    rec.reason = reason
            else:
                rec = IgnoredUser(
                    user_id=user_id,
                    username=username,
                    list_type=list_type,
                    excluded_rule_ids_json=excluded_rule_ids or [],
                    reason=reason,
                )
                session.add(rec)
            await session.flush()
            return rec

    async def remove_user_exception(self, user_id: int) -> bool:
        """إزالة مستخدم من قوائم الاستثناءات."""
        async with self.db_manager.session() as session:
            res = await session.execute(
                delete(IgnoredUser).where(IgnoredUser.user_id == user_id)
            )
            return (res.rowcount or 0) > 0

    async def set_chat_exception(
        self,
        chat_id: int,
        list_type: str = "blacklist",
        title: Optional[str] = None,
        excluded_media_types: Optional[List[str]] = None,
        reason: Optional[str] = None,
    ) -> IgnoredConversation:
        """إضافة أو تحديث محادثة في قائمة التجاهل أو السماح."""
        async with self.db_manager.session() as session:
            rec = await session.get(IgnoredConversation, chat_id)
            if rec:
                rec.list_type = list_type
                if title:
                    rec.title = title
                if excluded_media_types is not None:
                    rec.excluded_media_types_json = excluded_media_types
                if reason:
                    rec.reason = reason
            else:
                rec = IgnoredConversation(
                    chat_id=chat_id,
                    title=title,
                    list_type=list_type,
                    excluded_media_types_json=excluded_media_types or [],
                    reason=reason,
                )
                session.add(rec)
            await session.flush()
            return rec

    async def remove_chat_exception(self, chat_id: int) -> bool:
        """إزالة محادثة من قائمة التجاهل."""
        async with self.db_manager.session() as session:
            res = await session.execute(
                delete(IgnoredConversation).where(
                    IgnoredConversation.chat_id == chat_id
                )
            )
            return (res.rowcount or 0) > 0

    async def list_exceptions_summary(self) -> Dict[str, List[object]]:
        """جلب قوائم المستخدمين والمحادثات المستثناة للعرض في لوحة التحكم."""
        async with self.db_manager.session() as session:
            users = list((await session.scalars(select(IgnoredUser))).all())
            chats = list((await session.scalars(select(IgnoredConversation))).all())
            return {"users": users, "chats": chats}
