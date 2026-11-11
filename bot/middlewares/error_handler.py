import logging
import traceback
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.types import CallbackQuery, Message, TelegramObject
from database.database import DatabaseManager
from database.models import SystemError

logger = logging.getLogger("secretary_bot.errors")


class ErrorHandlerMiddleware(BaseMiddleware):
    """التقاط الاستثناءات، وتجاهل خطأ عدم تغير النص، والتعامل السليم مع انقطاعات الشبكة."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        super().__init__()
        self.db_manager = db_manager

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)

        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                if isinstance(event, CallbackQuery):
                    try:
                        await event.answer("البيانات محدثة بالفعل ✅")
                    except Exception:
                        pass
                return None

            await self._log_system_error(exc, is_retryable=False)
            await self._notify_user_on_error(event, "⚠️ تعذر تنفيذ الطلب في تيليجرام.")
            return None

        except TelegramNetworkError as exc:
            logger.warning("بطء أو انقطاع شبكة مؤقت مع خوادم تيليجرام: %s", exc)
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer(
                        "🌐 بطء مؤقت في الاتصال بخوادم تيليجرام، يرجى المحاولة مرة أخرى.",
                        show_alert=False,
                    )
                except Exception:
                    pass
            return None

        except Exception as exc:
            await self._log_system_error(exc, is_retryable=False)
            await self._notify_user_on_error(
                event,
                "⚠️ حدث خطأ غير متوقع وتم تسجيله في قسم «سجل النشاط والأخطاء».",
            )
            return None

    async def _log_system_error(self, exc: Exception, is_retryable: bool) -> None:
        tb_text = traceback.format_exc()
        logger.error("خطأ غير متوقع أثناء معالجة الحدث: %s\n%s", exc, tb_text)
        try:
            async with self.db_manager.session() as session:
                session.add(
                    SystemError(
                        component="bot_handler",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        traceback_text=tb_text[:4000],
                        is_retryable=is_retryable,
                        resolved=False,
                    )
                )
        except Exception as db_exc:
            logger.error("فشل حفظ الخطأ في جدول system_errors: %s", db_exc)

    @staticmethod
    async def _notify_user_on_error(event: TelegramObject, text: str) -> None:
        if isinstance(event, CallbackQuery):
            try:
                await event.answer(text, show_alert=True)
            except Exception:
                pass
        elif isinstance(event, Message) and not getattr(event, "business_connection_id", None):
            try:
                await event.answer(text)
            except Exception:
                pass
