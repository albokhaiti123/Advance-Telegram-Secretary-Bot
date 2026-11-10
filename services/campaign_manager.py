import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence
from sqlalchemy import delete, select, update

from database.database import DatabaseManager
from database.models import (
    ActivityLog,
    BroadcastCampaign,
    BroadcastRecipient,
    BroadcastRun,
    ScheduledTask,
)
from services.group_manager import GroupManager


class CampaignManager:
    """
    إدارة دورة حياة الحملات الإعلانية:
    - إنشاء حملة وتحديد المجموعات المستهدفة والمستثناة.
    - بدء التشغيل وإنشاء run_id فريد.
    - إيقاف مؤقت، استئناف، وإلغاء.
    - استبدال حملة قديمة بحملة جديدة مع إبطال run_id القديم وإيقاف جميع مهامه في قاعدة البيانات فورًا.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        group_manager: GroupManager,
    ) -> None:
        self.db_manager = db_manager
        self.group_manager = group_manager

    async def create_campaign(
        self,
        name: str,
        content_type: str = "text",
        text_content: Optional[str] = None,
        media_ref_id: Optional[int] = None,
        collection_id: Optional[int] = None,
        specific_group_ids: Optional[Sequence[int]] = None,
        excluded_group_ids: Optional[Sequence[int]] = None,
        schedule_type: str = "immediate",
        scheduled_at: Optional[datetime] = None,
        cron_expression: Optional[str] = None,
    ) -> BroadcastCampaign:
        """إنشاء حملة جديدة بصيغة مسودة (draft) وربط المجموعات المستهدفة بها."""
        target_groups = await self.group_manager.resolve_campaign_target_groups(
            collection_id=collection_id,
            specific_group_ids=specific_group_ids,
            excluded_group_ids=excluded_group_ids,
        )
        excluded_set = set(excluded_group_ids or [])

        async with self.db_manager.session() as session:
            campaign = BroadcastCampaign(
                name=name.strip(),
                content_type=content_type,
                text_content=text_content,
                media_ref_id=media_ref_id,
                collection_id=collection_id,
                excluded_group_ids_json=list(excluded_set),
                schedule_type=schedule_type,
                scheduled_at=scheduled_at,
                cron_expression=cron_expression,
                status="draft",
                active_run_id=None,
            )
            session.add(campaign)
            await session.flush()

            for grp in target_groups:
                session.add(
                    BroadcastRecipient(
                        campaign_id=campaign.id,
                        group_id=grp.group_id,
                        is_excluded=grp.group_id in excluded_set,
                    )
                )

            session.add(
                ActivityLog(
                    category="campaign",
                    action="create_campaign",
                    target_id=str(campaign.id),
                    details_json={
                        "name": campaign.name,
                        "targets_count": len(target_groups),
                        "schedule_type": schedule_type,
                    },
                )
            )
            await session.flush()
            return campaign

    async def initiate_new_run(self, campaign_id: int) -> BroadcastRun:
        """
        بدء تنفيذ جديد للحملة:
        - يُبطل أي run_id سابق كان قيد التشغيل لنفس الحملة.
        - ينشئ run_id جديد ويضبطه كـ active_run_id.
        """
        new_run_id = f"run_{campaign_id}_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        async with self.db_manager.session() as session:
            campaign = await session.get(BroadcastCampaign, campaign_id)
            if not campaign:
                raise ValueError(f"الحملة #{campaign_id} غير موجودة.")

            # إبطال أي تشغيل قديم ما زال مفتوحًا
            if campaign.active_run_id:
                old_run = await session.get(BroadcastRun, campaign.active_run_id)
                if old_run and old_run.status in {"running", "paused"}:
                    old_run.status = "superseded"
                    old_run.completed_at = now

                await session.execute(
                    update(ScheduledTask)
                    .where(
                        ScheduledTask.run_id == campaign.active_run_id,
                        ScheduledTask.status.in_(["pending", "running"]),
                    )
                    .values(status="cancelled", updated_at=now)
                )

            recipients = list(
                (
                    await session.scalars(
                        select(BroadcastRecipient).where(
                            BroadcastRecipient.campaign_id == campaign_id,
                            BroadcastRecipient.is_excluded.is_(False),
                        )
                    )
                ).all()
            )

            new_run = BroadcastRun(
                run_id=new_run_id,
                campaign_id=campaign_id,
                status="running",
                total_targets=len(recipients),
                sent_count=0,
                failed_count=0,
                skipped_count=0,
                started_at=now,
            )
            session.add(new_run)

            campaign.active_run_id = new_run_id
            campaign.status = "running"
            campaign.updated_at = now

            await session.flush()
            return new_run

    async def pause_campaign(self, campaign_id: int) -> bool:
        """إيقاف الحملة الجارية مؤقتًا."""
        async with self.db_manager.session() as session:
            campaign = await session.get(BroadcastCampaign, campaign_id)
            if not campaign or not campaign.active_run_id:
                return False
            campaign.status = "paused"
            run_rec = await session.get(BroadcastRun, campaign.active_run_id)
            if run_rec and run_rec.status == "running":
                run_rec.status = "paused"
            return True

    async def resume_campaign(self, campaign_id: int) -> Optional[str]:
        """استئناف حملة متوقفة مؤقتًا باستخدام نفس الـ run_id لاستكمال المجموعات المتبقية."""
        async with self.db_manager.session() as session:
            campaign = await session.get(BroadcastCampaign, campaign_id)
            if not campaign or not campaign.active_run_id:
                return None
            run_rec = await session.get(BroadcastRun, campaign.active_run_id)
            if not run_rec or run_rec.status != "paused":
                return None
            campaign.status = "running"
            run_rec.status = "running"
            return run_rec.run_id

    async def cancel_campaign(self, campaign_id: int) -> bool:
        """إلغاء الحملة وإيقاف جميع مهامها المجدولة نهائيًا."""
        now = datetime.now(timezone.utc)
        async with self.db_manager.session() as session:
            campaign = await session.get(BroadcastCampaign, campaign_id)
            if not campaign:
                return False
            old_run_id = campaign.active_run_id
            campaign.status = "cancelled"
            campaign.active_run_id = None
            campaign.updated_at = now

            if old_run_id:
                run_rec = await session.get(BroadcastRun, old_run_id)
                if run_rec and run_rec.status in {"running", "paused"}:
                    run_rec.status = "cancelled"
                    run_rec.completed_at = now

            await session.execute(
                update(ScheduledTask)
                .where(
                    ScheduledTask.reference_id == str(campaign_id),
                    ScheduledTask.status.in_(["pending", "running"]),
                )
                .values(status="cancelled", updated_at=now)
            )
            return True

    async def replace_campaign(
        self,
        campaign_id: int,
        new_name: Optional[str] = None,
        new_text_content: Optional[str] = None,
        new_content_type: Optional[str] = None,
        new_media_ref_id: Optional[int] = None,
        new_collection_id: Optional[int] = None,
        new_excluded_group_ids: Optional[Sequence[int]] = None,
        start_immediately: bool = True,
    ) -> BroadcastRun:
        """
        استبدال حملة قديمة بتحديثات جديدة (القسم 12.3 من وثيقة المواصفات):
        1. إبطال معرف التنفيذ القديم run_id وتحويل حالته إلى superseded.
        2. إلغاء جميع المهام المجدولة التابعة للتنفيذ القديم.
        3. تحديث محتوى الحملة والمجموعات المستهدفة.
        4. إنشاء تنفيذ جديد مستقل بـ run_id جديد تمامًا.
        """
        now = datetime.now(timezone.utc)

        async with self.db_manager.session() as session:
            campaign = await session.get(BroadcastCampaign, campaign_id)
            if not campaign:
                raise ValueError(f"الحملة #{campaign_id} غير موجودة للاستبدال.")

            old_run_id = campaign.active_run_id
            if old_run_id:
                old_run = await session.get(BroadcastRun, old_run_id)
                if old_run and old_run.status in {"running", "paused"}:
                    old_run.status = "superseded"
                    old_run.completed_at = now

                await session.execute(
                    update(ScheduledTask)
                    .where(
                        ScheduledTask.run_id == old_run_id,
                        ScheduledTask.status.in_(["pending", "running"]),
                    )
                    .values(status="cancelled", updated_at=now)
                )

            if new_name is not None:
                campaign.name = new_name.strip()
            if new_text_content is not None:
                campaign.text_content = new_text_content
            if new_content_type is not None:
                campaign.content_type = new_content_type
            if new_media_ref_id is not None:
                campaign.media_ref_id = new_media_ref_id
            if new_collection_id is not None:
                campaign.collection_id = new_collection_id
            if new_excluded_group_ids is not None:
                campaign.excluded_group_ids_json = list(new_excluded_group_ids)

            campaign.updated_at = now

            session.add(
                ActivityLog(
                    category="campaign",
                    action="replace_campaign",
                    target_id=str(campaign_id),
                    details_json={
                        "superseded_run_id": old_run_id,
                    },
                )
            )

        # تحديث قائمة المستلمين إذا تغير التصنيف أو الاستثناءات
        if new_collection_id is not None or new_excluded_group_ids is not None:
            target_groups = await self.group_manager.resolve_campaign_target_groups(
                collection_id=campaign.collection_id,
                excluded_group_ids=campaign.excluded_group_ids_json,
            )
            async with self.db_manager.session() as session:
                await session.execute(
                    delete(BroadcastRecipient).where(
                        BroadcastRecipient.campaign_id == campaign_id
                    )
                )
                for grp in target_groups:
                    session.add(
                        BroadcastRecipient(
                            campaign_id=campaign_id,
                            group_id=grp.group_id,
                            is_excluded=False,
                        )
                    )

        return await self.initiate_new_run(campaign_id)
