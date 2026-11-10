import logging
from dataclasses import dataclass
from typing import List, Optional
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup

from database.database import DatabaseManager
from database.models import SystemError
from integrations.business.permissions import BusinessPermissionChecker

logger = logging.getLogger("secretary_bot.business.sender")


@dataclass
class BusinessSendResult:
    success: bool
    message_id: Optional[int] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    retry_after: Optional[int] = None
    is_retryable: bool = False


class BusinessSender:
    """
    تنفيذ الإرسال والحذف والتعديل نيابة عن حساب الأعمال المتصل (Telegram Business).
    يتحقق من الصلاحيات قبل الإرسال أو الحذف ويسجل الأخطاء بدقة.
    """

    def __init__(self, bot: Bot, db_manager: DatabaseManager) -> None:
        self.bot = bot
        self.db_manager = db_manager
        self.permission_checker = BusinessPermissionChecker(db_manager)

    async def send_reply(
        self,
        business_connection_id: str,
        chat_id: int,
        content_type: str = "text",
        text: Optional[str] = None,
        bot_file_id: Optional[str] = None,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
    ) -> BusinessSendResult:
        """إرسال رد تلقائي (نص أو وسائط أو أزرار) عبر اتصال Business."""
        summary = await self.permission_checker.get_active_connection_summary(
            business_connection_id
        )
        if not summary or not summary.is_active:
            return BusinessSendResult(
                success=False,
                error_type="ConnectionInactive",
                error_message="اتصال Telegram Business غير نشط أو غير موجود.",
                is_retryable=False,
            )
        if not summary.can_reply:
            return BusinessSendResult(
                success=False,
                error_type="PermissionDenied",
                error_message="البوت لا يملك صلاحية الرد (can_reply) في إعدادات Telegram Business.",
                is_retryable=False,
            )

        try:
            if content_type == "text":
                msg = await self.bot.send_message(
                    chat_id=chat_id,
                    text=text or "",
                    business_connection_id=business_connection_id,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_web_page_preview,
                    reply_markup=reply_markup,
                )
            elif content_type == "photo" and bot_file_id:
                msg = await self.bot.send_photo(
                    chat_id=chat_id,
                    photo=bot_file_id,
                    caption=text,
                    business_connection_id=business_connection_id,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            elif content_type == "video" and bot_file_id:
                msg = await self.bot.send_video(
                    chat_id=chat_id,
                    video=bot_file_id,
                    caption=text,
                    business_connection_id=business_connection_id,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            elif content_type == "document" and bot_file_id:
                msg = await self.bot.send_document(
                    chat_id=chat_id,
                    document=bot_file_id,
                    caption=text,
                    business_connection_id=business_connection_id,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            elif content_type == "voice" and bot_file_id:
                msg = await self.bot.send_voice(
                    chat_id=chat_id,
                    voice=bot_file_id,
                    caption=text,
                    business_connection_id=business_connection_id,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            elif content_type == "audio" and bot_file_id:
                msg = await self.bot.send_audio(
                    chat_id=chat_id,
                    audio=bot_file_id,
                    caption=text,
                    business_connection_id=business_connection_id,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            elif content_type == "sticker" and bot_file_id:
                msg = await self.bot.send_sticker(
                    chat_id=chat_id,
                    sticker=bot_file_id,
                    business_connection_id=business_connection_id,
                    reply_markup=reply_markup,
                )
            else:
                return BusinessSendResult(
                    success=False,
                    error_type="UnsupportedContentType",
                    error_message=f"نوع المحتوى غير مدعوم أو معرف الملف مفقود: {content_type}",
                    is_retryable=False,
                )

            return BusinessSendResult(success=True, message_id=msg.message_id)

        except TelegramRetryAfter as exc:
            await self._record_error("BusinessSender.send_reply", exc, is_retryable=True)
            return BusinessSendResult(
                success=False,
                error_type="FloodWait",
                error_message=str(exc),
                retry_after=exc.retry_after,
                is_retryable=True,
            )
        except TelegramAPIError as exc:
            retryable = "timeout" in str(exc).lower() or "network" in str(exc).lower()
            await self._record_error("BusinessSender.send_reply", exc, is_retryable=retryable)
            return BusinessSendResult(
                success=False,
                error_type=type(exc).__name__,
                error_message=str(exc),
                is_retryable=retryable,
            )
        except Exception as exc:
            await self._record_error("BusinessSender.send_reply", exc, is_retryable=True)
            return BusinessSendResult(
                success=False,
                error_type=type(exc).__name__,
                error_message=str(exc),
                is_retryable=True,
            )

    async def delete_managed_messages(
        self,
        business_connection_id: str,
        message_ids: List[int],
    ) -> BusinessSendResult:
        """
        حذف رسائل الرد التلقائي السابقة التي أرسلها النظام باستخدام deleteBusinessMessages.
        """
        if not message_ids:
            return BusinessSendResult(success=True)

        summary = await self.permission_checker.get_active_connection_summary(
            business_connection_id
        )
        if not summary or not summary.is_active:
            return BusinessSendResult(
                success=False,
                error_type="ConnectionInactive",
                error_message="اتصال Business غير نشط أثناء محاولة الحذف.",
                is_retryable=False,
            )

        if not (summary.can_delete_outgoing_messages or summary.can_delete_all_messages):
            return BusinessSendResult(
                success=False,
                error_type="DeletePermissionDenied",
                error_message="صلاحية حذف الرسائل الصادرة غير مفعلة في إعدادات Business.",
                is_retryable=False,
            )

        try:
            await self.bot.delete_business_messages(
                business_connection_id=business_connection_id,
                message_ids=message_ids,
            )
            return BusinessSendResult(success=True)
        except TelegramRetryAfter as exc:
            await self._record_error("BusinessSender.delete", exc, is_retryable=True)
            return BusinessSendResult(
                success=False,
                error_type="FloodWait",
                error_message=str(exc),
                retry_after=exc.retry_after,
                is_retryable=True,
            )
        except TelegramAPIError as exc:
            retryable = "message to delete not found" not in str(exc).lower()
            await self._record_error("BusinessSender.delete", exc, is_retryable=retryable)
            return BusinessSendResult(
                success=False,
                error_type=type(exc).__name__,
                error_message=str(exc),
                is_retryable=retryable,
            )

    async def _record_error(
        self, component: str, exc: Exception, is_retryable: bool
    ) -> None:
        logger.error("خطأ في %s: %s", component, exc)
        try:
            async with self.db_manager.session() as session:
                session.add(
                    SystemError(
                        component=component,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        is_retryable=is_retryable,
                        resolved=False,
                    )
                )
        except Exception:
            pass
