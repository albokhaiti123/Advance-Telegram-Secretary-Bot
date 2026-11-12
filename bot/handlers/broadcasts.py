import asyncio
import math
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from bot.keyboards.main_menu import get_section_submenu_keyboard
from bot.keyboards.navigation import (
    build_back_and_home_row,
    build_cancel_keyboard,
    build_confirm_cancel_keyboard,
    build_pagination_row,
)
from bot.states.admin_states import CampaignWizardStates, CollectionWizardStates
from database.database import DatabaseManager
from database.models import (
    BroadcastCampaign,
    BroadcastRun,
    Group,
    GroupCollection,
    GroupCollectionMember,
    ReplyButton,
)
from integrations.personal.dialogs import PersonalDialogsInspector
from security.validators import InputValidator
from services.broadcast_manager import BroadcastManager
from services.campaign_manager import CampaignManager
from services.group_manager import GroupManager
from services.media_manager import MediaManager

broadcasts_router = Router(name="broadcasts_router")


# =========================================================
# 1. اكتشاف وعرض المجموعات
# =========================================================
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
    parts = (callback.data or "bc:groups:1").split(":")
    page = int(parts[-1]) if parts[-1].isdigit() else 1
    page_size = 15

    groups = await group_manager.list_groups()
    total = len(groups)
    total_pages = max(1, math.ceil(total / page_size))
    slice_groups = groups[(page - 1) * page_size : page * page_size]

    lines = []
    for g in slice_groups:
        badge = "🟢" if g.can_send_messages else "🔴"
        lines.append(f"{badge} <b>{g.title[:35]}</b> (<code>{g.group_id}</code>)")

    body = "\n".join(lines) if lines else "لا توجد مجموعات مكتشفة بعد."
    text = f"📋 <b>قائمة المجموعات ({total}) — صفحة {page}/{total_pages}</b>\n━━━━━━━━━━━━━━━━━━━━\n{body}"

    rows = []
    pag = build_pagination_row("bc:groups", page, total_pages)
    if pag:
        rows.append(pag)
    rows.append(build_back_and_home_row("sec:broadcasts"))

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await callback.answer()


# =========================================================
# 2. إدارة تصنيفات المجموعات (Collections)
# =========================================================
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
            text=f"🗂️ <b>تصنيفات المجموعات ({len(cols)})</b>\nاختر تصنيفًا لعرض مجموعاته أو إضافة/إزالة مجموعات منه:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await callback.answer()


@broadcasts_router.callback_query(F.data.startswith("bc:col_view:"))
async def cb_view_collection_details(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    """فتح تصنيف محدد وعرض المجموعات بداخله وخيارات إدارته."""
    col_id = int((callback.data or "").split(":")[-1])
    async with db_manager.session() as session:
        col = await session.get(GroupCollection, col_id)
        if not col:
            await callback.answer("التصنيف غير موجود.", show_alert=True)
            return

        stmt = (
            select(Group)
            .join(GroupCollectionMember, GroupCollectionMember.group_id == Group.group_id)
            .where(GroupCollectionMember.collection_id == col_id)
            .order_by(Group.title.asc())
        )
        member_groups = list((await session.scalars(stmt)).all())

    lines = []
    rows = []
    for g in member_groups[:15]:
        badge = "🟢" if g.can_send_messages else "🔴"
        lines.append(f"• {badge} <b>{g.title[:32]}</b>")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"❌ إزالة: {g.title[:28]}",
                    callback_data=f"bc:col_remg:{col_id}:{g.group_id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="➕ إضافة مجموعات لهذا التصنيف",
                callback_data=f"bc:col_pickg:{col_id}:1",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🗑️ حذف التصنيف",
                callback_data=f"bc:col_del:{col_id}",
            )
        ]
    )
    rows.append(build_back_and_home_row("bc:collections:1"))

    body = "\n".join(lines) if lines else "لا توجد مجموعات مضافة داخل هذا التصنيف بعد."
    text = (
        f"🗂️ <b>تصنيف: {col.name}</b>\n"
        f"• <b>عدد المجموعات في التصنيف:</b> {len(member_groups)}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{body}"
    )

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await callback.answer()


