from typing import List, Tuple
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_back_and_home_row(back_callback: str = "nav:main") -> List[InlineKeyboardButton]:
    """بناء صف أزرار الرجوع والقائمة الرئيسية."""
    row = [InlineKeyboardButton(text="🔙 رجوع", callback_data=back_callback)]
    if back_callback != "nav:main":
        row.append(InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="nav:main"))
    return row


def build_cancel_keyboard(cancel_callback: str = "nav:cancel") -> InlineKeyboardMarkup:
    """لوحة إلغاء العملية الحالية أثناء إدخال البيانات."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ إلغاء العملية", callback_data=cancel_callback)]
        ]
    )


def build_confirm_cancel_keyboard(
    confirm_callback: str,
    cancel_callback: str = "nav:main",
    confirm_text: str = "✅ تأكيد التنفيذ",
    cancel_text: str = "❌ إلغاء",
) -> InlineKeyboardMarkup:
    """لوحة تأكيد أو إلغاء الإجراءات الحساسة (مثل حذف قاعدة أو إطلاق حملة)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=confirm_text, callback_data=confirm_callback),
                InlineKeyboardButton(text=cancel_text, callback_data=cancel_callback),
            ]
        ]
    )


def build_pagination_row(
    prefix: str,
    current_page: int,
    total_pages: int,
) -> List[InlineKeyboardButton]:
    """بناء صف أزرار الترقيم للقوائم الطويلة."""
    if total_pages <= 1:
        return []
    buttons: List[InlineKeyboardButton] = []
    if current_page > 1:
        buttons.append(
            InlineKeyboardButton(text="➡️ السابق", callback_data=f"{prefix}:page:{current_page - 1}")
        )
    buttons.append(
        InlineKeyboardButton(text=f"📄 {current_page}/{total_pages}", callback_data="nav:noop")
    )
    if current_page < total_pages:
        buttons.append(
            InlineKeyboardButton(text="التالي ⬅️", callback_data=f"{prefix}:page:{current_page + 1}")
        )
    return buttons
