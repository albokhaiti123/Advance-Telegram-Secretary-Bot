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

from bot.keyboards.builders import build_templates_list_keyboard
from bot.keyboards.main_menu import get_section_submenu_keyboard
from bot.keyboards.navigation import (
    build_back_and_home_row,
    build_cancel_keyboard,
    build_confirm_cancel_keyboard,
    build_pagination_row,
)
from bot.states.admin_states import TemplateWizardStates
from database.database import DatabaseManager
from database.models import AutoReplyRule, MediaReference, ReplyButton, ReplyTemplate
from security.access_control import AccessControl
from security.validators import InputValidator
from services.media_manager import MediaManager
from services.template_manager import TemplateManager

templates_router = Router(name="templates_router")
MEDIA_PAGE_SIZE = 8


@templates_router.callback_query(F.data.startswith("tpl:list"))
async def cb_list_templates(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    async with db_manager.session() as session:
        templates = list(
            (
                await session.scalars(
                    select(ReplyTemplate).order_by(ReplyTemplate.id.desc())
                )
            ).all()
        )

    if callback.message:
        await callback.message.edit_text(
            text=(
                f"📂 <b>قوالب الردود المحفوظة ({len(templates)})</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "اختر قالبًا لعرض تفاصيله، معاينته، أو إدارة أزراره التفاعلية:"
            ),
            reply_markup=build_templates_list_keyboard(templates),
            parse_mode="HTML",
        )
    await callback.answer()


@templates_router.callback_query(F.data.startswith("tpl:view:"))
async def cb_view_template_details(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    tpl_id = int((callback.data or "").split(":")[-1])
    async with db_manager.session() as session:
        stmt = (
            select(ReplyTemplate)
            .options(
                selectinload(ReplyTemplate.buttons),
                selectinload(ReplyTemplate.media_ref),
            )
            .where(ReplyTemplate.id == tpl_id)
        )
        tpl = await session.scalar(stmt)

    if not tpl:
        await callback.answer("القالب غير موجود.", show_alert=True)
        return

    quick_badge = "⚡ نعم" if tpl.is_quick_reply else "لا"
    quick_btn_text = (
        "⚡ إزالة من الردود السريعة"
        if tpl.is_quick_reply
        else "⚡ تعيين كرد سريع"
    )
    buttons_count = len(tpl.buttons or [])
    media_name = tpl.media_ref.file_name if tpl.media_ref else "بدون مرفق"

    text = (
        f"📄 <b>تفاصيل القالب: {tpl.name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>نوع المحتوى:</b> <code>{tpl.content_type}</code>\n"
        f"• <b>الملف المرفق:</b> <code>{media_name}</code>\n"
        f"• <b>عدد الأزرار التفاعلية:</b> {buttons_count}\n"
        f"• <b>مصنف كرد سريع:</b> {quick_badge}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 <b>النص المحفوظ:</b>\n{tpl.text_content or '<i>(بدون نص)</i>'}"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👁️ إرسال معاينة حية",
                    callback_data=f"tpl:preview:{tpl.id}",
                ),
                InlineKeyboardButton(
                    text=f"🔘 إدارة الأزرار ({buttons_count})",
                    callback_data=f"tpl:btn_manage:{tpl.id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=quick_btn_text,
                    callback_data=f"tpl:toggle_quick:{tpl.id}",
                ),
                InlineKeyboardButton(
                    text="🗑️ حذف القالب",
                    callback_data=f"tpl:del_ask:{tpl.id}",
                ),
            ],
            build_back_and_home_row("tpl:list:1"),
        ]
    )

    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@templates_router.callback_query(F.data.startswith("tpl:toggle_quick:"))
