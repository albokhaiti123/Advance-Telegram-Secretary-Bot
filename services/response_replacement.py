import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from sqlalchemy import select

from database.database import DatabaseManager
from database.models import (
    ActivityLog,
    Conversation,
    ConversationState,
    ManagedResponse,
    ScheduledTask,
)
from integrations.business.sender import BusinessSender
from services.template_manager import RenderedTemplatePayload

logger = logging.getLogger("secretary_bot.services.replacement")


@dataclass
class ReplacementExecutionResult:
    success: bool
    new_message_id: Optional[int] = None
    replaced_old_message_id: Optional[int] = None
    old_delete_succeeded: bool = True
    error_message: Optional[str] = None


class ResponseReplacementManager:
    """
    المدير المسؤول عن إرسال الرد التلقائي الجديد ثم حذف الرد التلقائي السابق بأمان
    مع قفل مستقل لكل محادثة واستعادة الحالة بعد إعادة التشغيل.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        business_sender: BusinessSender,
        max_delete_retries: int = 3,
    ) -> None:
        self.db_manager = db_manager
        self.business_sender = business_sender
        self.max_delete_retries = max_delete_retries
        self._chat_locks: Dict[int, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    async def get_chat_lock(self, chat_id: int) -> asyncio.Lock:
        """الحصول على قفل مستقل خاص بالمحادثة المحددة."""
        async with self._registry_lock:
            if chat_id not in self._chat_locks:
                self._chat_locks[chat_id] = asyncio.Lock()
            return self._chat_locks[chat_id]

    async def send_and_replace_previous(
        self,
        chat_id: int,
        business_connection_id: str,
        payload: RenderedTemplatePayload,
        rule_id: Optional[int] = None,
        auto_delete_seconds: Optional[int] = None,
    ) -> ReplacementExecutionResult:
        """
        تنفيذ دورة الاستبدال الذرية داخل قفل المحادثة:
        1. إرسال الرد الجديد أولًا.
        2. إذا نجح، حذف آخر رد تلقائي سابق مسجل في managed_responses لهذه المحادثة.
        3. تحديث السجلات وحفظ الحالة.
        """
        chat_lock = await self.get_chat_lock(chat_id)

        async with chat_lock:
            now = datetime.now(timezone.utc)

            # 1. جلب آخر رد تلقائي سابق وتسجيل عملية معلقة قبل الإرسال
            previous_managed_id: Optional[int] = None
            previous_tg_msg_id: Optional[int] = None

            async with self.db_manager.session() as session:
                prev_record = await session.scalar(
                    select(ManagedResponse)
                    .where(
                        ManagedResponse.chat_id == chat_id,
                        ManagedResponse.is_latest_in_chat.is_(True),
                        ManagedResponse.status == "active",
                    )
                    .order_by(ManagedResponse.id.desc())
                )
                if prev_record:
                    previous_managed_id = prev_record.id
                    previous_tg_msg_id = prev_record.message_id

                conv_state = await session.scalar(
                    select(ConversationState).where(ConversationState.chat_id == chat_id)
                )
                if conv_state:
                    conv_state.pending_operation_json = {
                        "operation": "replace_auto_reply",
                        "rule_id": rule_id,
                        "previous_managed_id": previous_managed_id,
                        "previous_tg_msg_id": previous_tg_msg_id,
                        "started_at": now.isoformat(),
                    }

            # 2. إرسال الرد الجديد أولًا
            send_result = await self.business_sender.send_reply(
                business_connection_id=business_connection_id,
                chat_id=chat_id,
                content_type=payload.content_type,
                text=payload.text,
                bot_file_id=payload.bot_file_id,
                reply_markup=payload.reply_markup,
                parse_mode=payload.parse_mode,
                disable_web_page_preview=payload.disable_web_page_preview,
            )

            # 3. إذا فشل إرسال الرد الجديد: لا نحذف الرد القديم أبدًا وننظف العملية المعلقة
            if not send_result.success or send_result.message_id is None:
                async with self.db_manager.session() as session:
                    conv_state = await session.scalar(
                        select(ConversationState).where(ConversationState.chat_id == chat_id)
                    )
                    if conv_state:
                        conv_state.pending_operation_json = None

                logger.warning(
                    "فشل إرسال الرد الجديد في المحادثة %s؛ تم الإبقاء على الرد السابق (%s) دون تغيير.",
                    chat_id,
                    previous_tg_msg_id,
                )
                return ReplacementExecutionResult(
                    success=False,
                    error_message=send_result.error_message,
                )

            new_msg_id = send_result.message_id
            old_delete_ok = True

            # 4. بعد نجاح إرسال الرد الجديد: نحذف الرد التلقائي السابق إن وجد
            if previous_tg_msg_id is not None and previous_managed_id is not None:
                del_result = await self.business_sender.delete_managed_messages(
                    business_connection_id=business_connection_id,
                    message_ids=[previous_tg_msg_id],
                )
                old_delete_ok = del_result.success

            # 5. تحديث قاعدة البيانات: تسجيل الرد الجديد وتحديث حالة الرد القديم
            finish_now = datetime.now(timezone.utc)
            scheduled_del_at = (
                finish_now + timedelta(seconds=auto_delete_seconds)
                if auto_delete_seconds and auto_delete_seconds > 0
                else None
            )

            async with self.db_manager.session() as session:
                if previous_managed_id is not None:
                    old_rec = await session.get(ManagedResponse, previous_managed_id)
                    if old_rec:
                        old_rec.is_latest_in_chat = False
                        if old_delete_ok:
                            old_rec.status = "replaced"
                            old_rec.deleted_at = finish_now
                            session.add(
                                ActivityLog(
                                    category="deletion",
                                    action="previous_auto_reply_replaced",
                                    target_id=str(chat_id),
                                    details_json={
                                        "deleted_message_id": previous_tg_msg_id,
                                        "new_message_id": new_msg_id,
                                        "rule_id": rule_id,
                                    },
                                )
                            )
                        else:
                            old_rec.status = "delete_failed"
                            old_rec.delete_retry_count = (old_rec.delete_retry_count or 0) + 1
                            # جدولة مهمة إعادة محاولة حذف الرد القديم
                            retry_task_id = f"retry_del_{old_rec.id}_{uuid.uuid4().hex[:6]}"
                            session.add(
                                ScheduledTask(
                                    task_id=retry_task_id,
                                    task_type="retry_delete",
                                    reference_id=str(old_rec.id),
                                    payload_json={
                                        "managed_response_id": old_rec.id,
                                        "chat_id": chat_id,
                                        "message_id": previous_tg_msg_id,
                                        "business_connection_id": business_connection_id,
                                    },
                                    run_at=finish_now + timedelta(seconds=30),
                                    status="pending",
                                    max_retries=self.max_delete_retries,
                                    last_error="Failed initial deletion during replacement",
                                )
                            )

                # إضافة الرد التلقائي الجديد كآخر رد في المحادثة
                new_managed = ManagedResponse(
                    chat_id=chat_id,
                    message_id=new_msg_id,
                    rule_id=rule_id,
                    business_connection_id=business_connection_id,
                    is_latest_in_chat=True,
                    status="active",
                    scheduled_delete_at=scheduled_del_at,
                    sent_at=finish_now,
                )
                session.add(new_managed)
                await session.flush()

                # إذا كان للقاعدة مدة حذف تلقائي، نجدول مهمة حذف مستقبلية
                if scheduled_del_at is not None:
                    session.add(
                        ScheduledTask(
                            task_id=f"autodel_{new_managed.id}_{uuid.uuid4().hex[:6]}",
                            task_type="auto_delete",
                            reference_id=str(new_managed.id),
                            payload_json={
                                "managed_response_id": new_managed.id,
                                "chat_id": chat_id,
                                "message_id": new_msg_id,
                                "business_connection_id": business_connection_id,
                            },
                            run_at=scheduled_del_at,
                            status="pending",
                            max_retries=self.max_delete_retries,
                        )
                    )

                # تحديث عداد الردود وإزالة العملية المعلقة
                conv = await session.get(Conversation, chat_id)
                if conv:
                    conv.reply_count = (conv.reply_count or 0) + 1
                    conv.last_activity_at = finish_now

                conv_state = await session.get(ConversationState, chat_id)
                if conv_state:
                    conv_state.pending_operation_json = None
                    conv_state.last_rule_id = rule_id
                    conv_state.last_reply_at = finish_now

            return ReplacementExecutionResult(
                success=True,
                new_message_id=new_msg_id,
                replaced_old_message_id=previous_tg_msg_id,
                old_delete_succeeded=old_delete_ok,
            )

    async def retry_failed_deletion(self, managed_response_id: int) -> bool:
        """إعادة محاولة حذف رد سابق فشل حذفه سابقًا."""
        async with self.db_manager.session() as session:
            rec = await session.get(ManagedResponse, managed_response_id)
            if not rec or rec.status == "deleted" or not rec.business_connection_id:
                return True

            if rec.delete_retry_count >= self.max_delete_retries:
                return False

            chat_id = rec.chat_id
            msg_id = rec.message_id
            conn_id = rec.business_connection_id

        chat_lock = await self.get_chat_lock(chat_id)
        async with chat_lock:
            res = await self.business_sender.delete_managed_messages(
                business_connection_id=conn_id,
                message_ids=[msg_id],
            )
            now = datetime.now(timezone.utc)
            async with self.db_manager.session() as session:
                rec = await session.get(ManagedResponse, managed_response_id)
                if not rec:
                    return True
                if res.success:
                    rec.status = "replaced"
                    rec.deleted_at = now
                    return True
                rec.delete_retry_count = (rec.delete_retry_count or 0) + 1
                return False
