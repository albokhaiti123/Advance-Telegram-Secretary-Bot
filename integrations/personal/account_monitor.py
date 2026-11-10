from datetime import datetime, timezone
from typing import Dict, Optional
from sqlalchemy import select

from database.database import DatabaseManager
from database.models import Notification, PersonalAccountSession
from integrations.personal.client import PersonalClientManager


class PersonalAccountMonitor:
    """فحص صلاحية جلسة الحساب الشخصي واكتشاف انتهاء الجلسة وإشعار المالك."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        client_manager: PersonalClientManager,
    ) -> None:
        self.db_manager = db_manager
        self.client_manager = client_manager

    async def verify_session_health(self) -> Dict[str, object]:
        """التحقق الفعلي من حالة الجلسة عبر استدعاء get_me() وتحديث قاعدة البيانات."""
        async with self.db_manager.session() as session:
            rec = await session.scalar(
                select(PersonalAccountSession).order_by(
                    PersonalAccountSession.updated_at.desc()
                )
            )
            if not rec:
                return {
                    "is_authorized": False,
                    "phone_masked": "غير مربوط",
                    "status_message": "لم يتم إعداد جلسة للحساب الشخصي بعد.",
                }
            phone_masked = rec.phone_masked
            rec_id = rec.id

        client = await self.client_manager.get_authorized_client()
        now = datetime.now(timezone.utc)

        if client is None:
            await self._record_session_expired(rec_id)
            return {
                "is_authorized": False,
                "phone_masked": phone_masked,
                "status_message": "الجلسة منتهية أو غير صالحة؛ يلزم إعادة التفويض.",
            }

        try:
            me = await client.get_me()
            if me is None:
                await self._record_session_expired(rec_id)
                return {
                    "is_authorized": False,
                    "phone_masked": phone_masked,
                    "status_message": "تعذر قراءة بيانات الحساب الشخصي.",
                }

            async with self.db_manager.session() as session:
                db_rec = await session.get(PersonalAccountSession, rec_id)
                if db_rec:
                    db_rec.is_authorized = True
                    db_rec.telegram_user_id = me.id
                    db_rec.username = getattr(me, "username", None)
                    db_rec.last_verified_at = now
                    db_rec.status_message = "نشط ومفوض"

            return {
                "is_authorized": True,
                "phone_masked": phone_masked,
                "telegram_user_id": me.id,
                "username": getattr(me, "username", None),
                "status_message": "نشط ومفوض",
                "last_verified_at": now.strftime("%Y-%m-%d %H:%M UTC"),
            }
        except Exception as exc:
            await self._record_session_expired(rec_id, str(exc))
            return {
                "is_authorized": False,
                "phone_masked": phone_masked,
                "status_message": f"خطأ في الاتصال: {type(exc).__name__}",
            }

    async def _record_session_expired(
        self, session_id: int, error_detail: Optional[str] = None
    ) -> None:
        async with self.db_manager.session() as session:
            rec = await session.get(PersonalAccountSession, session_id)
            if rec:
                rec.is_authorized = False
                rec.status_message = error_detail or "انتهت صلاحية الجلسة"
            session.add(
                Notification(
                    level="critical",
                    category="personal_session",
                    title="انتهاء جلسة الحساب الشخصي",
                    message="فقد النظام الاتصال بجلسة الحساب الشخصي (MTProto). يرجى إعادة التفويض من قسم إعدادات الحساب.",
                    is_sent_to_owner=False,
                )
            )
