import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from sqlalchemy import select

from database.database import DatabaseManager
from database.models import (
    AutoReplyRule,
    ConversationState,
    IgnoredConversation,
    IgnoredUser,
)

logger = logging.getLogger("secretary_bot.services.rule_engine")


@dataclass
class IncomingMessageEnvelope:
    chat_id: int
    sender_id: int
    chat_type: str = "private"
    text: Optional[str] = None
    media_type: Optional[str] = None
    has_media: bool = False
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    chat_title: Optional[str] = None
    business_connection_id: Optional[str] = None
    timestamp: Optional[datetime] = None


class RuleEngine:
    """
    محرك القواعد المستقل:
    يفحص الرسائل الواردة مقابل القواعد المفعلة مرتبة حسب الأولوية (priority DESC).
    """

    def __init__(self, db_manager: DatabaseManager, timezone_str: str = "Asia/Aden") -> None:
        self.db_manager = db_manager
        self.timezone_str = timezone_str

    async def evaluate_message(
        self,
        envelope: IncomingMessageEnvelope,
        is_within_work_hours: bool = True,
    ) -> List[AutoReplyRule]:
        """
        تحديد قائمة القواعد المطابقة للرسالة الواردة مع احترام الأولويات، الاستثناءات، وCooldown.
        """
        now = envelope.timestamp or datetime.now(timezone.utc)

        async with self.db_manager.session() as session:
            # 1. فحص قوائم تجاهل المحادثات
            ignored_chat = await session.scalar(
                select(IgnoredConversation).where(
                    IgnoredConversation.chat_id == envelope.chat_id
                )
            )
            if ignored_chat and ignored_chat.list_type == "blacklist":
                if not ignored_chat.excluded_media_types_json:
                    return []
                msg_mtype = envelope.media_type or "text"
                if msg_mtype in ignored_chat.excluded_media_types_json:
                    return []

            # 2. فحص قوائم تجاهل المستخدمين
            ignored_user = await session.scalar(
                select(IgnoredUser).where(IgnoredUser.user_id == envelope.sender_id)
            )
            user_excluded_rule_ids: set[int] = set()
            if ignored_user and ignored_user.list_type == "blacklist":
                if not ignored_user.excluded_rule_ids_json:
                    # محظور من جميع القواعد
                    return []
                user_excluded_rule_ids = set(ignored_user.excluded_rule_ids_json)

            # 3. جلب حالة المحادثة الحالية
            conv_state = await session.scalar(
                select(ConversationState).where(
                    ConversationState.chat_id == envelope.chat_id
                )
            )
            first_message_handled = (
                bool(conv_state.first_message_handled) if conv_state else False
            )
            cooldowns_map: Dict[str, str] = (
                dict(conv_state.rule_cooldowns_json or {}) if conv_state else {}
            )
            counts_map: Dict[str, int] = (
                dict(conv_state.rule_counts_json or {}) if conv_state else {}
            )

            # 4. جلب جميع القواعد المفعلة مرتبة تنازليًا حسب الأولوية ثم الأقدم
            stmt = (
                select(AutoReplyRule)
                .where(AutoReplyRule.is_active.is_(True))
                .order_by(AutoReplyRule.priority.desc(), AutoReplyRule.id.asc())
            )
            active_rules = list((await session.scalars(stmt)).all())

        matched_rules: List[AutoReplyRule] = []
        selected_template_ids: set[int] = set()

        for rule in active_rules:
            # استثناء القاعدة للمستخدم المحدد
            if rule.id in user_excluded_rule_ids:
                continue
            if envelope.sender_id in (rule.excluded_user_ids_json or []):
                continue

            # التحقق من نوع المحادثة المستهدفة
            if rule.target_chat_types not in {"all", envelope.chat_type}:
                continue

            # التحقق من نطاق ساعات العمل (Scope)
            if rule.scope == "work_hours_only" and not is_within_work_hours:
                continue
            if rule.scope == "outside_work_hours" and is_within_work_hours:
                continue

            # التحقق من الحد الأقصى لعدد مرات التنفيذ في المحادثة
            rule_key = str(rule.id)
            executed_in_chat = int(counts_map.get(rule_key, 0))
            if (
                rule.max_executions_per_chat is not None
                and executed_in_chat >= rule.max_executions_per_chat
            ):
                continue

            # التحقق من فترة الانتظار (Cooldown)
            if rule.cooldown_seconds > 0 and rule_key in cooldowns_map:
                try:
                    last_exec_iso = cooldowns_map[rule_key]
                    last_exec_dt = datetime.fromisoformat(last_exec_iso)
                    if last_exec_dt.tzinfo is None:
                        last_exec_dt = last_exec_dt.replace(tzinfo=timezone.utc)
                    elapsed = (now - last_exec_dt).total_seconds()
                    if elapsed < rule.cooldown_seconds:
                        continue
                except Exception:
                    pass

            # التحقق من الشروط الإضافية (conditions_json)
            if not self._check_additional_conditions(rule.conditions_json or {}, envelope, now):
                continue

            # التحقق من المحفز الأساسي (Trigger Matching)
            if not self._matches_trigger(rule, envelope.text, first_message_handled):
                continue

            # منع إرسال نفس القالب مرتين في نفس الرسالة حتى لو تطابقت قاعدتان
            if rule.template_id in selected_template_ids:
                continue

            matched_rules.append(rule)
            selected_template_ids.add(rule.template_id)

            # إذا كانت القاعدة لا تسمح بتنفيذ قواعد إضافية بعدها نتوقف فورًا
            if not rule.allow_multiple:
                break

        return matched_rules

    def _matches_trigger(
        self,
        rule: AutoReplyRule,
        message_text: Optional[str],
        first_message_handled: bool,
    ) -> bool:
        """فحص مطابقة المحفز (first_message, keyword, regex, any)."""
        t_type = rule.trigger_type.lower().strip()
        text = (message_text or "").strip()

        if t_type == "first_message":
            return not first_message_handled

        if t_type == "any":
            return True

        if not text:
            return False

        trigger_val = (rule.trigger_value or "").strip()
        if not trigger_val:
            return False

        conditions = rule.conditions_json or {}
        case_sensitive = bool(conditions.get("case_sensitive", False))
        exact_match = bool(conditions.get("exact_match", False))

        if t_type == "keyword":
            # دعم عدة كلمات مفتاحية مفصولة بفاصلة (، أو ,) أو سطر جديد
            raw_keywords = re.split(r"[,،\n]+", trigger_val)
            keywords = [k.strip() for k in raw_keywords if k.strip()]
            compare_text = text if case_sensitive else text.lower()

            for kw in keywords:
                compare_kw = kw if case_sensitive else kw.lower()
                if exact_match:
                    if compare_text == compare_kw:
                        return True
                else:
                    if compare_kw in compare_text:
                        return True
            return False

        if t_type == "regex":
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                return bool(re.search(trigger_val, text, flags=flags))
            except re.error as exc:
                logger.error("تعبير نمطي تالف في القاعدة %s: %s", rule.id, exc)
                return False

        return False

    def _check_additional_conditions(
        self,
        conditions: Dict[str, Any],
        envelope: IncomingMessageEnvelope,
        now: datetime,
    ) -> bool:
        """فحص الشروط الإضافية الاختيارية للقاعدة."""
        if not conditions:
            return True

        # شرط وجود مرفقات فقط أو نص فقط
        if conditions.get("require_media") is True and not envelope.has_media:
            return False
        if conditions.get("text_only") is True and envelope.has_media:
            return False

        # شرط نوع الوسيط المحدد
        allowed_media_types = conditions.get("allowed_media_types")
        if allowed_media_types and envelope.media_type not in allowed_media_types:
            return False

        # شرط المحادثات المحددة
        allowed_chat_ids = conditions.get("allowed_chat_ids")
        if allowed_chat_ids and envelope.chat_id not in allowed_chat_ids:
            return False

        # شرط طول النص
        text_len = len(envelope.text or "")
        min_len = conditions.get("min_length")
        if min_len is not None and text_len < int(min_len):
            return False
        max_len = conditions.get("max_length")
        if max_len is not None and text_len > int(max_len):
            return False

        return True
