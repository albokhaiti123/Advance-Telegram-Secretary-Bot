from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# 1. إعدادات النظام
class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# 2. اتصالات Telegram Business
class BusinessConnection(Base):
    __tablename__ = "business_connections"

    connection_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_chat_id: Mapped[int] = mapped_column(Integer, nullable=False)
    can_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    rights_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# 3. جلسة الحساب الشخصي (MTProto)
class PersonalAccountSession(Base):
    __tablename__ = "personal_account_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    telegram_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, unique=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    encrypted_session: Mapped[str] = mapped_column(Text, nullable=False)
    is_authorized: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    status_message: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# 4. مراجع الوسائط
class MediaReference(Base):
    __tablename__ = "media_references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_unique_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)  # photo, video, document, voice, audio, sticker
    bot_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    local_file_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    mtproto_file_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# 5. قوالب الردود
class ReplyTemplate(Base):
    __tablename__ = "reply_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    text_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media_ref_id: Mapped[Optional[int]] = mapped_column(ForeignKey("media_references.id", ondelete="SET NULL"), nullable=True)
    parse_mode: Mapped[str] = mapped_column(String(16), default="HTML")
    disable_web_page_preview: Mapped[bool] = mapped_column(Boolean, default=True)
    is_quick_reply: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    media_ref: Mapped[Optional["MediaReference"]] = relationship("MediaReference")
    buttons: Mapped[List["ReplyButton"]] = relationship(
        "ReplyButton", back_populates="template", cascade="all, delete-orphan"
    )


