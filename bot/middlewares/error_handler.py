import logging
import traceback
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from database.database import DatabaseManager
from database.models import SystemError

logger = logging.getLogger("secretary_bot.errors")


class ErrorHandlerMiddleware(BaseMiddleware):
    """التقاط الاستثناءات غير المتوقعة، تسجيلها في قاعدة البيانات، وإشعار المالك بأمان."""

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
        except Exception as exc:
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
                            is_retryable=False,
                            resolved=False,
                        )
                    )
            except Exception as db_exc:
                logger.error("فشل حفظ الخطأ في جدول system_errors: %s", db_exc)

            if isinstance(event, CallbackQuery):
                try:
                    await event.answer(
                        "⚠️ حدث خطأ داخلي وتم تسجيله في سجل الأخطاء.",
                        show_alert=True,
                    )
                except Exception:
                    pass
            elif isinstance(event, Message) and not getattr(event, "business_connection_id", None):
                try:
                    await event.answer(
                        "⚠️ حدث خطأ غير متوقع أثناء تنفيذ العملية. تم تسجيل التفاصيل في قسم «سجل النشاط والأخطاء»."
                    )
                except Exception:
                    pass
            return None
