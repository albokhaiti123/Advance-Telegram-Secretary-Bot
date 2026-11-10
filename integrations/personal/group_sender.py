import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from telethon.errors import (
    ChannelPrivateError,
    ChatAdminRequiredError,
    ChatSendInlineForbiddenError,
    ChatSendMediaForbiddenError,
    ChatWriteForbiddenError,
    FloodWaitError,
    SlowModeWaitError,
    UserBannedInChannelError,
)

from database.database import DatabaseManager
from database.models import Group, ReplyButton
from integrations.personal.client import PersonalClientManager

logger = logging.getLogger("secretary_bot.personal.group_sender")


@dataclass
class GroupSendAttemptResult:
    status: str  # sent | failed | skipped
    telegram_message_id: Optional[int] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    retry_after: int = 0
    is_retryable: bool = False


class PersonalGroupSender:
    """
    إرسال رسائل الحملات إلى المجموعات بهوية الحساب الشخصي وصورته عبر Telethon.
    يميز بدقة بين منع النشر الدائم، عدم كفاية الصلاحيات، أخطاء الشبكة المؤقتة، وFloodWait.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        client_manager: PersonalClientManager,
    ) -> None:
        self.db_manager = db_manager
        self.client_manager = client_manager

    @staticmethod
    def append_cta_buttons_to_text(
        base_text: Optional[str],
        buttons: Optional[List[ReplyButton]],
    ) -> str:
        """
        تحويل الأزرار المرتبطة بالحملة إلى روابط تفاعلية منسقة في ذيل الرسالة
        لضمان ظهور الرسالة باسم وصورة الحساب الشخصي في جميع المجموعات حتى التي تمنع الـ Inline Bots.
        """
        text = (base_text or "").strip()
        if not buttons:
            return text

        url_Buttons = [b for b in buttons if b.button_type == "url" and b.url]
        if not url_Buttons:
            return text

        links_lines = [f"🔗 <a href=\"{b.url}\">{b.text}</a>" for b in url_Buttons]
        footer = "\n\n" + "\n".join(links_lines)
        return f"{text}{footer}".strip()

    async def send_to_group(
        self,
        group_id: int,
        content_type: str = "text",
        text: Optional[str] = None,
        local_file_path: Optional[str] = None,
        buttons: Optional[List[ReplyButton]] = None,
        parse_mode: str = "html",
    ) -> GroupSendAttemptResult:
        """إرسال رسالة واحدة إلى مجموعة محددة بهوية الحساب الشخصي."""
        client = await self.client_manager.get_authorized_client()
        if client is None:
            return GroupSendAttemptResult(
                status="failed",
                error_type="SessionNotAuthorized",
                error_message="جلسة الحساب الشخصي غير متصلة أو غير مفوضة.",
                is_retryable=False,
            )

        final_text = self.append_cta_buttons_to_text(text, buttons)
        tl_parse_mode = "html" if parse_mode.lower() == "html" else "md"

        try:
            if content_type == "text" or not local_file_path:
                sent_msg = await client.send_message(
                    entity=group_id,
                    message=final_text,
                    parse_mode=tl_parse_mode,
                    link_preview=False,
                )
            else:
                file_path = Path(local_file_path)
                if not file_path.exists():
                    return GroupSendAttemptResult(
                        status="failed",
                        error_type="MediaFileMissing",
                        error_message=f"ملف الوسيط المحلي غير موجود: {local_file_path}",
                        is_retryable=False,
                    )
                is_voice = content_type == "voice"
                sent_msg = await client.send_file(
                    entity=group_id,
                    file=str(file_path),
                    caption=final_text,
                    parse_mode=tl_parse_mode,
                    voice_note=is_voice,
                )

            return GroupSendAttemptResult(
                status="sent",
                telegram_message_id=getattr(sent_msg, "id", None),
            )

        except FloodWaitError as exc:
            wait_sec = int(getattr(exc, "seconds", 30) or 30)
            logger.warning("FloodWait أثناء الإرسال للمجموعة %s: انتظار %s ثانية", group_id, wait_sec)
            return GroupSendAttemptResult(
                status="failed",
                error_type="FloodWait",
                error_message=f"FloodWait for {wait_sec}s",
                retry_after=wait_sec,
                is_retryable=True,
            )

        except SlowModeWaitError as exc:
            wait_sec = int(getattr(exc, "seconds", 15) or 15)
            return GroupSendAttemptResult(
                status="skipped",
                error_type="SlowModeWait",
                error_message=f"الوضع البطيء مفعل في المجموعة ({wait_sec} ثانية)",
                retry_after=wait_sec,
                is_retryable=False,
            )

        except (
            ChatWriteForbiddenError,
            UserBannedInChannelError,
            ChatAdminRequiredError,
            ChannelPrivateError,
        ) as exc:
            # تحديث حالة صلاحيات المجموعة دون إخراج الحساب منها أو حذفها
            await self._update_group_permission_state(
                group_id=group_id,
                can_send_messages=False,
                permission_status="forbidden",
            )
            return GroupSendAttemptResult(
                status="skipped",
                error_type=type(exc).__name__,
                error_message="الحساب ممنوع من الكتابة أو لا يملك صلاحية النشر في هذه المجموعة.",
                is_retryable=False,
            )

        except (ChatSendMediaForbiddenError, ChatSendInlineForbiddenError) as exc:
            await self._update_group_permission_state(
                group_id=group_id,
                can_send_messages=True,
                can_send_media=False,
                permission_status="restricted",
            )
            return GroupSendAttemptResult(
                status="skipped",
                error_type=type(exc).__name__,
                error_message="المجموعة تمنع إرسال الوسائط.",
                is_retryable=False,
            )

        except (ConnectionError, TimeoutError, OSError) as exc:
            return GroupSendAttemptResult(
                status="failed",
                error_type=type(exc).__name__,
                error_message=f"خطأ شبكة مؤقت: {exc}",
                is_retryable=True,
            )

        except Exception as exc:
            return GroupSendAttemptResult(
                status="failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
                is_retryable=False,
            )

    async def _update_group_permission_state(
        self,
        group_id: int,
        can_send_messages: bool,
        permission_status: str,
        can_send_media: Optional[bool] = None,
    ) -> None:
        async with self.db_manager.session() as session:
            grp = await session.get(Group, group_id)
            if grp:
                grp.can_send_messages = can_send_messages
                grp.permission_status = permission_status
                if can_send_media is not None:
                    grp.can_send_media = can_send_media