@broadcasts_router.callback_query(F.data.startswith("bc:col_pickg:"))
async def cb_pick_groups_for_collection(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    """عرض المجموعات المتاحة لإضافتها إلى التصنيف بضغطة زر."""
    parts = (callback.data or "").split(":")
    col_id = int(parts[2])
    page = int(parts[-1]) if parts[-1].isdigit() else 1
    page_size = 10

    async with db_manager.session() as session:
        col = await session.get(GroupCollection, col_id)
        if not col:
            await callback.answer("التصنيف غير موجود.", show_alert=True)
            return

        existing_ids = set(
            (
                await session.scalars(
                    select(GroupCollectionMember.group_id).where(
                        GroupCollectionMember.collection_id == col_id
                    )
                )
            ).all()
        )
        all_sendable = list(
            (
                await session.scalars(
                    select(Group)
                    .where(Group.is_active.is_(True), Group.can_send_messages.is_(True))
                    .order_by(Group.title.asc())
                )
            ).all()
        )

    available = [g for g in all_sendable if g.group_id not in existing_ids]
    total = len(available)
    total_pages = max(1, math.ceil(total / page_size))
    slice_avail = available[(page - 1) * page_size : page * page_size]

    rows = []
    for g in slice_avail:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"➕ {g.title[:35]}",
                    callback_data=f"bc:col_addg:{col_id}:{g.group_id}:{page}",
                )
            ]
        )

    pag = build_pagination_row(f"bc:col_pickg:{col_id}", page, total_pages)
    if pag:
        rows.append(pag)
    rows.append(build_back_and_home_row(f"bc:col_view:{col_id}"))

    text = (
        f"➕ <b>إضافة مجموعات إلى تصنيف: {col.name}</b>\n"
        f"المجموعات المؤهلة للنشر المتاحة للإضافة ({total}):\n"
        "اضغط على أي مجموعة لإضافتها فورًا:"
    )
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await callback.answer()


@broadcasts_router.callback_query(F.data.startswith("bc:col_addg:"))
async def cb_add_group_to_collection_action(
    callback: CallbackQuery,
    group_manager: GroupManager,
    db_manager: DatabaseManager,
) -> None:
    parts = (callback.data or "").split(":")
    col_id, grp_id, page = int(parts[2]), int(parts[3]), int(parts[4])
    await group_manager.add_group_to_collection(col_id, grp_id)
    await callback.answer("تمت إضافة المجموعة للتصنيف ✅")
    cb_copy = callback.model_copy(update={"data": f"bc:col_pickg:{col_id}:{page}"})
    await cb_pick_groups_for_collection(cb_copy, db_manager)


@broadcasts_router.callback_query(F.data.startswith("bc:col_remg:"))
async def cb_remove_group_from_collection_action(
    callback: CallbackQuery,
    group_manager: GroupManager,
    db_manager: DatabaseManager,
) -> None:
    parts = (callback.data or "").split(":")
    col_id, grp_id = int(parts[2]), int(parts[3])
    await group_manager.remove_group_from_collection(col_id, grp_id)
    await callback.answer("تمت إزالة المجموعة من التصنيف 🗑️")
    cb_copy = callback.model_copy(update={"data": f"bc:col_view:{col_id}"})
    await cb_view_collection_details(cb_copy, db_manager)


@broadcasts_router.callback_query(F.data.startswith("bc:col_del:"))
async def cb_delete_collection_action(
    callback: CallbackQuery,
    group_manager: GroupManager,
) -> None:
    col_id = int((callback.data or "").split(":")[-1])
    await group_manager.delete_collection(col_id)
    await callback.answer("تم حذف التصنيف بنجاح (دون المساس بالمجموعات) ✅", show_alert=True)
    await cb_list_collections(callback, group_manager)


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
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"➕ إضافة مجموعات إلى «{col.name}»",
                    callback_data=f"bc:col_pickg:{col.id}:1",
                )
            ],
            build_back_and_home_row("bc:collections:1"),
        ]
    )
    await message.answer(
        text=f"✅ تم إنشاء التصنيف <b>{col.name}</b> بنجاح.\nيمكنك الآن إضافة المجموعات إليه:",
        reply_markup=kb,
        parse_mode="HTML",
    )


