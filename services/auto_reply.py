import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List
from sqlalchemy import select

from database.database import DatabaseManager
from database.models import ActivityLog, AutoReplyRule, ConversationState
from services.response_replacement import ResponseReplacementManager
from services.rule_engine import IncomingMessageEnvelope, RuleEngine
from services.template_manager import TemplateManager

logger = logging.getLogger("secretary_bot.services.auto_reply")


class AutoReplyService:
    """
    تنسيق عملية الرد التلقائي:
    - يستدعي RuleEngine لاستخراج القواعد المطابقة.
    - يطبق التأخير الزمني (delay_seconds) إن وجد.
    - يجهز القالب بالمتغيرات الديناميكية.
    - ينفذ الإرسال والاستبدال عبر ResponseReplacementManager.
    - يحدث إحصائيات القاعدة وحالة المحادثة (Cooldown & Counts).
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        rule_engine: RuleEngine,
        template_manager: TemplateManager,
        replacement_manager: ResponseReplacementManager,
    ) -> None:
        self.db_manager = db_manager
        self.rule_engine = rule_engine
        self.template_manager = template_manager
        self.replacement_manager = replacement_manager

    async def process_and_reply(
        self,
        envelope: IncomingMessageEnvelope,
        is_within_work_hours: bool = True,
    ) -> List[int]:
        """معالجة رسالة العميل وإرجاع قائمة معرفات الرسائل التلقائية الجديدة التي أُرسلت."""
        if not envelope.business_connection_id:
            return []

        matched_rules = await self.rule_engine.evaluate_message(
            envelope=envelope,
            is_within_work_hours=is_within_work_hours,
        )
        if not matched_rules:
            return []

        sent_message_ids: List[int] = []
        variable_context: Dict[str, object] = {
            "first_name": envelope.first_name,
            "last_name": envelope.last_name,
            "username": envelope.username,
            "user_id": envelope.sender_id,
            "chat_title": envelope.chat_title,
        }

        for rule in matched_rules:
            if rule.delay_seconds and rule.delay_seconds > 0:
                await asyncio.sleep(min(rule.delay_seconds, 60.0))

            payload = await self.template_manager.build_rendered_payload(
                template_id=rule.template_id,
                context=variable_context,
            )
            if not payload:
                logger.warning("القالب المرتبط بالقاعدة %s غير موجود.", rule.id)
                continue

            exec_result = await self.replacement_manager.send_and_replace_previous(
                chat_id=envelope.chat_id,
                business_connection_id=envelope.business_connection_id,
                payload=payload,
                rule_id=rule.id,
                auto_delete_seconds=rule.auto_delete_seconds,
            )

            if exec_result.success and exec_result.new_message_id is not None:
                sent_message_ids.append(exec_result.new_message_id)
                await self._record_rule_execution(
                    chat_id=envelope.chat_id,
                    rule_id=rule.id,
                    new_message_id=exec_result.new_message_id,
                )

        return sent_message_ids

    async def _record_rule_execution(
        self,
        chat_id: int,
        rule_id: int,
        new_message_id: int,
    ) -> None:
        """تحديث عداد تنفيذ القاعدة وتحديث Cooldown وحالة أول رسالة في المحادثة."""
        now = datetime.now(timezone.utc)
        rule_key = str(rule_id)

        async with self.db_manager.session() as session:
            rule = await session.get(AutoReplyRule, rule_id)
            if rule:
                rule.execution_count = (rule.execution_count or 0) + 1
                rule.last_executed_at = now

            conv_state = await session.scalar(
                select(ConversationState).where(ConversationState.chat_id == chat_id)
            )
            if conv_state is None:
                conv_state = ConversationState(
                    chat_id=chat_id,
                    first_message_handled=True,
                    last_rule_id=rule_id,
                    last_reply_at=now,
                    rule_cooldowns_json={rule_key: now.isoformat()},
                    rule_counts_json={rule_key: 1},
                )
                session.add(conv_state)
            else:
                conv_state.first_message_handled = True
                conv_state.last_rule_id = rule_id
                conv_state.last_reply_at = now

                cooldowns = dict(conv_state.rule_cooldowns_json or {})
                cooldowns[rule_key] = now.isoformat()
                conv_state.rule_cooldowns_json = cooldowns

                counts = dict(conv_state.rule_counts_json or {})
                counts[rule_key] = int(counts.get(rule_key, 0)) + 1
                conv_state.rule_counts_json = counts

            session.add(
                ActivityLog(
                    category="auto_reply",
                    action="rule_executed",
                    target_id=str(chat_id),
                    details_json={
                        "rule_id": rule_id,
                        "sent_message_id": new_message_id,
                    },
                )
            )
