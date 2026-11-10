import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select

from database.database import DatabaseManager
from database.models import (
    BroadcastCampaign,
    BroadcastDeliveryLog,
    BroadcastRecipient,
    BroadcastRun,
    Group,
    MediaReference,
    Notification,
    ReplyButton,
)
from integrations.personal.group_sender import PersonalGroupSender

logger = logging.getLogger("secretary_bot.services.broadcast")


class BroadcastManager:
    """
    تنفيذ حملات البث إلى المجموعات مع التحقق الصارم قبل كل مجموعة من أن:
    1. الحملة لم يتم استبدالها (campaign.active_run_id == run_id).
    2. حالة التشغيل ما زالت 'running' (ليست متوقفة أو ملغاة أو مستبدلة).
    3. احترام FloodWait وتطبيق فواصل زمنية محافظة (Jitter Delay).
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        group_sender: PersonalGroupSender,
        min_delay_seconds: float = 5.0,
        max_delay_seconds: float = 12.0,
        max_retries_per_group: int = 2,
    ) -> None:
        self.db_manager = db_manager
        self.group_sender = group_sender
        self.min_delay_seconds = min_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.max_retries_per_group = max_retries_per_group

    async def execute_campaign_run(self, campaign_id: int, run_id: str) -> str:
        """
        تنفيذ دورة بث كاملة مرتبطة بـ run_id محدد.
        يرجع الحالة النهائية للتنفيذ (completed, partial, paused, cancelled, superseded, failed).
        """
        # جلب بيانات الحملة والمجموعات المستهدفة التي لم يُرسل إليها بعد في هذا الـ run_id
        async with self.db_manager.session() as session:
            campaign = await session.get(BroadcastCampaign, campaign_id)
            run_rec = await session.get(BroadcastRun, run_id)
            if not campaign or not run_rec:
                return "failed"

            if campaign.active_run_id != run_id or run_rec.status != "running":
                return run_rec.status

            local_file_path: Optional[str] = None
            if campaign.media_ref_id:
                media_ref = await session.get(MediaReference, campaign.media_ref_id)
                if media_ref:
                    local_file_path = media_ref.local_file_path

            buttons = list(
                (
                    await session.scalars(
                        select(ReplyButton).where(
                            ReplyButton.campaign_id == campaign_id
                        )
                    )
                ).all()
            )

            recipients = list(
                (
                    await session.scalars(
                        select(BroadcastRecipient)
                        .where(
                            BroadcastRecipient.campaign_id == campaign_id,
                            BroadcastRecipient.is_excluded.is_(False),
                        )
                        .order_by(BroadcastRecipient.id.asc())
                    )
                ).all()
            )
            already_processed_group_ids = set(
                (
                    await session.scalars(
                        select(BroadcastDeliveryLog.group_id).where(
                            BroadcastDeliveryLog.run_id == run_id
                        )
                    )
                ).all()
            )

            target_group_ids = [
                r.group_id
                for r in recipients
                if r.group_id not in already_processed_group_ids
            ]

            content_type = campaign.content_type
            text_content = campaign.text_content
            parse_mode = campaign.parse_mode

        for idx, group_id in enumerate(target_group_ids):
            # الفحص الذري الحاسم قبل إرسال كل مجموعة: هل ما زال هذا الـ run_id صالحًا ونشطًا؟
            if not await self._is_run_still_active(campaign_id, run_id):
                logger.info(
                    "توقف تنفيذ الحملة %s (run_id=%s) فورًا بسبب إيقافها أو استبدالها.",
                    campaign_id,
                    run_id,
                )
                return await self._get_current_run_status(run_id)

            # التحقق من صلاحية المجموعة المسبقة في قاعدة البيانات
            can_send = await self._check_group_eligible_in_db(group_id)
            if not can_send:
                await self._record_delivery_outcome(
                    run_id=run_id,
                    campaign_id=campaign_id,
                    group_id=group_id,
                    status="skipped",
                    error_type="PreCheckRestricted",
                    error_message="المجموعة مقيدة أو لا تسمح بالنشر.",
                    retry_count=0,
                )
                continue

            # محاولة الإرسال مع إعادة المحاولة للأخطاء المؤقتة وFloodWait
            attempt = 0
            while True:
                attempt += 1
                result = await self.group_sender.send_to_group(
                    group_id=group_id,
                    content_type=content_type,
                    text=text_content,
                    local_file_path=local_file_path,
                    buttons=buttons,
                    parse_mode=parse_mode,
                )

                if result.status == "sent":
                    await self._record_delivery_outcome(
                        run_id=run_id,
                        campaign_id=campaign_id,
                        group_id=group_id,
                        status="sent",
                        telegram_message_id=result.telegram_message_id,
                        retry_count=attempt - 1,
                    )
                    break

                if result.error_type == "FloodWait" and result.retry_after > 0:
                    # احترام FloodWait بالكامل ثم التحقق مجددًا من صلاحية الـ run_id
                    await asyncio.sleep(result.retry_after + 1.0)
                    if not await self._is_run_still_active(campaign_id, run_id):
                        return await self._get_current_run_status(run_id)
                    if attempt <= self.max_retries_per_group:
                        continue

                if result.is_retryable and attempt <= self.max_retries_per_group:
                    await asyncio.sleep(2.0 * attempt)
                    continue

                # فشل نهائي أو تخطي (Skipped)
                await self._record_delivery_outcome(
                    run_id=run_id,
                    campaign_id=campaign_id,
                    group_id=group_id,
                    status=result.status,
                    error_type=result.error_type,
                    error_message=result.error_message,
                    retry_count=attempt - 1,
                )
                break

            # فاصل زمني محافظ بين المجموعات لمنع الحظر (إلا بعد آخر مجموعة)
            if idx < len(target_group_ids) - 1 and self.max_delay_seconds > 0:
                delay = random.uniform(self.min_delay_seconds, self.max_delay_seconds)
                await asyncio.sleep(delay)

        return await self._finalize_run(campaign_id, run_id)

    async def _is_run_still_active(self, campaign_id: int, run_id: str) -> bool:
        """التحقق من قاعدة البيانات أن الحملة لم تُستبدل أو تُوقف."""
        async with self.db_manager.session() as session:
            campaign = await session.get(BroadcastCampaign, campaign_id)
            run_rec = await session.get(BroadcastRun, run_id)
            if not campaign or not run_rec:
                return False
            return (
                campaign.active_run_id == run_id
                and campaign.status == "running"
                and run_rec.status == "running"
            )

    async def _get_current_run_status(self, run_id: str) -> str:
        async with self.db_manager.session() as session:
            run_rec = await session.get(BroadcastRun, run_id)
            return run_rec.status if run_rec else "cancelled"

    async def _check_group_eligible_in_db(self, group_id: int) -> bool:
        async with self.db_manager.session() as session:
            grp = await session.get(Group, group_id)
            if not grp or not grp.is_active:
                return False
            return bool(grp.can_send_messages)

    async def _record_delivery_outcome(
        self,
        run_id: str,
        campaign_id: int,
        group_id: int,
        status: str,
        telegram_message_id: Optional[int] = None,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        retry_count: int = 0,
    ) -> None:
        async with self.db_manager.session() as session:
            session.add(
                BroadcastDeliveryLog(
                    run_id=run_id,
                    campaign_id=campaign_id,
                    group_id=group_id,
                    status=status,
                    telegram_message_id=telegram_message_id,
                    error_type=error_type,
                    error_message=error_message,
                    retry_count=retry_count,
                )
            )
            run_rec = await session.get(BroadcastRun, run_id)
            if run_rec:
                if status == "sent":
                    run_rec.sent_count = (run_rec.sent_count or 0) + 1
                elif status == "skipped":
                    run_rec.skipped_count = (run_rec.skipped_count or 0) + 1
                else:
                    run_rec.failed_count = (run_rec.failed_count or 0) + 1

    async def _finalize_run(self, campaign_id: int, run_id: str) -> str:
        now = datetime.now(timezone.utc)
        async with self.db_manager.session() as session:
            campaign = await session.get(BroadcastCampaign, campaign_id)
            run_rec = await session.get(BroadcastRun, run_id)
            if not campaign or not run_rec:
                return "failed"

            if campaign.active_run_id != run_id:
                run_rec.status = "superseded"
                return "superseded"

            if run_rec.status in {"paused", "cancelled", "superseded"}:
                return run_rec.status

            if run_rec.sent_count > 0 and run_rec.failed_count == 0:
                final_status = "completed"
            elif run_rec.sent_count > 0 and run_rec.failed_count > 0:
                final_status = "partial"
            elif run_rec.sent_count == 0 and run_rec.skipped_count > 0 and run_rec.failed_count == 0:
                final_status = "completed"
            else:
                final_status = "failed"

            run_rec.status = final_status
            run_rec.completed_at = now

            # إذا كانت الحملة غير متكررة نحدّث حالتها إلى النهائية، أما المتكررة فتبقى scheduled للجولة القادمة
            if campaign.schedule_type in {"immediate", "once"}:
                campaign.status = final_status
            else:
                campaign.status = "scheduled"

            session.add(
                Notification(
                    level="info" if final_status == "completed" else "warning",
                    category="campaign",
                    title=f"نتيجة الحملة: {campaign.name}",
                    message=(
                        f"اكتمل تنفيذ الحملة ({final_status}) | "
                        f"نجح: {run_rec.sent_count} | فشل: {run_rec.failed_count} | تخطي: {run_rec.skipped_count}"
                    ),
                    is_sent_to_owner=False,
                )
            )
            return final_status