# =========================================================
# 3. إدارة الحملات الإعلانية، الأزرار، الإيقاف، الاستبدال، والأرشيف
# =========================================================
@broadcasts_router.callback_query(F.data.startswith("camp:active"))
async def cb_list_active_campaigns(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    """عرض الحملات الجارية والمجدولة والمسودات."""
    async with db_manager.session() as session:
        stmt = (
            select(BroadcastCampaign)
            .where(BroadcastCampaign.status.in_(["running", "scheduled", "paused", "draft"]))
            .order_by(BroadcastCampaign.id.desc())
            .limit(15)
        )
        campaigns = list((await session.scalars(stmt)).all())

    rows = []
    for c in campaigns:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📢 #{c.id} {c.name} [{c.status}]",
                    callback_data=f"camp:view:{c.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ إنشاء حملة جديدة", callback_data="camp:create")])
    rows.append(build_back_and_home_row("sec:campaigns"))

    body = (
        "اختر أي حملة لمتابعة تقدمها، إيقافها، استئنافها، أو إضافة أزرار لها:"
        if campaigns
        else "لا توجد حملات جارية أو مجدولة حاليًا."
    )
    if callback.message:
        await callback.message.edit_text(
            text=f"📊 <b>الحملات الجارية والمجدولة ({len(campaigns)})</b>\n━━━━━━━━━━━━━━━━━━━━\n{body}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await callback.answer()


@broadcasts_router.callback_query(F.data.startswith("camp:history"))
async def cb_list_campaigns_history(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    """عرض أرشيف الحملات السابقة ونتائج تنفيذها."""
    async with db_manager.session() as session:
        stmt = (
            select(BroadcastCampaign)
            .order_by(BroadcastCampaign.id.desc())
            .limit(15)
        )
        campaigns = list((await session.scalars(stmt)).all())

    rows = []
    for c in campaigns:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📜 #{c.id} {c.name} ({c.status})",
                    callback_data=f"camp:view:{c.id}",
                )
            ]
        )
    rows.append(build_back_and_home_row("sec:campaigns"))

    body = (
        "اختر أي حملة لاستعراض تفاصيلها وعدد المجموعات التي استلمت الرسالة:"
        if campaigns
        else "لا توجد حملات في الأرشيف بعد."
    )
    if callback.message:
        await callback.message.edit_text(
            text=f"📜 <b>أرشيف الحملات ونتائجها ({len(campaigns)})</b>\n━━━━━━━━━━━━━━━━━━━━\n{body}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await callback.answer()


@broadcasts_router.callback_query(F.data.startswith("camp:view:"))
async def cb_view_campaign_details(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    """عرض تفاصيل حملة محددة، إحصائيات التشغيل، وأزرار التحكم الكاملة."""
    camp_id = int((callback.data or "").split(":")[-1])
    async with db_manager.session() as session:
        camp = await session.get(BroadcastCampaign, camp_id)
        if not camp:
            await callback.answer("الحملة غير موجودة.", show_alert=True)
            return

        run_rec = None
        if camp.active_run_id:
            run_rec = await session.get(BroadcastRun, camp.active_run_id)
        if not run_rec:
            run_rec = await session.scalar(
                select(BroadcastRun)
                .where(BroadcastRun.campaign_id == camp_id)
                .order_by(BroadcastRun.started_at.desc())
            )

        btns = list(
            (
                await session.scalars(
                    select(ReplyButton).where(ReplyButton.campaign_id == camp_id)
                )
            ).all()
        )

    run_info = (
        f"• <b>معرف التشغيل (run_id):</b> <code>{run_rec.run_id}</code>\n"
        f"• <b>المستهدف:</b> {run_rec.total_targets} | <b>نجح:</b> {run_rec.sent_count} ✅ | <b>فشل:</b> {run_rec.failed_count} ❌ | <b>تخطي:</b> {run_rec.skipped_count} ⏭️\n"
        if run_rec
        else "• لم يبدأ تنفيذ الحملة بعد.\n"
    )

    text = (
        f"📢 <b>تفاصيل الحملة #{camp.id}: {camp.name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>الحالة الحالية:</b> <code>{camp.status}</code>\n"
        f"• <b>نوع المحتوى:</b> <code>{camp.content_type}</code>\n"
        f"• <b>عدد الأزرار المرفقة:</b> {len(btns)}\n"
        f"{run_info}"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 <b>محتوى الرسالة:</b>\n{(camp.text_content or '')[:500]}"
    )

    rows = []
    if camp.status in {"draft", "completed", "partial", "failed", "cancelled"}:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🚀 إطلاق / إعادة تشغيل الحملة الآن",
                    callback_data=f"camp:launch:{camp.id}",
                )
            ]
        )
    if camp.status == "running":
        rows.append(
            [
                InlineKeyboardButton(
                    text="⏸️ إيقاف مؤقت",
                    callback_data=f"camp:pause:{camp.id}",
                ),
                InlineKeyboardButton(
                    text="🛑 إلغاء الحملة",
                    callback_data=f"camp:cancel:{camp.id}",
                ),
            ]
        )
    if camp.status == "paused":
        rows.append(
            [
                InlineKeyboardButton(
                    text="▶️ استئناف الحملة",
                    callback_data=f"camp:resume:{camp.id}",
                ),
                InlineKeyboardButton(
                    text="🛑 إلغاء الحملة",
                    callback_data=f"camp:cancel:{camp.id}",
                ),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=f"🔘 إضافة زر رابط للحملة ({len(btns)})",
                callback_data=f"camp:btn_add:{camp.id}",
            ),
            InlineKeyboardButton(
                text="🔄 استبدال محتوى الحملة",
                callback_data=f"camp:replace:{camp.id}",
            ),
        ]
    )
    rows.append(build_back_and_home_row("sec:campaigns"))

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await callback.answer()