async def cb_toggle_quick_reply_template(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
    access_control: AccessControl,
) -> None:
    tpl_id = int((callback.data or "").split(":")[-1])
    async with db_manager.session() as session:
        tpl = await session.get(ReplyTemplate, tpl_id)
        if not tpl:
            await callback.answer("القالب غير موجود.", show_alert=True)
            return
        tpl.is_quick_reply = not tpl.is_quick_reply
        new_state = tpl.is_quick_reply

    await access_control.log_admin_action(
        "toggle_template_quick_reply",
        target_id=str(tpl_id),
        details={"is_quick_reply": new_state},
    )
    await callback.answer(
        "تمت الإضافة إلى الردود السريعة ⚡"
        if new_state
        else "تمت الإزالة من الردود السريعة",
    )
    await cb_view_template_details(callback, db_manager)


@templates_router.callback_query(F.data.startswith("tpl:del_ask:"))
async def cb_ask_delete_template(callback: CallbackQuery) -> None:
    tpl_id = int((callback.data or "").split(":")[-1])
    if callback.message:
        await callback.message.edit_text(
            text="⚠️ <b>هل أنت متأكد من حذف هذا القالب؟</b>\n(لا يمكن حذف قالب مرتبط بقاعدة نشطة إلا بعد حذف القاعدة أو تغيير قالبها)",
            reply_markup=build_confirm_cancel_keyboard(
                confirm_callback=f"tpl:del_confirm:{tpl_id}",
                cancel_callback=f"tpl:view:{tpl_id}",
            ),
            parse_mode="HTML",
        )
    await callback.answer()


@templates_router.callback_query(F.data.startswith("tpl:del_confirm:"))
async def cb_confirm_delete_template(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
    access_control: AccessControl,
) -> None:
    tpl_id = int((callback.data or "").split(":")[-1])
    async with db_manager.session() as session:
        linked_rules = await session.scalar(
            select(func.count())
            .select_from(AutoReplyRule)
            .where(AutoReplyRule.template_id == tpl_id)
        )
        if (linked_rules or 0) > 0:
            await callback.answer(
                f"⛔ لا يمكن حذف القالب لأنه مستخدم في ({linked_rules}) قاعدة. احذف القاعدة المرتبطة أولًا.",
                show_alert=True,
            )
            return

        tpl = await session.get(ReplyTemplate, tpl_id)
        if tpl:
            await session.delete(tpl)

    await access_control.log_admin_action("delete_template", target_id=str(tpl_id))
    await callback.answer("تم حذف القالب بنجاح ✅", show_alert=True)
    await cb_list_templates(callback, db_manager)


@templates_router.callback_query(F.data == "tpl:add")
async def cb_add_template_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TemplateWizardStates.waiting_for_name)
    if callback.message:
        await callback.message.edit_text(
            text="➕ <b>إنشاء قالب جديد</b>\nأرسل اسم القالب (مثال: قالب الترحيب الرسمي):",
            reply_markup=build_cancel_keyboard("sec:templates"),
            parse_mode="HTML",
        )
    await callback.answer()


@templates_router.message(TemplateWizardStates.waiting_for_name)
async def msg_template_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("⚠️ يرجى إدخال اسم صالح للقالب (حرفان على الأقل).")
        return
    await state.update_data(name=name)
    await state.set_state(TemplateWizardStates.waiting_for_content)
    await message.answer(
        text=(
            "📝 أرسل الآن محتوى القالب (نص عادي، نص منسق، أو صورة/فيديو/مستند/صوت مع وصف).\n\n"
            "💡 <b>المتغيرات المدعومة:</b>\n"
            "<code>{first_name}</code> ، <code>{full_name}</code> ، <code>{username}</code> ، <code>{date}</code> ، <code>{time}</code> ، <code>{weekday}</code>"
        ),
        reply_markup=build_cancel_keyboard("sec:templates"),
        parse_mode="HTML",
    )


