from dataclasses import dataclass
from typing import Any, Dict, Optional
from sqlalchemy import select
from database.database import DatabaseManager
from database.models import BusinessConnection


@dataclass
class BusinessPermissionsSummary:
    connection_id: str
    is_active: bool
    can_reply: bool
    can_read_messages: bool
    can_delete_outgoing_messages: bool
    can_delete_all_messages: bool
    can_edit_name: bool
    can_pin_messages: bool

    @property
    def is_ready_for_secretary(self) -> bool:
        """هل يمتلك البوت الحد الأدنى للعمل كسكرتير (الرد + حذف ردوده السابقة)؟"""
        return (
            self.is_active
            and self.can_reply
            and (self.can_delete_outgoing_messages or self.can_delete_all_messages)
        )


class BusinessPermissionChecker:
    """التحقق من صلاحيات اتصال Telegram Business المحفوظة والفعلية."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self.db_manager = db_manager

    @staticmethod
    def extract_rights_dict(connection_event: Any) -> Dict[str, bool]:
        """استخراج قاموس الصلاحيات من حدث BusinessConnection الخاص بـ Bot API."""
        rights = getattr(connection_event, "rights", None)
        can_reply_top = bool(getattr(connection_event, "can_reply", False))

        if rights is None:
            return {
                "can_reply": can_reply_top,
                "can_read_messages": can_reply_top,
                "can_delete_outgoing_messages": can_reply_top,
                "can_delete_all_messages": False,
                "can_edit_name": False,
                "can_pin_messages": False,
            }

        return {
            "can_reply": bool(getattr(rights, "can_reply", can_reply_top)),
            "can_read_messages": bool(getattr(rights, "can_read_messages", True)),
            "can_delete_outgoing_messages": bool(
                getattr(rights, "can_delete_outgoing_messages", False)
                or getattr(rights, "can_delete_sent_messages", False)
            ),
            "can_delete_all_messages": bool(
                getattr(rights, "can_delete_all_messages", False)
            ),
            "can_edit_name": bool(getattr(rights, "can_edit_name", False)),
            "can_pin_messages": bool(getattr(rights, "can_pin_messages", False)),
        }

    async def get_active_connection_summary(
        self, connection_id: Optional[str] = None
    ) -> Optional[BusinessPermissionsSummary]:
        """جلب ملخص صلاحيات اتصال Business النشط (أو اتصال محدد بمعرفه)."""
        async with self.db_manager.session() as session:
            if connection_id:
                conn = await session.scalar(
                    select(BusinessConnection).where(
                        BusinessConnection.connection_id == connection_id
                    )
                )
            else:
                conn = await session.scalar(
                    select(BusinessConnection)
                    .where(BusinessConnection.is_active.is_(True))
                    .order_by(BusinessConnection.updated_at.desc())
                )

            if not conn:
                return None

            rights = conn.rights_json or {}
            return BusinessPermissionsSummary(
                connection_id=conn.connection_id,
                is_active=conn.is_active,
                can_reply=bool(conn.can_reply or rights.get("can_reply", False)),
                can_read_messages=bool(rights.get("can_read_messages", True)),
                can_delete_outgoing_messages=bool(
                    rights.get("can_delete_outgoing_messages", False)
                ),
                can_delete_all_messages=bool(
                    rights.get("can_delete_all_messages", False)
                ),
                can_edit_name=bool(rights.get("can_edit_name", False)),
                can_pin_messages=bool(rights.get("can_pin_messages", False)),
            )
