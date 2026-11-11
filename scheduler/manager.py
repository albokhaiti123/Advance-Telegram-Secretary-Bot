import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select

from database.database import DatabaseManager
from database.models import ScheduledTask
from integrations.business.sender import BusinessSender
from services.broadcast_manager import BroadcastManager
from services.campaign_manager import CampaignManager
from services.response_replacement import ResponseReplacementManager

logger = logging.getLogger("secretary_bot.scheduler")


class UnifiedSchedulerManager:
    """مدير المهام المجدولة الموحد مع التقاط دوري للمهام المستجدة في قاعدة البيانات."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        business_sender: BusinessSender,
        replacement_manager: ResponseReplacementManager,
        campaign_manager: CampaignManager,
        broadcast_manager: BroadcastManager,
        timezone_str: str = "Asia/Aden",
    ) -> None:
        self.db_manager = db_manager
        self.business_sender = business_sender
        self.replacement_manager = replacement_manager
        self.campaign_manager = campaign_manager
        self.broadcast_manager = broadcast_manager
        self.timezone_str = timezone_str

        try:
            self.tz = ZoneInfo(timezone_str)
        except Exception:
            self.tz = ZoneInfo("UTC")

        self.scheduler = AsyncIOScheduler(timezone=self.tz)

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()
            self.scheduler.add_job(
                self.poll_due_pending_tasks,
                trigger="interval",
                seconds=30,
                id="internal_poll_due_tasks",
                replace_existing=True,
            )

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def poll_due_pending_tasks(self) -> None:
        """التقاط وتنفيذ أي مهام مجدولة في قاعدة البيانات حان وقت تنفيذها."""
        now = datetime.now(timezone.utc)
        async with self.db_manager.session() as session:
            due_tasks = list(
                (
                    await session.scalars(
                        select(ScheduledTask).where(
                            ScheduledTask.status == "pending",
                            ScheduledTask.cron_expression.is_(None),
                            ScheduledTask.run_at.is_not(None),
                            ScheduledTask.run_at <= now,
                        )
                    )
                ).all()
            )
        for task in due_tasks:
            await self.execute_scheduled_task(task.task_id)

    async def schedule_campaign_task(
        self,
        campaign_id: int,
        run_at: Optional[datetime] = None,
        cron_expression: Optional[str] = None,
    ) -> ScheduledTask:
        now = datetime.now(timezone.utc)
        task_id = f"camp_sched_{campaign_id}_{uuid.uuid4().hex[:8]}"

        async with self.db_manager.session() as session:
            prev_tasks = list(
                (
                    await session.scalars(
                        select(ScheduledTask).where(
                            ScheduledTask.reference_id == str(campaign_id),
                            ScheduledTask.task_type.in_(
                                ["campaign_run", "recurring_campaign"]
                            ),
                            ScheduledTask.status == "pending",
                        )
                    )
                ).all()
            )
            for pt in prev_tasks:
                pt.status = "cancelled"
                pt.updated_at = now
                if self.scheduler.get_job(pt.task_id):
                    self.scheduler.remove_job(pt.task_id)

            t_type = "recurring_campaign" if cron_expression else "campaign_run"
            task = ScheduledTask(
                task_id=task_id,
                task_type=t_type,
                reference_id=str(campaign_id),
                payload_json={"campaign_id": campaign_id},
                run_at=run_at,
                cron_expression=cron_expression,
                timezone=self.timezone_str,
                status="pending",
            )
            session.add(task)

        self._register_apscheduler_job(task)
        return task

    def _register_apscheduler_job(self, task: ScheduledTask) -> None:
        if self.scheduler.get_job(task.task_id):
            self.scheduler.remove_job(task.task_id)

        if task.cron_expression:
            trigger = CronTrigger.from_crontab(task.cron_expression, timezone=self.tz)
        else:
            run_time = task.run_at or datetime.now(timezone.utc)
            trigger = DateTrigger(run_date=run_time, timezone=self.tz)

        self.scheduler.add_job(
            self.execute_scheduled_task,
            trigger=trigger,
            id=task.task_id,
            args=[task.task_id],
            replace_existing=True,
        )

    async def execute_scheduled_task(self, task_id: str) -> bool:
        now = datetime.now(timezone.utc)
        async with self.db_manager.session() as session:
            task = await session.get(ScheduledTask, task_id)
            if not task or task.status in {"cancelled", "completed"}:
                return False
            task.status = "running"
            task.updated_at = now
            t_type = task.task_type
            payload = dict(task.payload_json or {})

        try:
            if t_type in {"campaign_run", "recurring_campaign"}:
                camp_id = int(payload["campaign_id"])
                run_obj = await self.campaign_manager.initiate_new_run(camp_id)
                await self.broadcast_manager.execute_campaign_run(
                    camp_id, run_obj.run_id
                )

            elif t_type in {"auto_delete", "retry_delete"}:
                managed_id = int(payload["managed_response_id"])
                ok = await self.replacement_manager.retry_failed_deletion(managed_id)
                if not ok:
                    raise RuntimeError(f"فشل حذف الرسالة التلقائية #{managed_id}")

            async with self.db_manager.session() as session:
                task = await session.get(ScheduledTask, task_id)
                if task:
                    task.status = (
                        "pending" if task.cron_expression else "completed"
                    )
                    task.updated_at = datetime.now(timezone.utc)
            return True

        except Exception as exc:
            logger.error("فشل تنفيذ المهمة المجدولة %s: %s", task_id, exc)
            async with self.db_manager.session() as session:
                task = await session.get(ScheduledTask, task_id)
                if task:
                    task.retry_count = (task.retry_count or 0) + 1
                    task.last_error = str(exc)
                    if task.retry_count >= task.max_retries:
                        task.status = "failed"
                    else:
                        task.status = "pending"
            return False