@templates_router.message(TemplateWizardStates.waiting_for_content)
async def msg_template_content(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db_manager: DatabaseManager,
    media_manager: MediaManager,
    template_manager: TemplateManager,
    access_control: AccessControl,
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

    text_content = message.html_text if (message.text or message.caption) else None
    tpl = await template_manager.create_template(
        name=data["name"],
        content_type=content_type,
        text_content=text_content,
        media_ref_id=media_ref_id,
    )
    await state.clear()
    await access_control.log_admin_action("create_template", target_id=str(tpl.id))

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔘 إضافة أزرار تفاعلية لهذا القالب",
                    callback_data=f"tpl:btn_manage:{tpl.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👁️ معاينة القالب",
                    callback_data=f"tpl:preview:{tpl.id}",
                )
            ],
            build_back_and_home_row("sec:templates"),
        ]
    )
    await message.answer(
        text=f"✅ تم حفظ القالب <b>{tpl.name}</b> بنجاح.\nهل ترغب في إضافة أزرار تفاعلية له الآن؟",
        reply_markup=kb,
        parse_mode="HTML",
    )


@templates_router.callback_query(F.data.startswith("tpl:preview:"))
async def cb_preview_template(
    callback: CallbackQuery,
    bot: Bot,
    template_manager: TemplateManager,
) -> None:
    tpl_id = int((callback.data or "").split(":")[-1])
    sample_ctx = {
        "first_name": callback.from_user.first_name or "ربيع",
        "username": callback.from_user.username or "albokhaiti",
        "user_id": callback.from_user.id,
    }
    payload = await template_manager.build_rendered_payload(tpl_id, context=sample_ctx)
    if not payload:
        await callback.answer("القالب غير موجود.", show_alert=True)
        return

    chat_id = callback.from_user.id
    if payload.content_type == "text":
        await bot.send_message(
            chat_id=chat_id,
            text=payload.text,
            parse_mode=payload.parse_mode,
            reply_markup=payload.reply_markup,
        )
    elif payload.content_type == "photo" and payload.bot_file_id:
        await bot.send_photo(
            chat_id=chat_id,
            photo=payload.bot_file_id,
            caption=payload.text,
            parse_mode=payload.parse_mode,
            reply_markup=payload.reply_markup,
        )
    elif payload.content_type == "video" and payload.bot_file_id:
        await bot.send_video(
            chat_id=chat_id,
            video=payload.bot_file_id,
            caption=payload.text,
            parse_mode=payload.parse_mode,
            reply_markup=payload.reply_markup,
        )
    elif payload.content_type == "document" and payload.bot_file_id:
        await bot.send_document(
            chat_id=chat_id,
            document=payload.bot_file_id,
            caption=payload.text,
            parse_mode=payload.parse_mode,
            reply_markup=payload.reply_markup,
        )
    elif payload.content_type == "voice" and payload.bot_file_id:
        await bot.send_voice(
            chat_id=chat_id,
            voice=payload.bot_file_id,
            caption=payload.text,
            parse_mode=payload.parse_mode,
            reply_markup=payload.reply_markup,
        )
    elif payload.content_type == "audio" and payload.bot_file_id:
        await bot.send_audio(
            chat_id=chat_id,
            audio=payload.bot_file_id,
            caption=payload.text,
            parse_mode=payload.parse_mode,
            reply_markup=payload.reply_markup,
        )
    elif payload.content_type == "sticker" and payload.bot_file_id:
        await bot.send_sticker(
            chat_id=chat_id,
            sticker=payload.bot_file_id,
            reply_markup=payload.reply_markup,
        )
    else:
        await bot.send_message(
            chat_id=chat_id,
            text=f"👁️ <b>معاينة القالب ({payload.content_type}):</b>\n\n{payload.text}",
            parse_mode=payload.parse_mode,
            reply_markup=payload.reply_markup,
        )

    await callback.answer("تم إرسال المعاينة الحية للقالب ✅")


