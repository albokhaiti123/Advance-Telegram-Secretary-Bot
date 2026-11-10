from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.keyboards.main_menu import get_section_submenu_keyboard
from bot.keyboards.navigation import (
    build_back_and_home_row,
    build_cancel_keyboard,
    build_pagination_row,
)
from bot.states.admin_states import ExceptionWizardStates
from services.conversation_manager import ConversationManager

conversations_router = Router(name="conversations_router")


@conversations_router.callback_query(F.data.startswith("conv:list"))
async def cb_list_conversations(
    callback: CallbackQuery,
    conversation_manager: ConversationManager,
) -> None:
    parts = (callback.data or "conv:list:1").split(":")
    page = int(parts[-1]) if parts[-1].isdigit() else 1

    items, total, total_pages = await conversation_manager.list_conversations(page=page)
    rows = []
    for c in items:
        badge = "🟢" if c.auto_reply_enabled else "🔇"
        name = c.title_or_name or str(c.chat_id)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{badge} {name} (رسائل: {c.message_count} | ردود: {c.reply_count})",
                    callback_data=f"conv:view:{c.chat_id}",
                )
            ]
        )
    pag = build_pagination_row("conv:list", page, total_pages)
    if pag:
        rows.append(pag)
    rows.append(build_back_and_home_row("sec:conversations"))

    if callback.message:
        await callback.message.edit_text(
            text=f"💬 <b>المحادثات المسجلة ({total})</b>\nاختر محادثة للتحكم بها أو مراجعة آخر رد تلقائي:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await callback.answer()


@conversations_router.callback_query(F.data.startswith("conv:view:"))
async def cb_view_conversation(
    callback: CallbackQuery,
    conversation_manager: ConversationManager,
) -> None:
    chat_id = int((callback.data or "").split(":")[-1])
    summary = await conversation_manager.get_conversation_details(chat_id)
    if not summary:
        await callback.answer("المحادثة غير موجودة.", show_alert=True)
        return

    conv = summary.conversation
    latest_msg_id = (
        summary.latest_managed_response.message_id
        if summary.latest_managed_response
        else "لا يوجد"
    )
    first_handled = (
        "نعم" if (summary.state and summary.state.first_message_handled) else "لا"
    )
    ignore_badge = "🚫 في قائمة التجاهل" if summary.is_ignored else "✅ غير متجاهلة"
    toggle_label = (
        "🔴 تعطيل الرد التلقائي" if conv.auto_reply_enabled else "🟢 تفعيل الرد التلقائي"
    )
    ignore_btn_label = (
        "✅ إزالة من قائمة التجاهل"
        if summary.is_ignored
        else "🚫 إضافة لقائمة التجاهل"
    )
    ignore_cb = (
        f"conv:unignore:{chat_id}" if summary.is_ignored else f"conv:ignore:{chat_id}"
    )

    text = (
        f"💬 <b>تفاصيل المحادثة: {conv.title_or_name or chat_id}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>معرف المحادثة:</b> <code>{conv.chat_id}</code>\n"
        f"• <b>المعرف (@):</b> {('@' + conv.username) if conv.username else '—'}\n"
        f"• <b>الردود التلقائية:</b> {'🟢 مفعلة' if conv.auto_reply_enabled else '🔴 معطلة'}\n"
        f"• <b>حالة التجاهل:</b> {ignore_badge}\n"
        f"• <b>عدد الرسائل الواردة:</b> {conv.message_count}\n"
        f"• <b>عدد الردود المرسلة:</b> {conv.reply_count}\n"
        f"• <b>معرف آخر رد تلقائي نشط:</b> <code>{latest_msg_id}</code>\n"
        f"• <b>تمت معالجة أول رسالة:</b> {first_handled}"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle_label, callback_data=f"conv:toggle:{chat_id}"
                ),
                InlineKeyboardButton(text=ignore_btn_label, callback_data=ignore_cb),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 إعادة ضبط حالة المحادثة (Reset)",
                    callback_data=f"conv:reset:{chat_id}",
                )
            ],
            build_back_and_home_row("conv:list:1"),
        ]
    )
    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@conversations_router.callback_query(F.data.startswith("conv:toggle:"))