@broadcasts_router.callback_query(F.data.startswith("camp:pause:"))
async def cb_pause_campaign(
    callback: CallbackQuery,
    campaign_manager: CampaignManager,
    db_manager: DatabaseManager,
) -> None:
    camp_id = int((callback.data or "").split(":")[-1])
    await campaign_manager.pause_campaign(camp_id)
    await callback.answer("تم إيقاف الحملة مؤقتًا ⏸️", show_alert=True)
    await cb_view_campaign_details(callback, db_manager)


@broadcasts_router.callback_query(F.data.startswith("camp:resume:"))
async def cb_resume_campaign(
    callback: CallbackQuery,
    campaign_manager: CampaignManager,
    broadcast_manager: BroadcastManager,
    db_manager: DatabaseManager,
) -> None:
    camp_id = int((callback.data or "").split(":")[-1])
    run_id = await campaign_manager.resume_campaign(camp_id)
    if run_id:
        asyncio.create_task(broadcast_manager.execute_campaign_run(camp_id, run_id))
        await callback.answer("تم استئناف الحملة في الخلفية ▶️", show_alert=True)
    else:
        await callback.answer("لا يمكن استئناف هذه الحملة حاليًا.", show_alert=True)
    await cb_view_campaign_details(callback, db_manager)


@broadcasts_router.callback_query(F.data.startswith("camp:cancel:"))
async def cb_cancel_campaign(
    callback: CallbackQuery,
    campaign_manager: CampaignManager,
    db_manager: DatabaseManager,
) -> None:
    camp_id = int((callback.data or "").split(":")[-1])
    await campaign_manager.cancel_campaign(camp_id)
    await callback.answer("تم إلغاء الحملة وإيقاف مهامها نهائيًا 🛑", show_alert=True)
    await cb_view_campaign_details(callback, db_manager)


# --- إضافة أزرار تفاعلية للحملة ---
@broadcasts_router.callback_query(F.data.startswith("camp:btn_add:"))
async def cb_campaign_add_button_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    camp_id = int((callback.data or "").split(":")[-1])
    await state.update_data(target_campaign_id=camp_id)
    await state.set_state(CampaignWizardStates.waiting_for_button_text)
    if callback.message:
        await callback.message.edit_text(
            text="🔘 <b>إضافة زر رابط للحملة الإعلانية</b>\nأرسل الآن نص الزر (مثال: 📢 انضم لقناتنا أو 💬 تواصل معنا):",
            reply_markup=build_cancel_keyboard(f"camp:view:{camp_id}"),
            parse_mode="HTML",
        )
    await callback.answer()


@broadcasts_router.message(CampaignWizardStates.waiting_for_button_text)
async def msg_campaign_button_text(message: Message, state: FSMContext) -> None:
    btn_text = (message.text or "").strip()
    if not btn_text:
        await message.answer("⚠️ يرجى إرسال نص صالح للزر:")
        return
    data = await state.get_data()
    camp_id = data.get("target_campaign_id")
    await state.update_data(button_text=btn_text)
    await state.set_state(CampaignWizardStates.waiting_for_button_url)
    await message.answer(
        text="🔗 أرسل الآن رابط الزر (يبدأ بـ <code>https://</code> أو <code>tg://</code>):",
        reply_markup=build_cancel_keyboard(f"camp:view:{camp_id}"),
        parse_mode="HTML",
    )