@templates_router.callback_query(F.data == "tpl:buttons")
async def cb_select_template_for_buttons(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    async with db_manager.session() as session:
        stmt = (
            select(ReplyTemplate)
            .options(selectinload(ReplyTemplate.buttons))
            .order_by(ReplyTemplate.id.desc())
        )
        templates = list((await session.scalars(stmt)).all())

    rows = []
    for tpl in templates:
        btn_cnt = len(tpl.buttons or [])
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔘 {tpl.name} ({btn_cnt} أزرار)",
                    callback_data=f"tpl:btn_manage:{tpl.id}",
                )
            ]
        )

    if not templates:
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ إنشاء قالب جديد أولًا",
                    callback_data="tpl:add",
                )
            ]
        )
    rows.append(build_back_and_home_row("sec:templates"))

    text = (
        "🔘 <b>إدارة الأزرار التفاعلية (Inline Buttons)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "اختر القالب الذي تريد إضافة أو تعديل أو حذف أزراره:"
    )
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await callback.answer()


@templates_router.callback_query(F.data.startswith("tpl:btn_manage:"))
async def cb_manage_template_buttons(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    tpl_id = int((callback.data or "").split(":")[-1])
    async with db_manager.session() as session:
        stmt = (
            select(ReplyTemplate)
            .options(selectinload(ReplyTemplate.buttons))
            .where(ReplyTemplate.id == tpl_id)
        )
        tpl = await session.scalar(stmt)

    if not tpl:
        await callback.answer("القالب غير موجود.", show_alert=True)
        return

    buttons = sorted(
        tpl.buttons or [], key=lambda b: (b.row_index, b.col_index, b.id or 0)
    )
    rows = []
    lines = []

    for idx, btn in enumerate(buttons, start=1):
        target_info = btn.url if btn.button_type == "url" else btn.callback_data
        lines.append(f"{idx}. <b>{btn.text}</b> ⬅️ <code>{target_info}</code>")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🗑️ حذف الزر: {btn.text}",
                    callback_data=f"tpl:btn_del:{tpl.id}:{btn.id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="➕ إضافة زر رابط جديد (URL)",
                callback_data=f"tpl:btn_add:{tpl.id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="👁️ معاينة القالب بالأزرار",
                callback_data=f"tpl:preview:{tpl.id}",
            )
        ]
    )
    rows.append(build_back_and_home_row(f"tpl:view:{tpl.id}"))

    body = "\n".join(lines) if lines else "لا توجد أزرار مضافة لهذا القالب حاليًا."
    text = (
        f"🔘 <b>إدارة أزرار القالب: {tpl.name}</b>\n"
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


@templates_router.callback_query(F.data.startswith("tpl:btn_add:"))
async def cb_add_button_start(callback: CallbackQuery, state: FSMContext) -> None:
    tpl_id = int((callback.data or "").split(":")[-1])
    await state.update_data(target_template_id=tpl_id)
    await state.set_state(TemplateWizardStates.waiting_for_button_text)

    if callback.message:
        await callback.message.edit_text(
            text="➕ <b>إضافة زر تفاعلي جديد</b>\nأرسل الآن النص الذي سيظهر على الزر (مثال: 🌐 زيارة موقعنا أو 💬 تواصل عبر واتساب):",
            reply_markup=build_cancel_keyboard(f"tpl:btn_manage:{tpl_id}"),
            parse_mode="HTML",
        )
    await callback.answer()


@templates_router.message(TemplateWizardStates.waiting_for_button_text)
async def msg_button_text_received(message: Message, state: FSMContext) -> None:
    btn_text = (message.text or "").strip()
    if not btn_text or len(btn_text) > 64:
        await message.answer("⚠️ يرجى إدخال نص للزر لا يتجاوز 64 حرفًا:")
        return

    data = await state.get_data()
    tpl_id = data.get("target_template_id")
    await state.update_data(button_text=btn_text)
    await state.set_state(TemplateWizardStates.waiting_for_button_url)

    await message.answer(
        text="🔗 أرسل الآن الرابط الكامل للزر (يجب أن يبدأ بـ <code>https://</code> أو <code>tg://</code>):",
        reply_markup=build_cancel_keyboard(f"tpl:btn_manage:{tpl_id}"),
        parse_mode="HTML",
    )


@templates_router.message(TemplateWizardStates.waiting_for_button_url)
async def msg_button_url_received(
    message: Message,
    state: FSMContext,
    db_manager: DatabaseManager,
    template_manager: TemplateManager,
    access_control: AccessControl,
) -> None:
    url = (message.text or "").strip()
    is_valid, err_msg = InputValidator.validate_button_url(url)
    if not is_valid:
        await message.answer(f"⚠️ {err_msg}\nيرجى إرسال رابط صحيح يبدأ بـ https:// :")
        return

    data = await state.get_data()
    tpl_id = int(data["target_template_id"])
    btn_text = str(data["button_text"])

    async with db_manager.session() as session:
        existing_count = (
            await session.scalar(
                select(func.count())
                .select_from(ReplyButton)
                .where(ReplyButton.template_id == tpl_id)
            )
            or 0
        )

    btn = await template_manager.add_button_to_template(
        template_id=tpl_id,
        text=btn_text,
        button_type="url",
        url=url,
        row_index=int(existing_count),
        col_index=0,
    )
    await state.clear()
    await access_control.log_admin_action(
        "add_template_button",
        target_id=str(tpl_id),
        details={"button_id": btn.id, "text": btn_text},
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔘 العودة لإدارة أزرار القالب",
                    callback_data=f"tpl:btn_manage:{tpl_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👁️ معاينة القالب الآن",
                    callback_data=f"tpl:preview:{tpl.id}",
                )
            ],
            build_back_and_home_row("sec:templates"),
        ]
    )
    await message.answer(
        text=f"✅ تم إضافة الزر <b>{btn_text}</b> إلى القالب بنجاح.",
        reply_markup=kb,
        parse_mode="HTML",
    )


