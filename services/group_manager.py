from typing import List, Optional, Sequence
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from database.database import DatabaseManager
from database.models import (
    ActivityLog,
    Group,
    GroupCollection,
    GroupCollectionMember,
)


class GroupManager:
    """
    إدارة المجموعات المكتشفة وتنظيمها في تصنيفات مخصصة (Collections)
    مثل: مجموعات العمل، مجموعات الترويج، مجموعات المجتمع.
    """

    def __init__(self, db_manager: DatabaseManager) -> None:
        self.db_manager = db_manager

    async def list_groups(
        self,
        search_query: Optional[str] = None,
        only_sendable: bool = False,
    ) -> List[Group]:
        """البحث في المجموعات وتصفيتها حسب إمكانية الإرسال."""
        async with self.db_manager.session() as session:
            stmt = select(Group).where(Group.is_active.is_(True))
            if only_sendable:
                stmt = stmt.where(Group.can_send_messages.is_(True))
            if search_query and search_query.strip():
                q = f"%{search_query.strip()}%"
                stmt = stmt.where(Group.title.ilike(q))
            stmt = stmt.order_by(Group.title.asc())
            return list((await session.scalars(stmt)).all())

    async def create_collection(
        self, name: str, description: Optional[str] = None
    ) -> GroupCollection:
        """إنشاء تصنيف مجموعات جديد."""
        async with self.db_manager.session() as session:
            col = GroupCollection(name=name.strip(), description=description)
            session.add(col)
            await session.flush()
            session.add(
                ActivityLog(
                    category="group",
                    action="create_collection",
                    target_id=str(col.id),
                    details_json={"name": col.name},
                )
            )
            return col

    async def rename_collection(self, collection_id: int, new_name: str) -> bool:
        """إعادة تسمية تصنيف موجود."""
        async with self.db_manager.session() as session:
            col = await session.get(GroupCollection, collection_id)
            if not col:
                return False
            col.name = new_name.strip()
            return True

    async def delete_collection(self, collection_id: int) -> bool:
        """
        حذف التصنيف من قاعدة البيانات فقط.
        لا يؤدي ذلك إلى حذف المجموعات من تيليجرام أو تغيير عضوية الحساب فيها.
        """
        async with self.db_manager.session() as session:
            col = await session.get(GroupCollection, collection_id)
            if not col:
                return False
            await session.delete(col)
            return True

    async def add_group_to_collection(
        self, collection_id: int, group_id: int
    ) -> bool:
        """إضافة مجموعة إلى تصنيف محدد مع منع التكرار."""
        async with self.db_manager.session() as session:
            existing = await session.scalar(
                select(GroupCollectionMember).where(
                    GroupCollectionMember.collection_id == collection_id,
                    GroupCollectionMember.group_id == group_id,
                )
            )
            if existing:
                return True
            session.add(
                GroupCollectionMember(
                    collection_id=collection_id,
                    group_id=group_id,
                )
            )
            return True

    async def remove_group_from_collection(
        self, collection_id: int, group_id: int
    ) -> bool:
        """إزالة مجموعة من تصنيف محدد."""
        async with self.db_manager.session() as session:
            res = await session.execute(
                delete(GroupCollectionMember).where(
                    GroupCollectionMember.collection_id == collection_id,
                    GroupCollectionMember.group_id == group_id,
                )
            )
            return (res.rowcount or 0) > 0

    async def move_group_between_collections(
        self, from_collection_id: int, to_collection_id: int, group_id: int
    ) -> bool:
        """نقل مجموعة من تصنيف إلى آخر."""
        await self.remove_group_from_collection(from_collection_id, group_id)
        return await self.add_group_to_collection(to_collection_id, group_id)

    async def list_collections_with_counts(self) -> List[tuple[GroupCollection, int]]:
        """عرض جميع التصنيفات وعدد المجموعات داخل كل تصنيف."""
        async with self.db_manager.session() as session:
            stmt = select(GroupCollection).options(
                selectinload(GroupCollection.members)
            )
            cols = list((await session.scalars(stmt)).all())
            return [(c, len(c.members)) for c in cols]

    async def resolve_campaign_target_groups(
        self,
        collection_id: Optional[int] = None,
        specific_group_ids: Optional[Sequence[int]] = None,
        excluded_group_ids: Optional[Sequence[int]] = None,
    ) -> List[Group]:
        """تحديد القائمة النهائية للمجموعات المستهدفة في الحملة بعد استبعاد المستثناة."""
        excluded_set = set(excluded_group_ids or [])

        async with self.db_manager.session() as session:
            if collection_id is not None:
                stmt = (
                    select(Group)
                    .join(
                        GroupCollectionMember,
                        GroupCollectionMember.group_id == Group.group_id,
                    )
                    .where(
                        GroupCollectionMember.collection_id == collection_id,
                        Group.is_active.is_(True),
                    )
                )
                groups = list((await session.scalars(stmt)).all())
                return [g for g in groups if g.group_id not in excluded_set]
            elif specific_group_ids:
                stmt = select(Group).where(
                    Group.group_id.in_(list(specific_group_ids)),
                    Group.is_active.is_(True),
                )
                groups_map = {g.group_id: g for g in (await session.scalars(stmt)).all()}
                return [groups_map[gid] for gid in specific_group_ids if gid in groups_map and gid not in excluded_set]
            else:
                stmt = select(Group).where(Group.is_active.is_(True))
                groups = list((await session.scalars(stmt)).all())
                return [g for g in groups if g.group_id not in excluded_set]