@broadcasts_router.message(CampaignWizardStates.waiting_for_button_url)
async def msg_campaign_button_url(
    message: Message,
    state: FSMContext,
    db_manager: DatabaseManager,
) -> None:
    url = (message.text or "").strip()
    valid, err = InputValidator.validate_button_url(url)
    if not valid:
        await message.answer(f"⚠️ {err}\nأرسل رابطًا يبدأ بـ https:// :")
        return

    data = await state.get_data()
    camp_id = int(data["target_campaign_id"])
    btn_text = str(data["button_text"])

    async with db_manager.session() as session:
        session.add(
            ReplyButton(
                campaign_id=camp_id,
                text=btn_text,
                button_type="url",
                url=url,
                row_index=0,
                col_index=0,
            )
        )
    await state.clear()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 العودة لتفاصيل الحملة وإطلاقها",
                    callback_data=f"camp:view:{camp_id}",
                )
            ],
            build_back_and_home_row("sec:campaigns"),
        ]
    )
    await message.answer(
        text=f"✅ تم إرفاق الزر <b>{btn_text}</b> بالحملة #{camp_id} بنجاح.",
        reply_markup=kb,
        parse_mode="HTML",
    )


# --- استبدال حملة جارية (إبطال run_id القديم) ---
@broadcasts_router.callback_query(F.data.startswith("camp:replace:"))
async def cb_replace_campaign_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    camp_id = int((callback.data or "").split(":")[-1])
    await state.update_data(replace_campaign_id=camp_id)
    await state.set_state(CampaignWizardStates.waiting_for_replace_content)
    if callback.message:
        await callback.message.edit_text(
            text=(
                f"🔄 <b>استبدال الحملة #{camp_id} وإبطال التنفيذ القديم</b>\n"
                "أرسل الآن النص الجديد للحملة؛ سيقوم النظام فورًا بإيقاف وإبطال أي إرسال قديم (run_id) وبدء تنفيذ جديد بالمحتوى المحدث:"
            ),
            reply_markup=build_cancel_keyboard(f"camp:view:{camp_id}"),
            parse_mode="HTML",
        )
    await callback.answer()


@broadcasts_router.message(CampaignWizardStates.waiting_for_replace_content)
async def msg_replace_campaign_execute(
    message: Message,
    state: FSMContext,
    campaign_manager: CampaignManager,
    broadcast_manager: BroadcastManager,
) -> None:
    data = await state.get_data()
    camp_id = int(data["replace_campaign_id"])
    new_text = message.html_text or message.text or ""
    await state.clear()

    new_run = await campaign_manager.replace_campaign(
        campaign_id=camp_id,
        new_text_content=new_text,
    )
    asyncio.create_task(broadcast_manager.execute_campaign_run(camp_id, new_run.run_id))

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 متابعة تقدم الحملة الجديدة",
                    callback_data=f"camp:view:{camp_id}",
                )
            ],
            build_back_and_home_row("sec:campaigns"),
        ]
    )
    await message.answer(
        text=(
            f"✅ <b>تم استبدال الحملة #{camp_id} بنجاح!</b>\n"
            f"• تم إبطال التنفيذ السابق وتوليد معرف تشغيل جديد: <code>{new_run.run_id}</code>\n"
            "• يجري الآن البث في الخلفية بالمحتوى الجديد."
        ),
        reply_markup=kb,
        parse_mode="HTML",
    )