async def cb_toggle_conv(
    callback: CallbackQuery,
    conversation_manager: ConversationManager,
) -> None:
    chat_id = int((callback.data or "").split(":")[-1])
    await conversation_manager.toggle_conversation_auto_reply(chat_id)
    await cb_view_conversation(callback, conversation_manager)


@conversations_router.callback_query(F.data.startswith("conv:ignore:"))
async def cb_ignore_conv(
    callback: CallbackQuery,
    conversation_manager: ConversationManager,
) -> None:
    chat_id = int((callback.data or "").split(":")[-1])
    await conversation_manager.set_chat_exception(
        chat_id=chat_id, list_type="blacklist", reason="إضافة من تفاصيل المحادثة"
    )
    await callback.answer("تمت إضافة المحادثة إلى قائمة التجاهل 🚫", show_alert=True)
    await cb_view_conversation(callback, conversation_manager)


@conversations_router.callback_query(F.data.startswith("conv:unignore:"))
async def cb_unignore_conv(
    callback: CallbackQuery,
    conversation_manager: ConversationManager,
) -> None:
    chat_id = int((callback.data or "").split(":")[-1])
    await conversation_manager.remove_chat_exception(chat_id)
    await callback.answer("تمت إزالة المحادثة من قائمة التجاهل ✅", show_alert=True)
    await cb_view_conversation(callback, conversation_manager)


@conversations_router.callback_query(F.data.startswith("conv:reset:"))
async def cb_reset_conv(
    callback: CallbackQuery,
    conversation_manager: ConversationManager,
) -> None:
    chat_id = int((callback.data or "").split(":")[-1])
    await conversation_manager.reset_conversation_state(chat_id)
    await callback.answer("تمت إعادة ضبط حالة المحادثة وتصفير Cooldown ✅", show_alert=True)
    await cb_view_conversation(callback, conversation_manager)


@conversations_router.callback_query(F.data == "conv:clear_locks")
async def cb_clear_pending_ops(
    callback: CallbackQuery,
    conversation_manager: ConversationManager,
) -> None:
    cleared = await conversation_manager.clear_all_pending_operations()
    await callback.answer(
        f"تم تنظيف {cleared} عملية معلقة بنجاح ✅", show_alert=True
    )


# --- معالجات الاستثناءات ---
@conversations_router.callback_query(F.data == "exc:add")
async def cb_add_exception_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ExceptionWizardStates.waiting_for_user_id)
    if callback.message:
        await callback.message.edit_text(
            text="🚫 <b>إضافة مستخدم إلى قائمة التجاهل</b>\nأرسل الآن المعرف الرقمي (User ID) للمستخدم المراد استثناؤه من الردود التلقائية:",
            reply_markup=build_cancel_keyboard("sec:exceptions"),
            parse_mode="HTML",
        )
    await callback.answer()


@conversations_router.message(ExceptionWizardStates.waiting_for_user_id)
async def msg_add_exception_user(
    message: Message,
    state: FSMContext,
    conversation_manager: ConversationManager,
) -> None:
    raw = (message.text or "").strip()
    if not raw.lstrip("-").isdigit():
        await message.answer("⚠️ يرجى إدخال معرف رقمي صحيح (أرقام فقط):")
        return
    uid = int(raw)
    await conversation_manager.set_user_exception(
        user_id=uid, list_type="blacklist", reason="إضافة يدوية من لوحة التحكم"
    )
    await state.clear()
    await message.answer(
        text=f"✅ تم إضافة المعرف <code>{uid}</code> إلى قائمة الاستثناءات بنجاح.",
        reply_markup=get_section_submenu_keyboard("exceptions"),
        parse_mode="HTML",
    )
