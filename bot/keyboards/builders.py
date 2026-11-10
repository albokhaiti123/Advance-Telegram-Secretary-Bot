from typing import Sequence
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot.keyboards.navigation import build_back_and_home_row, build_pagination_row
from database.models import AutoReplyRule, ReplyTemplate


def build_rules_list_keyboard(
    rules: Sequence[AutoReplyRule],
    page: int = 1,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    """بناء لوحة عرض القواعد مع حالتها وأولويتها."""
    rows = []
    for r in rules:
        badge = "🟢" if r.is_active else "🔴"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{badge} [{r.priority}] {r.name} ({r.trigger_type})",
                    callback_data=f"rules:view:{r.id}",
                )
            ]
        )
    pag = build_pagination_row("rules:list", page, total_pages)
    if pag:
        rows.append(pag)
    rows.append([InlineKeyboardButton(text="➕ إضافة قاعدة جديدة", callback_data="rules:add")])
    rows.append(build_back_and_home_row("sec:rules"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_rule_details_keyboard(rule: AutoReplyRule) -> InlineKeyboardMarkup:
    """بناء أزرار التحكم بقاعدة محددة (تفعيل/تعطيل، معاينة، تغيير أولوية، نسخ، حذف)."""
    toggle_text = "🔴 تعطيل القاعدة" if rule.is_active else "🟢 تفعيل القاعدة"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle_text,
                    callback_data=f"rules:toggle:{rule.id}",
                ),
                InlineKeyboardButton(
                    text="👁️ معاينة الرد",
                    callback_data=f"tpl:preview:{rule.template_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬆️ رفع الأولوية (+10)",
                    callback_data=f"rules:prio_up:{rule.id}",
                ),
                InlineKeyboardButton(
                    text="⬇️ خفض الأولوية (-10)",
                    callback_data=f"rules:prio_down:{rule.id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📋 نسخ القاعدة",
                    callback_data=f"rules:clone:{rule.id}",
                ),
                InlineKeyboardButton(
                    text="🗑️ حذف القاعدة",
                    callback_data=f"rules:del_ask:{rule.id}",
                ),
            ],
            build_back_and_home_row("rules:list:1"),
        ]
    )


def build_trigger_types_keyboard() -> InlineKeyboardMarkup:
    """اختيار نوع المحفز عند إنشاء قاعدة جديدة."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👋 أول رسالة (first_message)",
                    callback_data="rules:set_trig:first_message",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔑 كلمة مفتاحية (keyword)",
                    callback_data="rules:set_trig:keyword",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧩 تعبير نمطي (regex)",
                    callback_data="rules:set_trig:regex",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌐 أي رسالة (any)",
                    callback_data="rules:set_trig:any",
                )
            ],
            build_back_and_home_row("sec:rules"),
        ]
    )


def build_templates_list_keyboard(
    templates: Sequence[ReplyTemplate],
    select_for_rule: bool = False,
) -> InlineKeyboardMarkup:
    """لوحة عرض القوالب أو اختيارها لقاعدة جديدة."""
    rows = []
    prefix = "rules:pick_tpl" if select_for_rule else "tpl:view"
    for tpl in templates:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📄 {tpl.name} ({tpl.content_type})",
                    callback_data=f"{prefix}:{tpl.id}",
                )
            ]
        )
    if not select_for_rule:
        rows.append([InlineKeyboardButton(text="➕ إنشاء قالب جديد", callback_data="tpl:add")])
    rows.append(build_back_and_home_row("sec:templates"))
    return InlineKeyboardMarkup(inline_keyboard=rows)
