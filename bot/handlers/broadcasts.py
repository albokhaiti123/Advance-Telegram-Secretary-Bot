from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from bot.keyboards.main_menu import get_section_submenu_keyboard
from bot.keyboards.navigation import (
    build_back_and_home_row,
    build_cancel_keyboard,
    build_confirm_cancel_keyboard,
)
from bot.states.admin_states import CampaignWizardStates, CollectionWizardStates
from database.database import DatabaseManager
from database.models import BroadcastCampaign, BroadcastRun
from integrations.personal.dialogs import PersonalDialogsInspector
from services.broadcast_manager import BroadcastManager
from services.campaign_manager import CampaignManager
from services.group_manager import GroupManager

broadcasts_router = Router(name="broadcasts_router")


@broadcasts_router.callback_query(F.data == "bc:discover")
async def cb_discover_groups(
    callback: CallbackQuery,
    dialogs_inspector: PersonalDialogsInspector,
) -> None:
    await callback.answer("جاري فحص المجموعات وصلاحيات النشر...")
    try:
        summary = await dialogs_inspector.discover_and_sync_groups()
        text = (
            "✅ <b>اكتمل اكتشاف وفحص المجموعات</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>إجمالي المجموعات المكتشفة:</b> {summary.total_discovered}\n"
            f"• <b>مجموعات متاحة للنشر:</b> {summary.sendable_count} 🟢\n"
            f"• <b>مجموعات مقيدة/ممنوعة:</b> {summary.restricted_count} 🔴"
        )
    except Exception as exc:
        text = f"⚠️ تعذر اكتشاف المجموعات: {exc}"

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_section_submenu_keyboard("broadcasts"),
            parse_mode="HTML",
        )


@broadcasts_router.callback_query(F.data.startswith("bc:groups"))
async def cb_list_discovered_groups(
    callback: CallbackQuery,
    group_manager: GroupManager,
) -> None:
    groups = await group_manager.list_groups()
    lines = []
    for g in groups[:20]:
        badge = "🟢" if g.can_send_messages else "🔴"
        lines.append(f"{badge} <b>{g.title}</b> (<code>{g.group_id}</code>)")

    body = "\n".join(lines) if lines else "لا توجد مجموعات مكتشفة بعد."
    text = f"📋 <b>قائمة المجموعات ({len(groups)})</b>\n━━━━━━━━━━━━━━━━━━━━\n{body}"
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_section_submenu_keyboard("broadcasts"),
            parse_mode="HTML",
        )
    await callback.answer()


@broadcasts_router.callback_query(F.data.startswith("bc:collections"))
async def cb_list_collections(
    callback: CallbackQuery,
    group_manager: GroupManager,
) -> None:
    cols = await group_manager.list_collections_with_counts()
    rows = [
        [
            InlineKeyboardButton(
                text=f"🗂️ {c.name} ({count} مجموعة)",
                callback_data=f"bc:col_view:{c.id}",
            )
        ]
        for c, count in cols
    ]
    rows.append([InlineKeyboardButton(text="➕ إنشاء تصنيف جديد", callback_data="bc:col_add")])
    rows.append(build_back_and_home_row("sec:broadcasts"))

    if callback.message:
        await callback.message.edit_text(
            text=f"🗂️ <b>تصنيفات المجموعات ({len(cols)})</b>\nاختر تصنيفًا لإدارته:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await callback.answer()


@broadcasts_router.callback_query(F.data == "bc:col_add")
async def cb_add_collection_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CollectionWizardStates.waiting_for_name)
    if callback.message:
        await callback.message.edit_text(
            text="➕ <b>إنشاء تصنيف مجموعات جديد</b>\nأرسل اسم التصنيف (مثال: مجموعات الترويج):",
            reply_markup=build_cancel_keyboard("sec:broadcasts"),
            parse_mode="HTML",
        )
    await callback.answer()


@broadcasts_router.message(CollectionWizardStates.waiting_for_name)
async def msg_add_collection_save(
    message: Message,
    state: FSMContext,
    group_manager: GroupManager,
) -> None:
    name = (message.text or "").strip()
    col = await group_manager.create_collection(name=name)
    await state.clear()
    await message.answer(
        text=f"✅ تم إنشاء التصنيف <b>{col.name}</b> بنجاح.",
        reply_markup=get_section_submenu_keyboard("broadcasts"),
        parse_mode="HTML",
    )


# --- إدارة الحملات وتأكيدها وإيقافها واستبدالها ---
@broadcasts_router.callback_query(F.data == "camp:create")
async def cb_create_campaign_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CampaignWizardStates.waiting_for_name)
    if callback.message:
        await callback.message.edit_text(
            text="📢 <b>إنشاء حملة بث جديدة</b>\nأرسل اسم الحملة الإعلانية:",
            reply_markup=build_cancel_keyboard("sec:campaigns"),
            parse_mode="HTML",
        )
    await callback.answer()


@broadcasts_router.message(CampaignWizardStates.waiting_for_name)
async def msg_campaign_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=(message.text or "").strip())
    await state.set_state(CampaignWizardStates.waiting_for_content)
    await message.answer(
        text="📝 أرسل الآن نص رسالة البث التي ستُنشر بهوية حسابك الشخصي في المجموعات:",
        reply_markup=build_cancel_keyboard("sec:campaigns"),
    )


@broadcasts_router.message(CampaignWizardStates.waiting_for_content)
async def msg_campaign_content(
    message: Message,
    state: FSMContext,
    campaign_manager: CampaignManager,
) -> None:
    data = await state.get_data()
    camp = await campaign_manager.create_campaign(
        name=data["name"],
        content_type="text",
        text_content=message.html_text or message.text,
        schedule_type="immediate",
    )
    await state.clear()

    preview_text = (
        f"📢 <b>تأكيد إطلاق الحملة: {camp.name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>طريقة الإرسال:</b> الحساب الشخصي (MTProto)\n"
        f"• <b>موعد التنفيذ:</b> فوري بعد التأكيد\n"
        f"• <b>محتوى الرسالة:</b>\n{camp.text_content}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "هل تؤكد بدء البث الآن؟"
    )
    await message.answer(
        text=preview_text,
        reply_markup=build_confirm_cancel_keyboard(
            confirm_callback=f"camp:launch:{camp.id}",
            cancel_callback="sec:campaigns",
            confirm_text="🚀 تأكيد وبدء البث",
        ),
        parse_mode="HTML",
    )


@broadcasts_router.callback_query(F.data.startswith("camp:launch:"))
async def cb_launch_campaign(
    callback: CallbackQuery,
    campaign_manager: CampaignManager,
    broadcast_manager: BroadcastManager,
) -> None:
    camp_id = int((callback.data or "").split(":")[-1])
    run_obj = await campaign_manager.initiate_new_run(camp_id)
    await callback.answer("بدأ تنفيذ الحملة...")
    final_status = await broadcast_manager.execute_campaign_run(camp_id, run_obj.run_id)

    if callback.message:
        await callback.message.edit_text(
            text=f"✅ اكتملت دورة البث للحملة #{camp_id} بالحالة: <b>{final_status}</b>",
            reply_markup=get_section_submenu_keyboard("campaigns"),
            parse_mode="HTML",
        )
