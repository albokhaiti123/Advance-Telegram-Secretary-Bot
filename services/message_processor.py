import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from aiogram import Bot
from aiogram.types import Message
from sqlalchemy import select

from database.database import DatabaseManager
from database.models import (
    Conversation,
    ConversationState,
    ManagedResponse,
    ReplyTemplate,
    SystemSetting,
)
from services.auto_reply import AutoReplyService
from services.media_manager import MediaManager
from services.response_replacement import ResponseReplacementManager
from services.rule_engine import IncomingMessageEnvelope
from services.template_manager import TemplateManager

logger = logging.getLogger("secretary_bot.services.message_processor")


class MessageProcessor:
    """
    المنسق الشامل لمعالجة الرسائل الواردة وميزات السكرتير المتقدمة:
    1. التحقق من التفعيل العام وتفعيل المحادثة.
    2. فحص ساعات العمل الرسمية (Work Hours).
    3. تطبيق وضع الغياب (Away Mode) مع منع الإزعاج (Cooldown).
    4. تطبيق رسالة الترحيب التلقائية (Greeting Message).
    5. تمرير الرسالة لمحرك القواعد والردود التلقائية (AutoReplyService).
    6. إدارة الردود السريعة (Quick Replies) وإدارة الرسائل (تعديل/حذف/تثبيت).
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        auto_reply_service: AutoReplyService,
        template_manager: Optional[TemplateManager] = None,
        replacement_manager: Optional[ResponseReplacementManager] = None,
    ) -> None:
        self.db_manager = db_manager
        self.auto_reply_service = auto_reply_service
        self.template_manager = (
            template_manager or auto_reply_service.template_manager
        )
        self.replacement_manager = (
            replacement_manager or auto_reply_service.replacement_manager
        )
        # قفل لكل محادثة لمنع تضارب المعالجة المتزامنة (Race Condition)
        self._processing_locks: Dict[int, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def _get_processing_lock(self, chat_id: int) -> asyncio.Lock:
        """إرجاع قفل asyncio مخصص لكل محادثة، مع إنشائه عند الحاجة بأمان."""
        async with self._locks_guard:
            if chat_id not in self._processing_locks:
                self._processing_locks[chat_id] = asyncio.Lock()
            return self._processing_locks[chat_id]

    async def handle_customer_message(self, message: Message) -> List[int]:
        """معالجة رسالة واردة من عميل عبر Telegram Business — محمية بقفل مستقل لكل محادثة."""
        if not message.business_connection_id:
            return []
        proc_lock = await self._get_processing_lock(message.chat.id)
        async with proc_lock:
            return await self._handle_customer_message_locked(message)

    async def _handle_customer_message_locked(self, message: Message) -> List[int]:
        """المنطق الفعلي لمعالجة الرسالة بعد الحصول على القفل الخاص بالمحادثة."""
        now = datetime.now(timezone.utc)
        async with self.db_manager.session() as session:
            master_setting = await session.scalar(
                select(SystemSetting).where(
                    SystemSetting.key == "auto_reply_master_enabled"
                )
            )
            if master_setting and not master_setting.value_json.get("enabled", True):
                return []

            conv = await session.scalar(
                select(Conversation).where(Conversation.chat_id == message.chat.id)
            )
            if conv and not conv.auto_reply_enabled:
                return []

            work_hours_setting = await session.scalar(
                select(SystemSetting).where(SystemSetting.key == "work_hours")
            )
            away_setting = await session.scalar(
                select(SystemSetting).where(SystemSetting.key == "away_mode")
            )
            greeting_setting = await session.scalar(
                select(SystemSetting).where(SystemSetting.key == "greeting_message")
            )

            work_hours_cfg = (
                work_hours_setting.value_json if work_hours_setting else {}
            )
            away_cfg = away_setting.value_json if away_setting else {}
            greeting_cfg = greeting_setting.value_json if greeting_setting else {}

        within_work_hours = self.check_is_within_work_hours(work_hours_cfg)
        media_info = MediaManager.extract_media_info_from_message(message)

        envelope = IncomingMessageEnvelope(
            chat_id=message.chat.id,
            sender_id=message.from_user.id if message.from_user else 0,
            chat_type=message.chat.type,
            text=message.text or message.caption,
            media_type=media_info["media_type"] if media_info else None,
            has_media=media_info is not None,
            first_name=message.from_user.first_name if message.from_user else None,
            last_name=message.from_user.last_name if message.from_user else None,
            username=message.from_user.username if message.from_user else None,
            chat_title=message.chat.title or message.chat.full_name,
            business_connection_id=message.business_connection_id,
            timestamp=now,
        )

        sent_ids: List[int] = []

        # 1. فحص وتنفيذ وضع الغياب (Away Mode)
        away_msg_id = await self._maybe_send_away_reply(
            envelope=envelope,
            away_cfg=away_cfg,
            within_work_hours=within_work_hours,
            now=now,
        )
        if away_msg_id is not None:
            sent_ids.append(away_msg_id)
            if away_cfg.get("stop_other_rules", True):
                return sent_ids

        # 2. فحص وتنفيذ رسالة الترحيب (Greeting Message)
        greeting_msg_id = await self._maybe_send_greeting_reply(
            envelope=envelope,
            greeting_cfg=greeting_cfg,
            now=now,
        )
        if greeting_msg_id is not None:
            sent_ids.append(greeting_msg_id)

        # 3. تمرير الرسالة لمحرك القواعد القياسي
        rule_msg_ids = await self.auto_reply_service.process_and_reply(
            envelope=envelope,
            is_within_work_hours=within_work_hours,
        )
        sent_ids.extend(rule_msg_ids)
        return sent_ids

    async def _maybe_send_away_reply(
        self,
        envelope: IncomingMessageEnvelope,
        away_cfg: Dict[str, Any],
        within_work_hours: bool,
        now: datetime,
    ) -> Optional[int]:
        """إرسال رسالة الغياب إذا كان الوضع مفعلًا وضمن شروط الوقت وCooldown."""
        if not away_cfg or not away_cfg.get("enabled", False):
            return None

        only_outside = bool(away_cfg.get("only_outside_work_hours", True))
        if only_outside and within_work_hours:
            return None

        template_id = away_cfg.get("template_id")
        if not template_id or not envelope.business_connection_id:
            return None

        cooldown_sec = int(away_cfg.get("cooldown_seconds", 3600))

        async with self.db_manager.session() as session:
            conv_state = await session.get(ConversationState, envelope.chat_id)
            if conv_state and conv_state.away_last_sent_at:
                last_away = conv_state.away_last_sent_at
                if last_away.tzinfo is None:
                    last_away = last_away.replace(tzinfo=timezone.utc)
                if (now - last_away).total_seconds() < cooldown_sec:
                    return None

        payload = await self.template_manager.build_rendered_payload(
            template_id=int(template_id),
            context={
                "first_name": envelope.first_name,
                "username": envelope.username,
                "user_id": envelope.sender_id,
                "chat_title": envelope.chat_title,
            },
        )
        if not payload:
            return None

        res = await self.replacement_manager.send_and_replace_previous(
            chat_id=envelope.chat_id,
            business_connection_id=envelope.business_connection_id,
            payload=payload,
            rule_id=None,
        )
        if res.success and res.new_message_id is not None:
            async with self.db_manager.session() as session:
                conv_state = await session.get(ConversationState, envelope.chat_id)
                if conv_state is None:
                    session.add(
                        ConversationState(
                            chat_id=envelope.chat_id,
                            away_last_sent_at=now,
                        )
                    )
                else:
                    conv_state.away_last_sent_at = now
            return res.new_message_id
        return None

    async def _maybe_send_greeting_reply(
        self,
        envelope: IncomingMessageEnvelope,
        greeting_cfg: Dict[str, Any],
        now: datetime,
    ) -> Optional[int]:
        """إرسال رسالة الترحيب عند التواصل لأول مرة أو بعد انقضاء مدة التكرار."""
        if not greeting_cfg or not greeting_cfg.get("enabled", False):
            return None

        template_id = greeting_cfg.get("template_id")
        if not template_id or not envelope.business_connection_id:
            return None

        cooldown_days = int(greeting_cfg.get("cooldown_days", 30))

        async with self.db_manager.session() as session:
            conv_state = await session.get(ConversationState, envelope.chat_id)
            if conv_state and conv_state.first_message_handled:
                if conv_state.last_reply_at:
                    last_rep = conv_state.last_reply_at
                    if last_rep.tzinfo is None:
                        last_rep = last_rep.replace(tzinfo=timezone.utc)
                    if (now - last_rep) < timedelta(days=cooldown_days):
                        return None
                else:
                    return None

        payload = await self.template_manager.build_rendered_payload(
            template_id=int(template_id),
            context={
                "first_name": envelope.first_name,
                "username": envelope.username,
                "user_id": envelope.sender_id,
            },
        )
        if not payload:
            return None

        res = await self.replacement_manager.send_and_replace_previous(
            chat_id=envelope.chat_id,
            business_connection_id=envelope.business_connection_id,
            payload=payload,
        )
        if res.success and res.new_message_id is not None:
            async with self.db_manager.session() as session:
                conv_state = await session.get(ConversationState, envelope.chat_id)
                if conv_state is None:
                    session.add(
                        ConversationState(
                            chat_id=envelope.chat_id,
                            first_message_handled=True,
                            last_reply_at=now,
                        )
                    )
                else:
                    conv_state.first_message_handled = True
                    conv_state.last_reply_at = now
            return res.new_message_id
        return None

    # --- إدارة الردود السريعة (Quick Replies) ---
    async def list_quick_replies(
        self, category: Optional[str] = None, search_query: Optional[str] = None
    ) -> List[ReplyTemplate]:
        """جلب الردود السريعة المحفوظة مع إمكانية التصفية حسب التصنيف أو البحث."""
        async with self.db_manager.session() as session:
            stmt = select(ReplyTemplate).where(
                ReplyTemplate.is_quick_reply.is_(True)
            )
            if category:
                stmt = stmt.where(ReplyTemplate.category == category)
            if search_query and search_query.strip():
                q = f"%{search_query.strip()}%"
                stmt = stmt.where(ReplyTemplate.name.ilike(q))
            stmt = stmt.order_by(ReplyTemplate.name.asc())
            return list((await session.scalars(stmt)).all())

    # --- إدارة الرسائل التي أرسلها النظام (تعديل / تثبيت) ---
    async def edit_managed_response_text(
        self,
        bot: Bot,
        managed_response_id: int,
        new_text: str,
    ) -> bool:
        """تعديل نص رسالة أرسلها النظام مسبقًا إذا كانت الصلاحيات تسمح بذلك."""
        async with self.db_manager.session() as session:
            rec = await session.get(ManagedResponse, managed_response_id)
            if not rec or rec.status != "active" or not rec.business_connection_id:
                return False
            chat_id = rec.chat_id
            msg_id = rec.message_id
            conn_id = rec.business_connection_id

        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                business_connection_id=conn_id,
                text=new_text,
                parse_mode="HTML",
            )
            return True
        except Exception as exc:
            logger.warning("تعذر تعديل الرسالة #%s: %s", managed_response_id, exc)
            return False

    @staticmethod
    def check_is_within_work_hours(
        work_hours_cfg: dict,
        reference_dt: Optional[datetime] = None,
    ) -> bool:
        """التحقق مما إذا كان الوقت الحالي يقع ضمن جدول ساعات العمل."""
        if not work_hours_cfg or not work_hours_cfg.get("enabled", False):
            return True

        tz_name = work_hours_cfg.get("timezone", "Asia/Aden")
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")

        now_local = (
            reference_dt.astimezone(tz)
            if reference_dt is not None
            else datetime.now(tz)
        )
        allowed_days = work_hours_cfg.get("days", [0, 1, 2, 3, 4, 6])
        if now_local.weekday() not in allowed_days:
            return False

        start_str = work_hours_cfg.get("start_time", "09:00")
        end_str = work_hours_cfg.get("end_time", "17:00")
        current_hm = now_local.strftime("%H:%M")
        return start_str <= current_hm <= end_str