# --- إنشاء حملة جديدة مع اختيار التصنيف ---
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
async def msg_campaign_name(
    message: Message,
    state: FSMContext,
    group_manager: GroupManager,
) -> None:
    await state.update_data(name=(message.text or "").strip())
    cols = await group_manager.list_collections_with_counts()

    if cols:
        await state.set_state(CampaignWizardStates.waiting_for_collection)
        rows = [
            [
                InlineKeyboardButton(
                    text=f"🗂️ تصنيف: {c.name} ({cnt} مجموعة)",
                    callback_data=f"camp:pick_col:{c.id}",
                )
            ]
            for c, cnt in cols
        ]
        rows.append(
            [
                InlineKeyboardButton(
                    text="🌐 جميع المجموعات المؤهلة للنشر",
                    callback_data="camp:pick_col:all",
                )
            ]
        )
        rows.append(build_back_and_home_row("sec:campaigns"))
        await message.answer(
            text="اختر المجموعات المستهدفة لهذه الحملة:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    else:
        await state.update_data(collection_id=None)
        await state.set_state(CampaignWizardStates.waiting_for_content)
        await message.answer(
            text="📝 أرسل الآن محتوى رسالة البث (نص أو صورة/فيديو/ملف مع وصف) التي ستُنشر بهوية حسابك الشخصي:",
            reply_markup=build_cancel_keyboard("sec:campaigns"),
        )


@broadcasts_router.callback_query(
    CampaignWizardStates.waiting_for_collection,
    F.data.startswith("camp:pick_col:"),
)
async def cb_campaign_pick_collection(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    val = (callback.data or "").split(":")[-1]
    col_id = int(val) if val.isdigit() else None
    await state.update_data(collection_id=col_id)
    await state.set_state(CampaignWizardStates.waiting_for_content)
    if callback.message:
        await callback.message.edit_text(
            text="📝 أرسل الآن محتوى رسالة البث (نص أو صورة/فيديو/ملف مع وصف) التي ستُنشر بهوية حسابك الشخصي:",
            reply_markup=build_cancel_keyboard("sec:campaigns"),
        )
    await callback.answer()


@broadcasts_router.message(CampaignWizardStates.waiting_for_content)
async def msg_campaign_content(
    message: Message,
    state: FSMContext,
    bot: Bot,
    campaign_manager: CampaignManager,
    media_manager: MediaManager,
) -> None:
    data = await state.get_data()
    media_info = MediaManager.extract_media_info_from_message(message)
    media_ref_id = None
    content_type = "text"

    if media_info:
        content_type = media_info["media_type"]
        media_ref = await media_manager.register_media(
            media_type=media_info["media_type"],
            file_unique_id=media_info["file_unique_id"],
            bot_file_id=media_info["bot_file_id"],
            file_name=media_info["file_name"],
            mime_type=media_info["mime_type"],
            file_size=media_info["file_size"],
            bot=bot,
        )
        media_ref_id = media_ref.id

    camp = await campaign_manager.create_campaign(
        name=data["name"],
        content_type=content_type,
        text_content=message.html_text if (message.text or message.caption) else "",
        media_ref_id=media_ref_id,
        collection_id=data.get("collection_id"),
        schedule_type="immediate",
    )
    await state.clear()

    preview_text = (
        f"📢 <b>تأكيد إعداد الحملة: {camp.name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>طريقة الإرسال:</b> الحساب الشخصي (MTProto)\n"
        f"• <b>نوع المحتوى:</b> {content_type}\n"
        f"• <b>محتوى الرسالة:</b>\n{camp.text_content or '(مرفق وسائط)'}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "يمكنك إضافة زر تفاعلي للحملة أولًا أو بدء البث مباشرة:"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 تأكيد وبدء البث الآن",
                    callback_data=f"camp:launch:{camp.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔘 إضافة زر رابط للحملة قبل البث",
                    callback_data=f"camp:btn_add:{camp.id}",
                )
            ],
            build_back_and_home_row("sec:campaigns"),
        ]
    )
    await message.answer(text=preview_text, reply_markup=kb, parse_mode="HTML")


@broadcasts_router.callback_query(F.data.startswith("camp:launch:"))
async def cb_launch_campaign(
    callback: CallbackQuery,
    campaign_manager: CampaignManager,
    broadcast_manager: BroadcastManager,
    db_manager: DatabaseManager,
) -> None:
    """إطلاق الحملة في الخلفية مع منع الإطلاق المزدوج إذا كانت قيد التنفيذ بالفعل."""
    camp_id = int((callback.data or "").split(":")[-1])
    async with db_manager.session() as session:
        camp = await session.get(BroadcastCampaign, camp_id)
        if camp and camp.status == "running":
            await callback.answer("⚠️ الحملة قيد التنفيذ بالفعل في الخلفية!", show_alert=True)
            return
    run_obj = await campaign_manager.initiate_new_run(camp_id)

    asyncio.create_task(broadcast_manager.execute_campaign_run(camp_id, run_obj.run_id))

    await callback.answer(
        f"🚀 انطلق البث في الخلفية ({run_obj.total_targets} مجموعة)! يمكنك متابعة التقدم أو إيقافها من هنا.",
        show_alert=True,
    )
    cb_copy = callback.model_copy(update={"data": f"camp:view:{camp_id}"})
    await cb_view_campaign_details(cb_copy, db_manager)
