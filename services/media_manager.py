import logging
from pathlib import Path
from typing import Optional
from aiogram import Bot
from aiogram.types import Message
from sqlalchemy import select

from database.database import DatabaseManager
from database.models import MediaReference

logger = logging.getLogger("secretary_bot.services.media")


class MediaManager:
    """
    إدارة مراجع الوسائط (الصور، الفيديو، المستندات، الصوتيات، الملصقات).
    يحفظ bot_file_id لردود Business، ويخزن نسخة محلية في media_cache_dir
    ليستخدمها عميل الحساب الشخصي (Telethon) عند بث الحملات للمجموعات.
    """

    def __init__(self, db_manager: DatabaseManager, media_cache_dir: Path) -> None:
        self.db_manager = db_manager
        self.media_cache_dir = media_cache_dir
        self.media_cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def extract_media_info_from_message(message: Message) -> Optional[dict]:
        """استخراج بيانات الوسيط من رسالة تيليجرام إن وجدت."""
        if message.photo:
            largest = message.photo[-1]
            return {
                "media_type": "photo",
                "file_unique_id": largest.file_unique_id,
                "bot_file_id": largest.file_id,
                "file_name": f"photo_{largest.file_unique_id}.jpg",
                "mime_type": "image/jpeg",
                "file_size": largest.file_size,
            }
        if message.video:
            v = message.video
            return {
                "media_type": "video",
                "file_unique_id": v.file_unique_id,
                "bot_file_id": v.file_id,
                "file_name": v.file_name or f"video_{v.file_unique_id}.mp4",
                "mime_type": v.mime_type or "video/mp4",
                "file_size": v.file_size,
            }
        if message.document:
            d = message.document
            return {
                "media_type": "document",
                "file_unique_id": d.file_unique_id,
                "bot_file_id": d.file_id,
                "file_name": d.file_name or f"doc_{d.file_unique_id}",
                "mime_type": d.mime_type,
                "file_size": d.file_size,
            }
        if message.voice:
            vc = message.voice
            return {
                "media_type": "voice",
                "file_unique_id": vc.file_unique_id,
                "bot_file_id": vc.file_id,
                "file_name": f"voice_{vc.file_unique_id}.ogg",
                "mime_type": vc.mime_type or "audio/ogg",
                "file_size": vc.file_size,
            }
        if message.audio:
            au = message.audio
            return {
                "media_type": "audio",
                "file_unique_id": au.file_unique_id,
                "bot_file_id": au.file_id,
                "file_name": au.file_name or f"audio_{au.file_unique_id}.mp3",
                "mime_type": au.mime_type or "audio/mpeg",
                "file_size": au.file_size,
            }
        if message.sticker:
            st = message.sticker
            return {
                "media_type": "sticker",
                "file_unique_id": st.file_unique_id,
                "bot_file_id": st.file_id,
                "file_name": f"sticker_{st.file_unique_id}.webp",
                "mime_type": "image/webp",
                "file_size": st.file_size,
            }
        return None

    async def register_media(
        self,
        media_type: str,
        file_unique_id: str,
        bot_file_id: str,
        file_name: Optional[str] = None,
        mime_type: Optional[str] = None,
        file_size: Optional[int] = None,
        bot: Optional[Bot] = None,
        download_for_mtproto: bool = True,
    ) -> MediaReference:
        """حفظ مرجع الوسيط في قاعدة البيانات مع تنزيل نسخة محلية اختيارية لعميل MTProto."""
        local_path_str: Optional[str] = None

        if bot is not None and download_for_mtproto:
            try:
                safe_name = f"{file_unique_id}_{file_name or 'media.bin'}"
                dest_path = self.media_cache_dir / safe_name
                if not dest_path.exists():
                    tg_file = await bot.get_file(bot_file_id)
                    if tg_file.file_path:
                        await bot.download_file(tg_file.file_path, destination=dest_path)
                if dest_path.exists():
                    local_path_str = str(dest_path.resolve())
            except Exception as exc:
                logger.warning("تعذر تنزيل نسخة محلية للوسيط %s: %s", file_unique_id, exc)

        async with self.db_manager.session() as session:
            existing = await session.scalar(
                select(MediaReference).where(
                    MediaReference.file_unique_id == file_unique_id
                )
            )
            if existing:
                existing.bot_file_id = bot_file_id
                if local_path_str:
                    existing.local_file_path = local_path_str
                return existing

            media_ref = MediaReference(
                file_unique_id=file_unique_id,
                media_type=media_type,
                bot_file_id=bot_file_id,
                local_file_path=local_path_str,
                file_name=file_name,
                mime_type=mime_type,
                file_size=file_size,
            )
            session.add(media_ref)
            await session.flush()
            return media_ref