# 6. الأزرار التفاعلية المرتبطة بالقوالب أو الحملات
class ReplyButton(Base):
    __tablename__ = "reply_buttons"
    __table_args__ = (
        Index("ix_reply_buttons_template_order", "template_id", "row_index", "col_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[Optional[int]] = mapped_column(ForeignKey("reply_templates.id", ondelete="CASCADE"), nullable=True)
    campaign_id: Mapped[Optional[int]] = mapped_column(ForeignKey("broadcast_campaigns.id", ondelete="CASCADE"), nullable=True)
    text: Mapped[str] = mapped_column(String(128), nullable=False)
    button_type: Mapped[str] = mapped_column(String(16), nullable=False, default="url")  # url | callback
    url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    callback_data: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    row_index: Mapped[int] = mapped_column(Integer, default=0)
    col_index: Mapped[int] = mapped_column(Integer, default=0)

    template: Mapped[Optional["ReplyTemplate"]] = relationship("ReplyTemplate", back_populates="buttons")


# 7. قواعد الرد التلقائي
class AutoReplyRule(Base):
    __tablename__ = "auto_reply_rules"
    __table_args__ = (
        Index("ix_rules_active_priority", "is_active", "priority"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)  # first_message, keyword, regex, any
    trigger_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("reply_templates.id", ondelete="RESTRICT"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    allow_multiple: Mapped[bool] = mapped_column(Boolean, default=False)
    scope: Mapped[str] = mapped_column(String(32), default="all")  # all, specific_chats, work_hours_only, outside_work_hours
    target_chat_types: Mapped[str] = mapped_column(String(32), default="private")
    excluded_user_ids_json: Mapped[List[int]] = mapped_column(JSON, default=list)
    delay_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=60)
    max_executions_per_chat: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    auto_delete_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    conditions_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    execution_count: Mapped[int] = mapped_column(Integer, default=0)
    last_executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    template: Mapped["ReplyTemplate"] = relationship("ReplyTemplate")


# 8. المحادثات
class Conversation(Base):
    __tablename__ = "conversations"

    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_type: Mapped[str] = mapped_column(String(32), default="private")
    title_or_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    business_connection_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    auto_reply_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    reply_count: Mapped[int] = mapped_column(Integer, default=0)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    state: Mapped[Optional["ConversationState"]] = relationship(
        "ConversationState", back_populates="conversation", uselist=False, cascade="all, delete-orphan"
    )


# 9. حالة كل محادثة (الـ Cooldown والعمليات المعلقة)
class ConversationState(Base):
    __tablename__ = "conversation_states"

    chat_id: Mapped[int] = mapped_column(ForeignKey("conversations.chat_id", ondelete="CASCADE"), primary_key=True)
    first_message_handled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_rule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("auto_reply_rules.id", ondelete="SET NULL"), nullable=True)
    last_reply_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rule_cooldowns_json: Mapped[Dict[str, str]] = mapped_column(JSON, default=dict)
    rule_counts_json: Mapped[Dict[str, int]] = mapped_column(JSON, default=dict)
    away_last_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    pending_operation_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="state")


# 10. المستخدمون المستثنون (قوائم التجاهل والسماح)
class IgnoredUser(Base):
    __tablename__ = "ignored_users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    list_type: Mapped[str] = mapped_column(String(16), default="blacklist", index=True)  # blacklist | whitelist
    excluded_rule_ids_json: Mapped[List[int]] = mapped_column(JSON, default=list)
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# 11. المحادثات المستثناة
class IgnoredConversation(Base):
    __tablename__ = "ignored_conversations"

    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    list_type: Mapped[str] = mapped_column(String(16), default="blacklist", index=True)  # blacklist | whitelist
    excluded_media_types_json: Mapped[List[str]] = mapped_column(JSON, default=list)
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# 12. الردود التي أرسلها النظام (لإدارة حذف الرد السابق واستبداله)
class ManagedResponse(Base):
    __tablename__ = "managed_responses"
    __table_args__ = (
        Index("ix_managed_responses_chat_latest", "chat_id", "is_latest_in_chat"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("conversations.chat_id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("auto_reply_rules.id", ondelete="SET NULL"), nullable=True)
    business_connection_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_latest_in_chat: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="active")  # active, replaced, deleted, delete_failed
    delete_retry_count: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_delete_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# 13. المهام المجدولة
class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"
    __table_args__ = (
        Index("ix_scheduled_tasks_status_run_at", "status", "run_at"),
    )

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_type: Mapped[str] = mapped_column(String(48), nullable=False)  # delayed_reply, auto_delete, campaign_run, retry_delete
    reference_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    payload_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cron_expression: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Aden")
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending, running, completed, cancelled, failed
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# 14. المجموعات المكتشفة عبر الحساب الشخصي
class Group(Base):
    __tablename__ = "groups"

    group_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    access_hash: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    group_type: Mapped[str] = mapped_column(String(32), default="supergroup")  # group | supergroup
    member_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    can_send_messages: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    can_send_media: Mapped[bool] = mapped_column(Boolean, default=True)
    can_send_inline: Mapped[bool] = mapped_column(Boolean, default=True)
    slow_mode_seconds: Mapped[int] = mapped_column(Integer, default=0)
    permission_status: Mapped[str] = mapped_column(String(64), default="verified")  # verified, restricted, forbidden, unknown
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# 15. تصنيفات المجموعات (Collections)
class GroupCollection(Base):
    __tablename__ = "group_collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    members: Mapped[List["GroupCollectionMember"]] = relationship(
        "GroupCollectionMember", back_populates="collection", cascade="all, delete-orphan"
    )


# 16. ربط المجموعات بالتصنيفات
class GroupCollectionMember(Base):
    __tablename__ = "group_collection_members"
    __table_args__ = (
        UniqueConstraint("collection_id", "group_id", name="uq_collection_group"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_id: Mapped[int] = mapped_column(ForeignKey("group_collections.id", ondelete="CASCADE"), nullable=False)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.group_id", ondelete="CASCADE"), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    collection: Mapped["GroupCollection"] = relationship("GroupCollection", back_populates="members")
    group: Mapped["Group"] = relationship("Group")


# 17. حملات البث الإعلانية
class BroadcastCampaign(Base):
    __tablename__ = "broadcast_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(32), default="text")
    text_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media_ref_id: Mapped[Optional[int]] = mapped_column(ForeignKey("media_references.id", ondelete="SET NULL"), nullable=True)
    parse_mode: Mapped[str] = mapped_column(String(16), default="HTML")
    collection_id: Mapped[Optional[int]] = mapped_column(ForeignKey("group_collections.id", ondelete="SET NULL"), nullable=True)
    excluded_group_ids_json: Mapped[List[int]] = mapped_column(JSON, default=list)
    schedule_type: Mapped[str] = mapped_column(String(32), default="immediate")  # immediate, once, daily, weekly, cron
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cron_expression: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    active_run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# 18. المجموعات المستهدفة لكل حملة
class BroadcastRecipient(Base):
    __tablename__ = "broadcast_recipients"
    __table_args__ = (
        UniqueConstraint("campaign_id", "group_id", name="uq_campaign_recipient"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("broadcast_campaigns.id", ondelete="CASCADE"), nullable=False)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.group_id", ondelete="CASCADE"), nullable=False)
    is_excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# 19. عمليات تنفيذ الحملات (Run History & Active Run Control)
class BroadcastRun(Base):
    __tablename__ = "broadcast_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("broadcast_campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)  # running, paused, completed, partial, cancelled, superseded, failed
    total_targets: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# 20. سجلات تسليم رسائل البث لكل مجموعة
class BroadcastDeliveryLog(Base):
    __tablename__ = "broadcast_delivery_logs"
    __table_args__ = (
        Index("ix_delivery_logs_run_status", "run_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("broadcast_runs.run_id", ondelete="CASCADE"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("broadcast_campaigns.id", ondelete="CASCADE"), nullable=False)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.group_id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # sent, failed, skipped
    telegram_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# 21. التنبيهات الإدارية للمالك
class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(16), default="info")  # info, warning, error, critical
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_sent_to_owner: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# 22. سجل النشاط العام
class ActivityLog(Base):
    __tablename__ = "activity_logs"
    __table_args__ = (
        Index("ix_activity_logs_category_created", "category", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    target_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    details_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# 23. سجل أخطاء النظام والاستثناءات
class SystemError(Base):
    __tablename__ = "system_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    component: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    error_type: Mapped[str] = mapped_column(String(128), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    traceback_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