@templates_router.callback_query(F.data.startswith("tpl:btn_del:"))
async def cb_delete_template_button(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
    template_manager: TemplateManager,
    access_control: AccessControl,
) -> None:
    parts = (callback.data or "").split(":")
    tpl_id, btn_id = int(parts[2]), int(parts[3])

    await template_manager.delete_button(btn_id)
    await access_control.log_admin_action(
        "delete_template_button",
        target_id=str(tpl_id),
        details={"button_id": btn_id},
    )
    await callback.answer("تم حذف الزر بنجاح 🗑️")
    callback_copy = callback.model_copy(update={"data": f"tpl:btn_manage:{tpl_id}"})
    await cb_manage_template_buttons(callback_copy, db_manager)


@templates_router.callback_query(F.data.startswith("tpl:media"))
async def cb_list_media_library(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    parts = (callback.data or "tpl:media:1").split(":")
    page = int(parts[-1]) if parts[-1].isdigit() else 1

    async with db_manager.session() as session:
        total = (
            await session.scalar(select(func.count()).select_from(MediaReference))
            or 0
        )
        total_pages = max(1, math.ceil(total / MEDIA_PAGE_SIZE))
        stmt = (
            select(MediaReference)
            .order_by(MediaReference.id.desc())
            .offset((page - 1) * MEDIA_PAGE_SIZE)
            .limit(MEDIA_PAGE_SIZE)
        )
        media_items = list((await session.scalars(stmt)).all())

    rows = []
    for item in media_items:
        size_kb = round((item.file_size or 0) / 1024, 1) if item.file_size else 0
        label = item.file_name or f"{item.media_type}_{item.id}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🖼️ [{item.media_type}] {label} ({size_kb} KB)",
                    callback_data=f"tpl:mview:{item.id}",
                )
            ]
        )

    pag = build_pagination_row("tpl:media", page, total_pages)
    if pag:
        rows.append(pag)
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ إضافة وسيط عبر قالب جديد",
                callback_data="tpl:add",
            )
        ]
    )
    rows.append(build_back_and_home_row("sec:templates"))

    text = (
        f"🖼️ <b>مكتبة الوسائط المحفوظة ({total})</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "تُحفظ هنا جميع الصور والفيديوهات والمستندات والصوتيات المرفقة بالقوالب:"
    )
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await callback.answer()


