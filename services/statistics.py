from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import func, select

from database.database import DatabaseManager
from database.models import (
    ActivityLog,
    AutoReplyRule,
    BroadcastCampaign,
    BroadcastDeliveryLog,
    BusinessConnection,
    Conversation,
    Group,
    ManagedResponse,
    PersonalAccountSession,
    ScheduledTask,
    SystemError,
)


@dataclass
class RepliesStatsReport:
    total_messages_received: int
    total_auto_replies_sent: int
    total_rules_executed: int
    top_rules: List[Tuple[str, int]]
    failed_replies_count: int
    deleted_replies_count: int
    replaced_replies_count: int


@dataclass
class CampaignsStatsReport:
    discovered_groups_count: int
    eligible_groups_count: int
    total_campaigns_count: int
    total_messages_attempted: int
    successful_deliveries: int
    failed_deliveries: int
    skipped_deliveries: int
    overall_success_rate: float


@dataclass
class SystemHealthReport:
    business_connected: bool
    personal_authorized: bool
    pending_tasks_count: int
    failed_tasks_count: int
    unresolved_errors_count: int
    uptime_seconds: int


class StatisticsService:
    """
    حساب وتجميع إحصائيات الردود، المجموعات والحملات، وحالة النظام
    مع دعم التقارير حسب اليوم والأسبوع والشهر.
    """

    def __init__(self, db_manager: DatabaseManager) -> None:
        self.db_manager = db_manager
        self.started_at = datetime.now(timezone.utc)

    @staticmethod
    def _resolve_since_datetime(period: str) -> Optional[datetime]:
        now = datetime.now(timezone.utc)
        if period == "daily":
            return now - timedelta(days=1)
        if period == "weekly":
            return now - timedelta(days=7)
        if period == "monthly":
            return now - timedelta(days=30)
        return None

    async def get_replies_statistics(
        self, period: str = "all"
    ) -> RepliesStatsReport:
        """إحصائيات الردود والقواعد (اليوم / الأسبوع / الشهر / الكل)."""
        since_dt = self._resolve_since_datetime(period)

        async with self.db_manager.session() as session:
            # إجمالي الرسائل المستلمة
            conv_stmt = select(func.coalesce(func.sum(Conversation.message_count), 0))
            if since_dt:
                conv_stmt = conv_stmt.where(Conversation.last_activity_at >= since_dt)
            total_received = int(await session.scalar(conv_stmt) or 0)

            # إجمالي الردود التلقائية المرسلة
            sent_stmt = select(func.count()).select_from(ManagedResponse)
            if since_dt:
                sent_stmt = sent_stmt.where(ManagedResponse.sent_at >= since_dt)
            total_sent = int(await session.scalar(sent_stmt) or 0)

            # الردود المستبدلة والمحذوفة
            replaced_stmt = (
                select(func.count())
                .select_from(ManagedResponse)
                .where(ManagedResponse.status == "replaced")
            )
            deleted_stmt = (
                select(func.count())
                .select_from(ManagedResponse)
                .where(ManagedResponse.status.in_(["deleted", "replaced"]))
            )
            if since_dt:
                replaced_stmt = replaced_stmt.where(ManagedResponse.sent_at >= since_dt)
                deleted_stmt = deleted_stmt.where(ManagedResponse.sent_at >= since_dt)

            replaced_count = int(await session.scalar(replaced_stmt) or 0)
            deleted_count = int(await session.scalar(deleted_stmt) or 0)

            # الردود الفاشلة
            err_stmt = (
                select(func.count())
                .select_from(SystemError)
                .where(SystemError.component == "BusinessSender.send_reply")
            )
            if since_dt:
                err_stmt = err_stmt.where(SystemError.created_at >= since_dt)
            failed_replies = int(await session.scalar(err_stmt) or 0)

            # القواعد المنفذة وأكثرها استخدامًا
            rules = list(
                (
                    await session.scalars(
                        select(AutoReplyRule).order_by(
                            AutoReplyRule.execution_count.desc()
                        )
                    )
                ).all()
            )
            total_rules_exec = sum(r.execution_count or 0 for r in rules)
            top_rules = [
                (r.name, int(r.execution_count or 0))
                for r in rules[:5]
                if (r.execution_count or 0) > 0
            ]

            return RepliesStatsReport(
                total_messages_received=total_received,
                total_auto_replies_sent=total_sent,
                total_rules_executed=total_rules_exec,
                top_rules=top_rules,
                failed_replies_count=failed_replies,
                deleted_replies_count=deleted_count,
                replaced_replies_count=replaced_count,
            )

    async def get_campaigns_statistics(
        self, period: str = "all"
    ) -> CampaignsStatsReport:
        """إحصائيات المجموعات والحملات الإعلانية ونسب النجاح."""
        since_dt = self._resolve_since_datetime(period)

        async with self.db_manager.session() as session:
            discovered = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Group)
                    .where(Group.is_active.is_(True))
                )
                or 0
            )
            eligible = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Group)
                    .where(
                        Group.is_active.is_(True),
                        Group.can_send_messages.is_(True),
                    )
                )
                or 0
            )

            camp_stmt = select(func.count()).select_from(BroadcastCampaign)
            if since_dt:
                camp_stmt = camp_stmt.where(BroadcastCampaign.created_at >= since_dt)
            total_campaigns = int(await session.scalar(camp_stmt) or 0)

            # نتائج الإرسال من جدول broadcast_delivery_logs
            base_log_stmt = select(
                BroadcastDeliveryLog.status,
                func.count(BroadcastDeliveryLog.id),
            ).group_by(BroadcastDeliveryLog.status)

            if since_dt:
                base_log_stmt = base_log_stmt.where(
                    BroadcastDeliveryLog.attempted_at >= since_dt
                )

            rows = (await session.execute(base_log_stmt)).all()
            counts_by_status: Dict[str, int] = {
                str(status): int(cnt) for status, cnt in rows
            }

            sent_ok = counts_by_status.get("sent", 0)
            failed = counts_by_status.get("failed", 0)
            skipped = counts_by_status.get("skipped", 0)
            total_attempted = sent_ok + failed + skipped

            denom = sent_ok + failed
            success_rate = round((sent_ok / denom) * 100.0, 2) if denom > 0 else 100.0

            return CampaignsStatsReport(
                discovered_groups_count=discovered,
                eligible_groups_count=eligible,
                total_campaigns_count=total_campaigns,
                total_messages_attempted=total_attempted,
                successful_deliveries=sent_ok,
                failed_deliveries=failed,
                skipped_deliveries=skipped,
                overall_success_rate=success_rate,
            )

    async def get_system_health_statistics(self) -> SystemHealthReport:
        """إحصائيات النظام والاتصالات والمهام والأخطاء ومدة التشغيل."""
        now = datetime.now(timezone.utc)
        uptime_sec = int((now - self.started_at).total_seconds())

        async with self.db_manager.session() as session:
            biz_active = (
                await session.scalar(
                    select(func.count())
                    .select_from(BusinessConnection)
                    .where(BusinessConnection.is_active.is_(True))
                )
                or 0
            ) > 0

            personal_active = (
                await session.scalar(
                    select(func.count())
                    .select_from(PersonalAccountSession)
                    .where(PersonalAccountSession.is_authorized.is_(True))
                )
                or 0
            ) > 0

            pending_tasks = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ScheduledTask)
                    .where(ScheduledTask.status == "pending")
                )
                or 0
            )
            failed_tasks = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ScheduledTask)
                    .where(ScheduledTask.status == "failed")
                )
                or 0
            )
            unresolved_errors = int(
                await session.scalar(
                    select(func.count())
                    .select_from(SystemError)
                    .where(SystemError.resolved.is_(False))
                )
                or 0
            )

            return SystemHealthReport(
                business_connected=biz_active,
                personal_authorized=personal_active,
                pending_tasks_count=pending_tasks,
                failed_tasks_count=failed_tasks,
                unresolved_errors_count=unresolved_errors,
                uptime_seconds=uptime_sec,
            )

    async def list_activity_logs(
        self, category: Optional[str] = None, limit: int = 15
    ) -> List[ActivityLog]:
        """جلب سجلات النشاط مصنفة حسب الفئة."""
        async with self.db_manager.session() as session:
            stmt = select(ActivityLog)
            if category:
                stmt = stmt.where(ActivityLog.category == category)
            stmt = stmt.order_by(ActivityLog.id.desc()).limit(limit)
            return list((await session.scalars(stmt)).all())

    async def list_system_errors(self, limit: int = 10) -> List[SystemError]:
        """جلب آخر أخطاء النظام لعرضها في لوحة التحكم."""
        async with self.db_manager.session() as session:
            stmt = (
                select(SystemError)
                .order_by(SystemError.id.desc())
                .limit(limit)
            )
            return list((await session.scalars(stmt)).all())
