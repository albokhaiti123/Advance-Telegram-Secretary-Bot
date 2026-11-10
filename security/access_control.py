import logging
from typing import Any, Dict, Optional
from database.database import DatabaseManager
from database.models import ActivityLog

logger = logging.getLogger("secretary_bot.security")


class AccessControl:
    """التحقق من هوية المالك وتسجيل العمليات الحساسة ومحاولات الاختراق."""

    def __init__(self, owner_id: int, db_manager: DatabaseManager) -> None:
        self.owner_id = owner_id
        self.db_manager = db_manager

    def is_owner(self, user_id: Optional[int]) -> bool:
        """التحقق مما إذا كان المعرف يطابق مالك النظام حصريًا."""
        if user_id is None:
            return False
        return int(user_id) == int(self.owner_id)

    async def log_admin_action(
        self,
        action: str,
        target_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """تسجيل عملية إدارية شرعية نفذها المالك."""
        try:
            async with self.db_manager.session() as session:
                session.add(
                    ActivityLog(
                        category="admin",
                        action=action,
                        actor_id=self.owner_id,
                        target_id=target_id,
                        details_json=details or {},
                    )
                )
        except Exception as exc:
            logger.error("فشل تسجيل العملية الإدارية في قاعدة البيانات: %s", exc)

    async def log_unauthorized_attempt(
        self,
        user_id: Optional[int],
        username: Optional[str],
        event_type: str,
    ) -> None:
        """تسجيل محاولة وصول غير مصرح بها إلى بوت الإدارة."""
        logger.warning(
            "محاولة وصول مرفوضة لبوت الإدارة | user_id=%s | username=%s | type=%s",
            user_id,
            username,
            event_type,
        )
        try:
            async with self.db_manager.session() as session:
                session.add(
                    ActivityLog(
                        category="security",
                        action="unauthorized_access_blocked",
                        actor_id=user_id,
                        target_id=event_type,
                        details_json={"username": username or "unknown"},
                    )
                )
        except Exception as exc:
            logger.error("فشل تسجيل محاولة الوصول المرفوضة: %s", exc)
