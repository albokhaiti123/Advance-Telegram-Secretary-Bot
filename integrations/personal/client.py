import asyncio
import logging
from typing import Optional
from sqlalchemy import select
from telethon import TelegramClient
from telethon.sessions import StringSession

from database.database import DatabaseManager
from database.models import PersonalAccountSession
from security.secrets import SecretManager

logger = logging.getLogger("secretary_bot.personal.client")


class PersonalClientManager:
    """
    إدارة جلسة عميل الحساب الشخصي (Telethon MTProto) بمعزل تام عن بوت الإدارة.
    يضمن تشغيل اتصال واحد فقط (Singleton Lock) ويفك تشفير الجلسة من قاعدة البيانات عند الحاجة.
    """

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        db_manager: DatabaseManager,
        secret_manager: SecretManager,
    ) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.db_manager = db_manager
        self.secret_manager = secret_manager
        self._client: Optional[TelegramClient] = None
        self._lock = asyncio.Lock()

    async def get_authorized_client(self) -> Optional[TelegramClient]:
        """إرجاع عميل Telethon متصل ومفوض، أو None إذا لم تكن هناك جلسة صالحة."""
        async with self._lock:
            if self._client is not None and self._client.is_connected():
                if await self._client.is_user_authorized():
                    return self._client

            async with self.db_manager.session() as session:
                session_rec = await session.scalar(
                    select(PersonalAccountSession)
                    .where(PersonalAccountSession.is_authorized.is_(True))
                    .order_by(PersonalAccountSession.updated_at.desc())
                )
                if not session_rec or not session_rec.encrypted_session:
                    return None

                raw_session_str = self.secret_manager.decrypt_session(
                    session_rec.encrypted_session
                )

            client = TelegramClient(
                StringSession(raw_session_str),
                self.api_id,
                self.api_hash,
                device_model="SecretaryBot Ubuntu VPS",
                system_version="Linux 6.8",
                app_version="1.0.0",
            )
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                await self._mark_session_unauthorized("انتهت صلاحية الجلسة من خوادم تيليجرام")
                return None

            self._client = client
            return self._client

    async def disconnect_safely(self) -> None:
        """فصل اتصال العميل عند إيقاف النظام دون إبطال الجلسة."""
        async with self._lock:
            if self._client is not None:
                try:
                    await self._client.disconnect()
                except Exception as exc:
                    logger.warning("خطأ طفيف أثناء فصل عميل Telethon: %s", exc)
                finally:
                    self._client = None

    async def _mark_session_unauthorized(self, reason: str) -> None:
        async with self.db_manager.session() as session:
            records = list(
                (
                    await session.scalars(
                        select(PersonalAccountSession).where(
                            PersonalAccountSession.is_authorized.is_(True)
                        )
                    )
                ).all()
            )
            for r in records:
                r.is_authorized = False
                r.status_message = reason
