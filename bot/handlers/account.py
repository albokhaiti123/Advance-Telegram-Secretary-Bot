from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.main_menu import get_section_submenu_keyboard
from bot.keyboards.navigation import build_cancel_keyboard, build_confirm_cancel_keyboard
from bot.states.admin_states import PersonalAuthStates
from database.database import DatabaseManager
from integrations.business.permissions import BusinessPermissionChecker
from integrations.personal.account_monitor import PersonalAccountMonitor
from integrations.personal.authorization import PersonalAuthService

account_router = Router(name="account_router")


@account_router.callback_query(F.data.in_({"acc:business_status", "acc:business_check"}))
async def cb_business_status(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    checker = BusinessPermissionChecker(db_manager)
    summary = await checker.get_active_connection_summary()

    if summary is None:
        text = (
            "💼 <b>حالة اتصال Telegram Business</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔴 <b>الحالة:</b> غير متصل حاليًا.\n\n"
            "📌 للربط: إعدادات تيليجرام ⟵ Telegram Business ⟵ Chatbots ⟵ أضف معرف البوت وفعّل صلاحية الرد وحذف الرسائل الصادرة."
        )
    else:
        ready_badge = "🟢 جاهز بالكامل" if summary.is_ready_for_secretary else "🟡 صلاحيات ناقصة"
        text = (
            "💼 <b>حالة اتصال Telegram Business</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>معرف الاتصال:</b> <code>{summary.connection_id}</code>\n"
            f"• <b>حالة الاتصال:</b> {'🟢 نشط' if summary.is_active else '🔴 متوقف'}\n"
            f"• <b>جاهزية السكرتير:</b> {ready_badge}\n"
            f"• <b>الرد على الرسائل:</b> {'✅' if summary.can_reply else '❌'}\n"
            f"• <b>حذف الرسائل الصادرة:</b> {'✅' if summary.can_delete_outgoing_messages else '❌'}"
        )

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_section_submenu_keyboard("account"),
            parse_mode="HTML",
        )
    await callback.answer()


@account_router.callback_query(F.data == "acc:personal_status")
async def cb_personal_status(
    callback: CallbackQuery,
    personal_monitor: PersonalAccountMonitor,
) -> None:
    health = await personal_monitor.verify_session_health()
    status_badge = "🟢 متصل ومفوض" if health.get("is_authorized") else "🔴 غير متصل"
    text = (
        "👤 <b>حالة جلسة الحساب الشخصي (MTProto)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>الحالة:</b> {status_badge}\n"
        f"• <b>الرقم المربوط:</b> <code>{health.get('phone_masked', '—')}</code>\n"
        f"• <b>المعرف (@):</b> {('@' + str(health['username'])) if health.get('username') else '—'}\n"
        f"• <b>التفاصيل:</b> {health.get('status_message', '—')}"
    )
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_section_submenu_keyboard("account"),
            parse_mode="HTML",
        )
    await callback.answer()


@account_router.callback_query(F.data == "acc:personal_login")
async def cb_personal_login_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PersonalAuthStates.waiting_for_phone)
    if callback.message:
        await callback.message.edit_text(
            text=(
                "🔐 <b>تفويض الحساب الشخصي بأمان</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "أرسل رقم الهاتف المرتبط بحسابك الشخصي بالصيغة الدولية (مثال: <code>+96777xxxxxxx</code>):"
            ),
            reply_markup=build_cancel_keyboard("sec:account"),
            parse_mode="HTML",
        )
    await callback.answer()


@account_router.message(PersonalAuthStates.waiting_for_phone)
async def msg_personal_phone(
    message: Message,
    state: FSMContext,
    personal_auth_service: PersonalAuthService,
) -> None:
    res = await personal_auth_service.start_login(message.text or "")
    if res.status == "code_sent":
        await state.set_state(PersonalAuthStates.waiting_for_code)
        await message.answer(
            text=(
                f"📲 {res.message}\n\n"
                "⚠️ <b>ملاحظة أمنية:</b> أرسل رمز التحقق مفصولًا بمسافة أو شرطة (مثال: <code>1 2 3 4 5</code>) وسيقوم البوت بحذف رسالتك فورًا لحمايتها."
            ),
            reply_markup=build_cancel_keyboard("sec:account"),
            parse_mode="HTML",
        )
    else:
        await state.clear()
        await message.answer(
            text=f"❌ {res.message}",
            reply_markup=get_section_submenu_keyboard("account"),
        )


@account_router.message(PersonalAuthStates.waiting_for_code)
async def msg_personal_code(
    message: Message,
    state: FSMContext,
    personal_auth_service: PersonalAuthService,
) -> None:
    code_text = message.text or ""
    # حذف رسالة الرمز السري من المحادثة فورًا لحماية الحساب
    try:
        await message.delete()
    except Exception:
        pass

    res = await personal_auth_service.submit_code(code_text)
    if res.status == "authorized":
        await state.clear()
        await message.answer(
            text=f"✅ {res.message}",
            reply_markup=get_section_submenu_keyboard("account"),
        )
    elif res.status == "need_2fa":
        await state.set_state(PersonalAuthStates.waiting_for_2fa_password)
        await message.answer(
            text=f"🔑 {res.message}\n(سيتم حذف رسالتك تلقائيًا فور استلامها)",
            reply_markup=build_cancel_keyboard("sec:account"),
        )
    else:
        await message.answer(
            text=f"⚠️ {res.message}",
            reply_markup=build_cancel_keyboard("sec:account"),
        )


@account_router.message(PersonalAuthStates.waiting_for_2fa_password)
async def msg_personal_2fa(
    message: Message,
    state: FSMContext,
    personal_auth_service: PersonalAuthService,
) -> None:
    pwd = message.text or ""
    try:
        await message.delete()
    except Exception:
        pass

    res = await personal_auth_service.submit_2fa_password(pwd)
    await state.clear()
    await message.answer(
        text=f"{'✅' if res.status == 'authorized' else '❌'} {res.message}",
        reply_markup=get_section_submenu_keyboard("account"),
    )


@account_router.callback_query(F.data == "acc:personal_logout_ask")
async def cb_personal_logout_ask(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.edit_text(
            text="⚠️ <b>هل أنت متأكد من فصل جلسة الحساب الشخصي وحذفها من النظام؟</b>",
            reply_markup=build_confirm_cancel_keyboard(
                confirm_callback="acc:personal_logout_confirm",
                cancel_callback="sec:account",
            ),
            parse_mode="HTML",
        )
    await callback.answer()


@account_router.callback_query(F.data == "acc:personal_logout_confirm")
async def cb_personal_logout_confirm(
    callback: CallbackQuery,
    personal_auth_service: PersonalAuthService,
) -> None:
    await personal_auth_service.logout_and_clear_session()
    if callback.message:
        await callback.message.edit_text(
            text="✅ تم فصل جلسة الحساب الشخصي وحذف بياناتها المشفرة بأمان.",
            reply_markup=get_section_submenu_keyboard("account"),
        )
    await callback.answer()
