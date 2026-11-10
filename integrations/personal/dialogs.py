import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List
from sqlalchemy import select

from database.database import DatabaseManager
from database.models import ActivityLog, Group
from integrations.personal.client import PersonalClientManager

logger = logging.getLogger("secretary_bot.personal.dialogs")


@dataclass
class DiscoverySummary:
    total_discovered: int
    sendable_count: int
    restricted_count: int


class PersonalDialogsInspector:
    """
    اكتشاف المجموعات التي يكون الحساب الشخصي عضوًا فيها عبر MTProto،
    وفحص صلاحيات الإرسال الفعلية (النص، الوسائط، الأزرار، والـ Slow Mode).
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        client_manager: PersonalClientManager,
    ) -> None:
        self.db_manager = db_manager
        self.client_manager = client_manager

    async def discover_and_sync_groups(self) -> DiscoverySummary:
        """جلب حوارات الحساب الشخصي وتحديث جدول groups دون حذف المجموعات عند الأخطاء المؤقتة."""
        client = await self.client_manager.get_authorized_client()
        if client is None:
            raise RuntimeError("جلسة الحساب الشخصي غير مفوضة حاليًا.")

        dialogs = await client.get_dialogs()
        now = datetime.now(timezone.utc)
        total_discovered = 0
        sendable_count = 0
        restricted_count = 0

        async with self.db_manager.session() as session:
            for d in dialogs:
                if not (getattr(d, "is_group", False)):
                    continue

                entity = d.entity
                group_id = int(getattr(entity, "id", d.id))
                access_hash = getattr(entity, "access_hash", None)
                title = getattr(d, "title", None) or getattr(entity, "title", f"Group {group_id}")
                username = getattr(entity, "username", None)
                is_megagroup = bool(getattr(entity, "megagroup", False))
                group_type = "supergroup" if is_megagroup else "group"
                member_count = getattr(entity, "participants_count", None)
                slow_mode = int(getattr(entity, "slowmode_seconds", 0) or 0)

                can_send_msg, can_send_media, can_send_inline, perm_status = (
                    self._evaluate_entity_permissions(entity)
                )

                total_discovered += 1
                if can_send_msg:
                    sendable_count += 1
                else:
                    restricted_count += 1

                existing = await session.scalar(
                    select(Group).where(Group.group_id == group_id)
                )
                if existing:
                    existing.access_hash = access_hash
                    existing.title = title
                    existing.username = username
                    existing.group_type = group_type
                    existing.member_count = member_count
                    existing.can_send_messages = can_send_msg
                    existing.can_send_media = can_send_media
                    existing.can_send_inline = can_send_inline
                    existing.slow_mode_seconds = slow_mode
                    existing.permission_status = perm_status
                    existing.is_active = True
                    existing.last_checked_at = now
                else:
                    session.add(
                        Group(
                            group_id=group_id,
                            access_hash=access_hash,
                            title=title,
                            username=username,
                            group_type=group_type,
                            member_count=member_count,
                            can_send_messages=can_send_msg,
                            can_send_media=can_send_media,
                            can_send_inline=can_send_inline,
                            slow_mode_seconds=slow_mode,
                            permission_status=perm_status,
                            is_active=True,
                            last_checked_at=now,
                        )
                    )

            session.add(
                ActivityLog(
                    category="group",
                    action="groups_discovered",
                    details_json={
                        "total": total_discovered,
                        "sendable": sendable_count,
                        "restricted": restricted_count,
                    },
                )
            )

        return DiscoverySummary(
            total_discovered=total_discovered,
            sendable_count=sendable_count,
            restricted_count=restricted_count,
        )

    @staticmethod
    def _evaluate_entity_permissions(entity: object) -> tuple[bool, bool, bool, str]:
        """فحص حقوق النشر في المجموعة من كائن Telethon Entity."""
        if getattr(entity, "left", False) or getattr(entity, "kicked", False):
            return False, False, False, "forbidden"

        if getattr(entity, "creator", False) or getattr(entity, "admin_rights", None):
            return True, True, True, "verified"

        banned_rights = getattr(entity, "banned_rights", None)
        default_banned = getattr(entity, "default_banned_rights", None)

        def _is_banned(rights_obj: object, attr: str) -> bool:
            return bool(getattr(rights_obj, attr, False)) if rights_obj else False

        send_messages_banned = _is_banned(banned_rights, "send_messages") or _is_banned(
            default_banned, "send_messages"
        )
        send_media_banned = _is_banned(banned_rights, "send_media") or _is_banned(
            default_banned, "send_media"
        )
        send_inline_banned = _is_banned(banned_rights, "send_inline") or _is_banned(
            default_banned, "send_inline"
        )

        if send_messages_banned:
            return False, False, False, "restricted"

        return (
            True,
            not send_media_banned,
            not send_inline_banned,
            "verified",
        )
