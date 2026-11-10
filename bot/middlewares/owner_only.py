from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from security.access_control import AccessControl


class OwnerOnlyMiddleware(BaseMiddleware):
    """
    حارس أمني لبوت الإدارة:
    - يسمح فقط لمالك النظام (OWNER_ID) باستخدام أوامر وأزرار لوحة التحكم.
    - لا يعترض رسائل Telegram Business الواردة من العملاء (حيث business_connection_id موجود)
      لأنها تُعالج في موجه الأعمال المستقل وليس في واجهة الإدارة.
    """

    def __init__(self, access_control: AccessControl) -> None:
        super().__init__()
        self.access_control = access_control

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # إذا كانت الرسالة قادمة عبر اتصال Telegram Business، نتركها لموجه Business
        if isinstance(event, Message) and getattr(event, "business_connection_id", None):
            return await handler(event, data)

        user = getattr(event, "from_user", None)
        user_id = getattr(user, "id", None)
        username = getattr(user, "username", None)

        if not self.access_control.is_owner(user_id):
            await self.access_control.log_unauthorized_attempt(
                user_id=user_id,
                username=username,
                event_type=type(event).__name__,
            )
            if isinstance(event, CallbackQuery):
                await event.answer("⛔ غير مصرح لك باستخدام هذا النظام.", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("⛔ هذا البوت خاص ومقيد لمالك النظام فقط.")
            return None

        data["access_control"] = self.access_control
        return await handler(event, data)
