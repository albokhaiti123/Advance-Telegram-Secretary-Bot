import logging
from datetime import datetime, timezone
from sqlalchemy import select

from database.database import DatabaseManager
from database.models import BroadcastCampaign, BroadcastRun, ConversationState, ScheduledTask
from scheduler.manager import UnifiedSchedulerManager

logger = logging.getLogger("secretary_bot.scheduler.recovery")


class TaskRecoveryService:
    """
    استعادة المهام المجدولة والعمليات المعلقة بعد إعادة تشغيل البرنامج أو الخادم (VPS).
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        scheduler_manager: UnifiedSchedulerManager,
    ) -> None:
        self.db_manager = db_manager
        self.scheduler_manager = scheduler_manager

    async def recover_all_on_startup(self) -> dict[str, int]:
        """
        1. تنظيف العمليات المعلقة في المحادثات (pending_operation_json).
        2. استعادة المهام المجدولة التي لم تكتمل (pending) أو المتأخرة أثناء التوقف.
        3. تحويل الحملات التي انقطع الخادم أثناء إرسالها إلى حالة paused ليتمكن المالك من استئنافها بأمان.
        """
        recovered_tasks = 0
        executed_overdue = 0
        cleaned_chat_ops = 0
        paused_interrupted_runs = 0
        now = datetime.now(timezone.utc)

        async with self.db_manager.session() as session:
            # 1. تنظيف الأقفال المعلقة في المحادثات
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
                cleaned_chat_ops += 1

            # 2. تأمين الحملات التي كانت في حالة running لحظة انقطاع الخادم
            interrupted_runs = list(
                (
                    await session.scalars(
                        select(BroadcastRun).where(BroadcastRun.status == "running")
                    )
                ).all()
            )
            for r in interrupted_runs:
                r.status = "paused"
                paused_interrupted_runs += 1
                camp = await session.get(BroadcastCampaign, r.campaign_id)
                if camp and camp.status == "running":
                    camp.status = "paused"

            # 3. جلب المهام المجدولة النشطة
            pending_tasks = list(
                (
                    await session.scalars(
                        select(ScheduledTask).where(
                            ScheduledTask.status.in_(["pending", "running"])
                        )
                    )
                ).all()
            )

        for task in pending_tasks:
            if task.cron_expression:
                self.scheduler_manager._register_apscheduler_job(task)
                recovered_tasks += 1
            elif task.run_at:
                run_at_utc = (
                    task.run_at
                    if task.run_at.tzinfo
                    else task.run_at.replace(tzinfo=timezone.utc)
                )
                if run_at_utc <= now:
                    # مهمة حان وقتها أثناء توقف الخادم -> ننفذها فورًا
                    await self.scheduler_manager.execute_scheduled_task(task.task_id)
                    executed_overdue += 1
                else:
                    self.scheduler_manager._register_apscheduler_job(task)
                    recovered_tasks += 1

        return {
            "recovered_tasks": recovered_tasks,
            "executed_overdue": executed_overdue,
            "cleaned_chat_ops": cleaned_chat_ops,
            "paused_interrupted_runs": paused_interrupted_runs,
        }
