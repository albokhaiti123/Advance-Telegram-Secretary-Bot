from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from database.database import DatabaseManager
from bot.handlers.dashboard import render_main_dashboard_text
from bot.keyboards.main_menu import get_main_dashboard_keyboard

start_router = Router(name="start_router")


@start_router.message(CommandStart())
@start_router.message(Command("panel", "admin"))
async def cmd_start_dashboard(
    message: Message,
    state: FSMContext,
    db_manager: DatabaseManager,
) -> None:
    """فتح لوحة التحكم الرئيسية للمالك وإلغاء أي حالة إدخال سابقة."""
    await state.clear()
    text, auto_reply_enabled = await render_main_dashboard_text(db_manager)
    await message.answer(
        text=text,
        reply_markup=get_main_dashboard_keyboard(auto_reply_enabled=auto_reply_enabled),
        parse_mode="HTML",
    )
