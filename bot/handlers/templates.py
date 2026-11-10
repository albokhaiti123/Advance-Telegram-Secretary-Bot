from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.keyboards.builders import build_templates_list_keyboard
from bot.keyboards.main_menu import get_section_submenu_keyboard
from bot.keyboards.navigation import build_cancel_keyboard
from bot.states.admin_states import TemplateWizardStates
from database.database import DatabaseManager
from database.models import ReplyTemplate
from security.access_control import AccessControl
from services.media_manager import MediaManager
from services.template_manager import TemplateManager

templates_router = Router(name="templates_router")


@templates_router.callback_query(F.data.startswith("tpl:list"))
async def cb_list_templates(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    async with db_manager.session() as session:
        templates = list((await session.scalars(select(ReplyTemplate))).all())

    if callback.message:
        await callback.message.edit_text(
            text=f"📂 <b>قوالب الردود المحفوظة ({len(templates)})</b>\nاختر قالبًا لمعاينته أو إدارته:",
            reply_markup=build_templates_list_keyboard(templates),
            parse_mode="HTML",
        )
    await callback.answer()


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
        await message.answer("⚠️ يرجى إدخال اسم صالح للقالب.")
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

    await message.answer(
        text=f"✅ تم حفظ القالب <b>{tpl.name}</b> بنجاح.",
        reply_markup=get_section_submenu_keyboard("templates"),
        parse_mode="HTML",
    )


@templates_router.callback_query(F.data.startswith("tpl:preview:"))
@templates_router.callback_query(F.data.startswith("tpl:view:"))
async def cb_preview_template(
    callback: CallbackQuery,
    bot: Bot,
    template_manager: TemplateManager,
) -> None:
    """معاينة الشكل النهائي للقالب مع تعويض المتغيرات والأزرار التفاعلية."""
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
    else:
        await bot.send_message(
            chat_id=chat_id,
            text=f"👁️ <b>معاينة القالب ({payload.content_type}):</b>\n\n{payload.text}",
            parse_mode=payload.parse_mode,
            reply_markup=payload.reply_markup,
        )

    await callback.answer("تم إرسال المعاينة الحية للقالب ✅")
