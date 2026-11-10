import html
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.database import DatabaseManager
from database.models import MediaReference, ReplyButton, ReplyTemplate
from security.validators import InputValidator

ARABIC_WEEKDAYS = {
    0: "الإثنين",
    1: "الثلاثاء",
    2: "الأربعاء",
    3: "الخميس",
    4: "الجمعة",
    5: "السبت",
    6: "الأحد",
}

VARIABLE_PATTERN = re.compile(r"\{([a-zA-Z0-9_]+)\}")


@dataclass
class RenderedTemplatePayload:
    template_id: int
    template_name: str
    content_type: str
    text: str
    parse_mode: str
    disable_web_page_preview: bool
    bot_file_id: Optional[str] = None
    local_file_path: Optional[str] = None
    reply_markup: Optional[InlineKeyboardMarkup] = None


class TemplateManager:
    """
    إدارة القوالب، تعويض المتغيرات الديناميكية بأمان، وبناء لوحة الأزرار التفاعلية.
    """

    def __init__(self, db_manager: DatabaseManager, timezone_str: str = "Asia/Aden") -> None:
        self.db_manager = db_manager
        self.timezone_str = timezone_str

    def render_text_variables(
        self,
        raw_text: Optional[str],
        context: Optional[Dict[str, Any]] = None,
        parse_mode: str = "HTML",
    ) -> str:
        """
        تعويض المتغيرات الديناميكية مثل {first_name}, {username}, {date}, {time}
        مع الهروب الآمن (HTML Escaping) لمدخلات المستخدم والتعامل السليم مع المتغيرات غير المتاحة.
        """
        if not raw_text:
            return ""

        ctx = context or {}
        try:
            tz = ZoneInfo(self.timezone_str)
        except Exception:
            tz = ZoneInfo("UTC")

        now = datetime.now(tz)
        first_name = str(ctx.get("first_name") or "عزيزي المتصل").strip()
        last_name = str(ctx.get("last_name") or "").strip()
        full_name = str(
            ctx.get("full_name") or f"{first_name} {last_name}".strip()
        )
        raw_username = str(ctx.get("username") or "").lstrip("@").strip()
        username_formatted = f"@{raw_username}" if raw_username else first_name
        chat_title = str(ctx.get("chat_title") or full_name).strip()
        user_id = str(ctx.get("user_id") or "")

        values_map = {
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "username": username_formatted,
            "user_id": user_id,
            "chat_title": chat_title,
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
            "weekday": ARABIC_WEEKDAYS.get(now.weekday(), ""),
        }

        # إضافة أي متغيرات مخصصة إضافية
        for k, v in ctx.items():
            if k not in values_map and v is not None:
                values_map[k] = str(v)

        def _replace_match(match: re.Match) -> str:
            var_name = match.group(1)
            if var_name not in values_map:
                # متغير غير معروف أو غير متاح: نرجعه فارغًا أو آمنًا دون إفساد الرسالة
                return ""
            val = values_map[var_name]
            if parse_mode.upper() == "HTML":
                return html.escape(val)
            return val

        rendered = VARIABLE_PATTERN.sub(_replace_match, raw_text)
        # تنظيف المسافات المزدوجة الناتجة عن متغيرات فارغة
        return re.sub(r"[ \t]{2,}", " ", rendered).strip()

    @staticmethod
    def build_inline_keyboard(
        buttons: Sequence[ReplyButton],
    ) -> Optional[InlineKeyboardMarkup]:
        """تجميع الأزرار التفاعلية مرتبة حسب الصفوف والأعمدة."""
        if not buttons:
            return None

        sorted_btns = sorted(buttons, key=lambda b: (b.row_index, b.col_index, b.id or 0))
        rows_dict: Dict[int, List[InlineKeyboardButton]] = {}

        for btn in sorted_btns:
            if btn.button_type == "url" and btn.url:
                valid, _ = InputValidator.validate_button_url(btn.url)
                if not valid:
                    continue
                tg_btn = InlineKeyboardButton(text=btn.text, url=btn.url.strip())
            elif btn.button_type == "callback" and btn.callback_data:
                tg_btn = InlineKeyboardButton(
                    text=btn.text,
                    callback_data=btn.callback_data[:64],
                )
            else:
                continue

            rows_dict.setdefault(btn.row_index, []).append(tg_btn)

        if not rows_dict:
            return None

        keyboard_rows = [rows_dict[row_idx] for row_idx in sorted(rows_dict.keys())]
        return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    async def create_template(
        self,
        name: str,
        content_type: str = "text",
        text_content: Optional[str] = None,
        media_ref_id: Optional[int] = None,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
        is_quick_reply: bool = False,
        category: Optional[str] = None,
        buttons_data: Optional[List[Dict[str, Any]]] = None,
    ) -> ReplyTemplate:
        """إنشاء قالب رد جديد مع أزراره التفاعلية."""
        async with self.db_manager.session() as session:
            template = ReplyTemplate(
                name=name.strip(),
                content_type=content_type,
                text_content=text_content,
                media_ref_id=media_ref_id,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
                is_quick_reply=is_quick_reply,
                category=category,
            )
            session.add(template)
            await session.flush()

            if buttons_data:
                for idx, b_info in enumerate(buttons_data):
                    btn_type = b_info.get("button_type", "url")
                    if btn_type == "url":
                        is_valid, err = InputValidator.validate_button_url(b_info.get("url", ""))
                        if not is_valid:
                            raise ValueError(f"زر غير صالح ({b_info.get('text')}): {err}")

                    session.add(
                        ReplyButton(
                            template_id=template.id,
                            text=str(b_info["text"]).strip(),
                            button_type=btn_type,
                            url=b_info.get("url"),
                            callback_data=b_info.get("callback_data"),
                            row_index=int(b_info.get("row_index", idx)),
                            col_index=int(b_info.get("col_index", 0)),
                        )
                    )
            await session.flush()
            return template

    async def add_button_to_template(
        self,
        template_id: int,
        text: str,
        button_type: str = "url",
        url: Optional[str] = None,
        callback_data: Optional[str] = None,
        row_index: int = 0,
        col_index: int = 0,
    ) -> ReplyButton:
        """إضافة زر تفاعلي إلى قالب موجود مع التحقق من صحة الرابط."""
        if button_type == "url":
            is_valid, err = InputValidator.validate_button_url(url or "")
            if not is_valid:
                raise ValueError(err or "رابط غير صالح.")

        async with self.db_manager.session() as session:
            btn = ReplyButton(
                template_id=template_id,
                text=text.strip(),
                button_type=button_type,
                url=url.strip() if url else None,
                callback_data=callback_data,
                row_index=row_index,
                col_index=col_index,
            )
            session.add(btn)
            await session.flush()
            return btn

    async def delete_button(self, button_id: int) -> bool:
        """حذف زر تفاعلي بمعرفه."""
        async with self.db_manager.session() as session:
            btn = await session.get(ReplyButton, button_id)
            if not btn:
                return False
            await session.delete(btn)
            return True

    async def build_rendered_payload(
        self,
        template_id: int,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[RenderedTemplatePayload]:
        """تجهيز القالب النهائي (نص معوض + وسيط + أزرار) للإرسال أو المعاينة."""
        async with self.db_manager.session() as session:
            stmt = (
                select(ReplyTemplate)
                .options(
                    selectinload(ReplyTemplate.buttons),
                    selectinload(ReplyTemplate.media_ref),
                )
                .where(ReplyTemplate.id == template_id)
            )
            template = await session.scalar(stmt)
            if not template:
                return None

            rendered_text = self.render_text_variables(
                raw_text=template.text_content,
                context=context,
                parse_mode=template.parse_mode,
            )
            keyboard = self.build_inline_keyboard(template.buttons)

            bot_file_id = None
            local_file_path = None
            if template.media_ref:
                bot_file_id = template.media_ref.bot_file_id
                local_file_path = template.media_ref.local_file_path

            return RenderedTemplatePayload(
                template_id=template.id,
                template_name=template.name,
                content_type=template.content_type,
                text=rendered_text,
                parse_mode=template.parse_mode,
                disable_web_page_preview=template.disable_web_page_preview,
                bot_file_id=bot_file_id,
                local_file_path=local_file_path,
                reply_markup=keyboard,
            )
