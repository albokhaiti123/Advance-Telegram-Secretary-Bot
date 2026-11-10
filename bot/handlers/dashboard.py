from typing import Tuple
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import func, select

from bot.keyboards.main_menu import (
    get_main_dashboard_keyboard,
    get_section_submenu_keyboard,
)
from database.database import DatabaseManager
from database.models import (
    AutoReplyRule,
    BroadcastCampaign,
    BusinessConnection,
    Conversation,
    Group,
    PersonalAccountSession,
    ScheduledTask,
    SystemError,
    SystemSetting,
)
from security.access_control import AccessControl

dashboard_router = Router(name="dashboard_router")

SECTION_TITLES = {
    "auto_reply": "1️⃣ قسم الردود التلقائية وأوضاع العمل",
    "rules": "2️⃣ قسم إدارة قواعد الرد التلقائي",
    "templates": "3️⃣ قسم القوالب والوسائط والأزرار",
    "conversations": "4️⃣ قسم إدارة المحادثات والردود المرسلة",
    "exceptions": "5️⃣ قسم إدارة الاستثناءات وقوائم التجاهل",
    "broadcasts": "6️⃣ قسم بث المجموعات وتصنيفاتها (Collections)",
    "campaigns": "7️⃣ قسم إدارة الحملات الإعلانية",
    "scheduler": "8️⃣ قسم المهام المجدولة والدورية",
    "statistics": "9️⃣ قسم إحصائيات النظام والتقارير",
    "logs": "🔟 قسم سجل النشاط والأخطاء",
    "account": "1️⃣1️⃣ قسم إعدادات الحساب والتكاملات (Business & MTProto)",
    "settings": "1️⃣2️⃣ قسم إعدادات النظام والنسخ الاحتياطي",
}


async def render_main_dashboard_text(db_manager: DatabaseManager) -> Tuple[str, bool]:
    """تجميع ملخص حي لحالة النظام لعرضه في رأس لوحة التحكم."""
    async with db_manager.session() as session:
        master_setting = await session.scalar(
            select(SystemSetting).where(
                SystemSetting.key == "auto_reply_master_enabled"
            )
        )
        auto_reply_enabled = (
            bool(master_setting.value_json.get("enabled", True))
            if master_setting
            else True
        )

        active_biz = await session.scalar(
            select(func.count())
            .select_from(BusinessConnection)
            .where(BusinessConnection.is_active.is_(True))
        )
        personal_auth = await session.scalar(
            select(func.count())
            .select_from(PersonalAccountSession)
            .where(PersonalAccountSession.is_authorized.is_(True))
        )
        active_rules = await session.scalar(
            select(func.count())
            .select_from(AutoReplyRule)
            .where(AutoReplyRule.is_active.is_(True))
        )
        total_groups = await session.scalar(
            select(func.count())
            .select_from(Group)
            .where(Group.is_active.is_(True))
        )
        running_campaigns = await session.scalar(
            select(func.count())
            .select_from(BroadcastCampaign)
            .where(BroadcastCampaign.status.in_(["running", "scheduled"]))
        )
        pending_tasks = await session.scalar(
            select(func.count())
            .select_from(ScheduledTask)
            .where(ScheduledTask.status == "pending")
        )
        unresolved_errors = await session.scalar(
            select(func.count())
            .select_from(SystemError)
            .where(SystemError.resolved.is_(False))
        )

    biz_badge = "🟢 متصل" if (active_biz or 0) > 0 else "🔴 غير متصل"
    personal_badge = "🟢 مفوض" if (personal_auth or 0) > 0 else "⚪ غير مربوط"
    reply_badge = "🟢 مفعل" if auto_reply_enabled else "🔴 متوقف"

    text = (
        "🤖 <b>لوحة التحكم الرئيسية — Telegram Secretary Bot</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>محرك الردود التلقائية:</b> {reply_badge}\n"
        f"• <b>اتصال Telegram Business:</b> {biz_badge}\n"
        f"• <b>جلسة الحساب الشخصي (MTProto):</b> {personal_badge}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>القواعد النشطة:</b> {active_rules or 0}\n"
        f"👥 <b>المجموعات المكتشفة:</b> {total_groups or 0}\n"
        f"📢 <b>الحملات الجارية/المجدولة:</b> {running_campaigns or 0}\n"
        f"⏳ <b>المهام المجدولة بالانتظار:</b> {pending_tasks or 0}\n"
        f"⚠️ <b>الأخطاء غير المعالجة:</b> {unresolved_errors or 0}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "اختر القسم المطلوب من الأزرار التفاعلية أدناه:"
    )
    return text, auto_reply_enabled


@dashboard_router.callback_query(F.data.in_({"nav:main", "nav:refresh_main", "nav:cancel"}))
async def cb_main_dashboard(
    callback: CallbackQuery,
    state: FSMContext,
    db_manager: DatabaseManager,
) -> None:
    """العودة إلى القائمة الرئيسية أو تحديث بياناتها."""
    await state.clear()
    text, auto_reply_enabled = await render_main_dashboard_text(db_manager)
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_main_dashboard_keyboard(auto_reply_enabled=auto_reply_enabled),
            parse_mode="HTML",
        )
    await callback.answer("تم تحديث اللوحة")


@dashboard_router.callback_query(F.data == "nav:noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@dashboard_router.callback_query(F.data.startswith("sec:"))
async def cb_open_section(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """فتح أي قسم من الأقسام الـ 12 وعرض قائمته الفرعية."""
    await state.clear()
    section_key = (callback.data or "").split(":", 1)[1]
    title = SECTION_TITLES.get(section_key, "قسم الإدارة")

    description = (
        f"📂 <b>{title}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "اختر الإجراء المطلوب من القائمة الفرعية:"
    )
    if callback.message:
        await callback.message.edit_text(
            text=description,
            reply_markup=get_section_submenu_keyboard(section_key),
            parse_mode="HTML",
        )
    await callback.answer()


@dashboard_router.callback_query(F.data == "ar:toggle_master")
async def cb_toggle_master_auto_reply(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
    access_control: AccessControl,
) -> None:
    """تفعيل أو تعطيل محرك الردود التلقائية العام بضغطة زر."""
    async with db_manager.session() as session:
        setting = await session.scalar(
            select(SystemSetting).where(
                SystemSetting.key == "auto_reply_master_enabled"
            )
        )
        current_state = bool(setting.value_json.get("enabled", True)) if setting else True
        new_state = not current_state
        if setting:
            setting.value_json = {"enabled": new_state}
        else:
            session.add(
                SystemSetting(
                    key="auto_reply_master_enabled",
                    value_json={"enabled": new_state},
                    description="التفعيل العام لمحرك الردود التلقائية",
                )
            )

    await access_control.log_admin_action(
        action="toggle_auto_reply_master",
        details={"enabled": new_state},
    )
    status_msg = "تم تفعيل الردود التلقائية 🟢" if new_state else "تم إيقاف الردود التلقائية 🔴"
    await callback.answer(status_msg, show_alert=True)

    text, auto_reply_enabled = await render_main_dashboard_text(db_manager)
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_main_dashboard_keyboard(auto_reply_enabled=auto_reply_enabled),
            parse_mode="HTML",
        )