@templates_router.callback_query(F.data.startswith("tpl:mview:"))
async def cb_view_media_item(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    media_id = int((callback.data or "").split(":")[-1])
    async with db_manager.session() as session:
        media = await session.get(MediaReference, media_id)

    if not media:
        await callback.answer("الوسيط غير موجود.", show_alert=True)
        return

    size_kb = round((media.file_size or 0) / 1024, 1) if media.file_size else 0
    has_local = "✅ متوفر للبث الشخصي" if media.local_file_path else "⚪ عبر Bot API فقط"

    text = (
        f"🖼️ <b>تفاصيل الوسيط #{media.id}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>الاسم:</b> <code>{media.file_name or '—'}</code>\n"
        f"• <b>النوع:</b> <code>{media.media_type}</code>\n"
        f"• <b>الحجم:</b> {size_kb} KB\n"
        f"• <b>التخزين المحلي (MTProto):</b> {has_local}"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👁️ إرسال الملف للمعاينة",
                    callback_data=f"tpl:msend:{media.id}",
                ),
                InlineKeyboardButton(
                    text="🗑️ حذف من المكتبة",
                    callback_data=f"tpl:mdel:{media.id}",
                ),
            ],
            build_back_and_home_row("tpl:media:1"),
        ]
    )
    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@templates_router.callback_query(F.data.startswith("tpl:msend:"))
async def cb_send_media_preview(
    callback: CallbackQuery,
    bot: Bot,
    db_manager: DatabaseManager,
) -> None:
    media_id = int((callback.data or "").split(":")[-1])
    async with db_manager.session() as session:
        media = await session.get(MediaReference, media_id)

    if not media:
        await callback.answer("الوسيط غير موجود.", show_alert=True)
        return

    chat_id = callback.from_user.id
    mtype = media.media_type
    fid = media.bot_file_id

    if mtype == "photo":
        await bot.send_photo(chat_id=chat_id, photo=fid, caption=media.file_name)
    elif mtype == "video":
        await bot.send_video(chat_id=chat_id, video=fid, caption=media.file_name)
    elif mtype == "document":
        await bot.send_document(chat_id=chat_id, document=fid, caption=media.file_name)
    elif mtype == "voice":
        await bot.send_voice(chat_id=chat_id, voice=fid)
    elif mtype == "audio":
        await bot.send_audio(chat_id=chat_id, audio=fid, caption=media.file_name)
    elif mtype == "sticker":
        await bot.send_sticker(chat_id=chat_id, sticker=fid)

    await callback.answer("تم إرسال الوسيط للمعاينة ✅")


@templates_router.callback_query(F.data.startswith("tpl:mdel:"))
async def cb_delete_media_item(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
    access_control: AccessControl,
) -> None:
    media_id = int((callback.data or "").split(":")[-1])
    async with db_manager.session() as session:
        used_in_tpl = await session.scalar(
            select(func.count())
            .select_from(ReplyTemplate)
            .where(ReplyTemplate.media_ref_id == media_id)
        )
        if (used_in_tpl or 0) > 0:
            await callback.answer(
                f"⛔ هذا الوسيط مستخدم في ({used_in_tpl}) قالب. احذف القالب المرتبط أولًا.",
                show_alert=True,
            )
            return

        media = await session.get(MediaReference, media_id)
        if media:
            await session.delete(media)

    await access_control.log_admin_action("delete_media_reference", target_id=str(media_id))
    await callback.answer("تم حذف الوسيط من المكتبة ✅", show_alert=True)
    await cb_list_media_library(callback, db_manager)
