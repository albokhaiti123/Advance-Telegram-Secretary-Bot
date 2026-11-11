from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from bot.keyboards.main_menu import get_section_submenu_keyboard
from bot.keyboards.navigation import build_back_and_home_row
from database.database import DatabaseManager
from database.models import ReplyTemplate, SystemSetting
from security.access_control import AccessControl
from services.message_processor import MessageProcessor
from utils.helpers import DatabaseBackupManager

settings_router = Router(name="settings_router")


@settings_router.callback_query(F.data == "ar:work_hours")
async def cb_work_hours_settings(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    async with db_manager.session() as session:
        setting = await session.scalar(
            select(SystemSetting).where(SystemSetting.key == "work_hours")
        )
        cfg = setting.value_json if setting else {}

    enabled = bool(cfg.get("enabled", False))
    start_t = cfg.get("start_time", "09:00")
    end_t = cfg.get("end_time", "17:00")
    tz = cfg.get("timezone", "Asia/Aden")

    text = (
        "🕒 <b>إعدادات ساعات العمل الرسمية</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>الحالة:</b> {'🟢 مفعلة' if enabled else '⚪ معطلة (عمل على مدار الساعة)'}\n"
        f"• <b>بداية الدوام:</b> {start_t}\n"
        f"• <b>نهاية الدوام:</b> {end_t}\n"
        f"• <b>المنطقة الزمنية:</b> <code>{tz}</code>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏯️ تفعيل / تعطيل جدول ساعات العمل",
                    callback_data="ar:toggle_work_hours",
                )
            ],
            build_back_and_home_row("sec:auto_reply"),
        ]
    )
    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@settings_router.callback_query(F.data == "ar:toggle_work_hours")
async def cb_toggle_work_hours(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
    access_control: AccessControl,
) -> None:
    async with db_manager.session() as session:
        setting = await session.scalar(
            select(SystemSetting).where(SystemSetting.key == "work_hours")
        )
        if setting:
            cfg = dict(setting.value_json or {})
            cfg["enabled"] = not bool(cfg.get("enabled", False))
            setting.value_json = cfg
            new_state = cfg["enabled"]
        else:
            new_state = True
            session.add(
                SystemSetting(
                    key="work_hours",
                    value_json={
                        "enabled": True,
                        "timezone": "Asia/Aden",
                        "days": [0, 1, 2, 3, 4, 6],
                        "start_time": "09:00",
                        "end_time": "17:00",
                    },
                )
            )
    await access_control.log_admin_action(
        "toggle_work_hours", details={"enabled": new_state}
    )
    await cb_work_hours_settings(callback, db_manager)


@settings_router.callback_query(F.data == "ar:away_mode")
async def cb_away_mode_settings(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    async with db_manager.session() as session:
        setting = await session.scalar(
            select(SystemSetting).where(SystemSetting.key == "away_mode")
        )
        cfg = setting.value_json if setting else {}
        templates = list((await session.scalars(select(ReplyTemplate))).all())

    enabled = bool(cfg.get("enabled", False))
    cooldown = cfg.get("cooldown_seconds", 3600)
    tpl_id = cfg.get("template_id")

    rows = [
        [
            InlineKeyboardButton(
                text="⏯️ تفعيل / إيقاف وضع الغياب",
                callback_data="ar:toggle_away_mode",
            )
        ]
    ]
    for t in templates[:6]:
        mark = "✅ " if t.id == tpl_id else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark}اختيار القالب: {t.name}",
                    callback_data=f"ar:away_tpl:{t.id}",
                )
            ]
        )
    rows.append(build_back_and_home_row("sec:auto_reply"))

    text = (
        "🌙 <b>إعدادات وضع الغياب (Away Mode)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>الحالة:</b> {'🟢 مفعل' if enabled else '🔴 متوقف'}\n"
        f"• <b>القالب المخصص:</b> {f'#{tpl_id}' if tpl_id else 'غير محدد (اختر من الأسفل)'}\n"
        f"• <b>منع التكرار (Cooldown):</b> كل {cooldown} ثانية"
    )
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await callback.answer()


@settings_router.callback_query(F.data.startswith("ar:away_tpl:"))
async def cb_set_away_template(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    tpl_id = int((callback.data or "").split(":")[-1])
    async with db_manager.session() as session:
        setting = await session.scalar(
            select(SystemSetting).where(SystemSetting.key == "away_mode")
        )
        if setting:
            cfg = dict(setting.value_json or {})
            cfg["template_id"] = tpl_id
            setting.value_json = cfg
    await callback.answer("تم تحديد قالب الغياب بنجاح ✅")
    await cb_away_mode_settings(callback, db_manager)


@settings_router.callback_query(F.data == "ar:toggle_away_mode")
async def cb_toggle_away_mode(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
    access_control: AccessControl,
) -> None:
    async with db_manager.session() as session:
        setting = await session.scalar(
            select(SystemSetting).where(SystemSetting.key == "away_mode")
        )
        if setting:
            cfg = dict(setting.value_json or {})
            cfg["enabled"] = not bool(cfg.get("enabled", False))
            setting.value_json = cfg
            new_state = cfg["enabled"]
        else:
            new_state = True
            session.add(
                SystemSetting(
                    key="away_mode",
                    value_json={"enabled": True, "cooldown_seconds": 3600},
                )
            )
    await access_control.log_admin_action(
        "toggle_away_mode", details={"enabled": new_state}
    )
    await cb_away_mode_settings(callback, db_manager)


# --- إعدادات رسالة الترحيب (ar:greeting) ---
@settings_router.callback_query(F.data == "ar:greeting")
async def cb_greeting_settings(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    """إعداد رسالة الترحيب التلقائية واختيار القالب الخاص بها."""
    async with db_manager.session() as session:
        setting = await session.scalar(
            select(SystemSetting).where(SystemSetting.key == "greeting_message")
        )
        cfg = setting.value_json if setting else {}
        templates = list((await session.scalars(select(ReplyTemplate))).all())

    enabled = bool(cfg.get("enabled", False))
    tpl_id = cfg.get("template_id")
    cooldown_days = cfg.get("cooldown_days", 30)

    rows = [
        [
            InlineKeyboardButton(
                text="⏯️ تفعيل / إيقاف رسالة الترحيب",
                callback_data="ar:toggle_greeting",
            )
        ]
    ]
    for t in templates[:6]:
        mark = "✅ " if t.id == tpl_id else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark}اختيار قالب الترحيب: {t.name}",
                    callback_data=f"ar:greet_tpl:{t.id}",
                )
            ]
        )
    rows.append(build_back_and_home_row("sec:auto_reply"))

    text = (
        "👋 <b>إعدادات رسالة الترحيب التلقائية</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>الحالة:</b> {'🟢 مفعلة' if enabled else '🔴 متوقفة'}\n"
        f"• <b>القالب المستخدم:</b> {f'#{tpl_id}' if tpl_id else 'غير محدد (اختر قالبًا بالأسفل)'}\n"
        f"• <b>فترة عدم التكرار:</b> {cooldown_days} يومًا\n\n"
        "تُرسل هذه الرسالة تلقائيًا للعملاء الذين يراسلونك لأول مرة أو بعد انقطاع طويل."
    )
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await callback.answer()


@settings_router.callback_query(F.data == "ar:toggle_greeting")
async def cb_toggle_greeting(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
    access_control: AccessControl,
) -> None:
    async with db_manager.session() as session:
        setting = await session.scalar(
            select(SystemSetting).where(SystemSetting.key == "greeting_message")
        )
        if setting:
            cfg = dict(setting.value_json or {})
            cfg["enabled"] = not bool(cfg.get("enabled", False))
            setting.value_json = cfg
            new_state = cfg["enabled"]
        else:
            new_state = True
            session.add(
                SystemSetting(
                    key="greeting_message",
                    value_json={"enabled": True, "template_id": None, "cooldown_days": 30},
                )
            )
    await access_control.log_admin_action(
        "toggle_greeting_message", details={"enabled": new_state}
    )
    await cb_greeting_settings(callback, db_manager)


@settings_router.callback_query(F.data.startswith("ar:greet_tpl:"))
async def cb_set_greeting_template(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    tpl_id = int((callback.data or "").split(":")[-1])
    async with db_manager.session() as session:
        setting = await session.scalar(
            select(SystemSetting).where(SystemSetting.key == "greeting_message")
        )
        if setting:
            cfg = dict(setting.value_json or {})
            cfg["template_id"] = tpl_id
            setting.value_json = cfg
    await callback.answer("تم تحديد قالب الترحيب بنجاح ✅")
    await cb_greeting_settings(callback, db_manager)


@settings_router.callback_query(F.data == "ar:quick_replies")
async def cb_quick_replies_list(
    callback: CallbackQuery,
    message_processor: MessageProcessor,
) -> None:
    q_list = await message_processor.list_quick_replies()
    if not q_list:
        body = "لا توجد ردود سريعة محفوظة حاليًا. يمكنك تعيين أي قالب كرد سريع من داخل تفاصيل القالب."
    else:
        body = "\n".join(f"⚡ <b>{t.name}</b> ({t.category or 'عام'})" for t in q_list)

    text = f"⚡ <b>الردود السريعة المحفوظة ({len(q_list)})</b>\n━━━━━━━━━━━━━━━━━━━━\n{body}"
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_section_submenu_keyboard("auto_reply"),
            parse_mode="HTML",
        )
    await callback.answer()


# --- إعدادات الإشعارات والمنطقة الزمنية (القسم 12) ---
@settings_router.callback_query(F.data == "sys:notifications")
async def cb_notifications_settings(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    async with db_manager.session() as session:
        setting = await session.scalar(
            select(SystemSetting).where(SystemSetting.key == "notifications_config")
        )
        cfg = setting.value_json if setting else {"enabled": True}

    enabled = bool(cfg.get("enabled", True))
    text = (
        "🔔 <b>إعدادات الإشعارات والتنبيهات الإدارية</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>إرسال التنبيهات للمالك:</b> {'🟢 مفعل' if enabled else '🔴 معطل (التنبيهات الحرجة فقط)'}\n\n"
        "يرسل النظام إشعارات عند اكتمال الحملات، تغير حالة اتصال Business، أو انتهاء جلسة الحساب الشخصي."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏯️ تفعيل / تعطيل الإشعارات الفورية",
                    callback_data="sys:toggle_notifications",
                )
            ],
            build_back_and_home_row("sec:settings"),
        ]
    )
    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@settings_router.callback_query(F.data == "sys:toggle_notifications")
async def cb_toggle_notifications(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
    access_control: AccessControl,
) -> None:
    async with db_manager.session() as session:
        setting = await session.scalar(
            select(SystemSetting).where(SystemSetting.key == "notifications_config")
        )
        if setting:
            cfg = dict(setting.value_json or {})
            cfg["enabled"] = not bool(cfg.get("enabled", True))
            setting.value_json = cfg
            new_state = cfg["enabled"]
        else:
            new_state = False
            session.add(
                SystemSetting(
                    key="notifications_config",
                    value_json={"enabled": False},
                    description="إعدادات الإشعارات الإدارية",
                )
            )
    await access_control.log_admin_action(
        "toggle_notifications", details={"enabled": new_state}
    )
    await cb_notifications_settings(callback, db_manager)


@settings_router.callback_query(F.data == "sys:timezone")
async def cb_timezone_settings(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    async with db_manager.session() as session:
        setting = await session.scalar(
            select(SystemSetting).where(SystemSetting.key == "work_hours")
        )
        cfg = setting.value_json if setting else {}

    tz = cfg.get("timezone", "Asia/Aden")
    text = (
        "🌍 <b>إعدادات المنطقة الزمنية</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>المنطقة الزمنية المعتمدة حاليًا:</b> <code>{tz}</code>\n\n"
        "اختر المنطقة الزمنية لتطبيقها على ساعات العمل والمهام المجدولة والمتغيرات:"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇾🇪 صنعاء / عدن (Asia/Aden)", callback_data="sys:set_tz:Asia/Aden"),
                InlineKeyboardButton(text="🇸🇦 الرياض (Asia/Riyadh)", callback_data="sys:set_tz:Asia/Riyadh"),
            ],
            [
                InlineKeyboardButton(text="🇪🇬 القاهرة (Africa/Cairo)", callback_data="sys:set_tz:Africa/Cairo"),
                InlineKeyboardButton(text="🌐 التوقيت العالمي (UTC)", callback_data="sys:set_tz:UTC"),
            ],
            build_back_and_home_row("sec:settings"),
        ]
    )
    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@settings_router.callback_query(F.data.startswith("sys:set_tz:"))
async def cb_set_timezone(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    new_tz = (callback.data or "").split(":", 2)[-1]
    async with db_manager.session() as session:
        setting = await session.scalar(
            select(SystemSetting).where(SystemSetting.key == "work_hours")
        )
        if setting:
            cfg = dict(setting.value_json or {})
            cfg["timezone"] = new_tz
            setting.value_json = cfg
    await callback.answer(f"تم ضبط المنطقة الزمنية على {new_tz} ✅")
    await cb_timezone_settings(callback, db_manager)


@settings_router.callback_query(F.data == "sys:backup_now")
async def cb_backup_database_now(
    callback: CallbackQuery,
    backup_manager: DatabaseBackupManager,
    access_control: AccessControl,
) -> None:
    try:
        backup_path = backup_manager.create_backup(tag="admin")
        await access_control.log_admin_action(
            "manual_database_backup", details={"file": backup_path.name}
        )
        msg = f"✅ تم إنشاء النسخة الاحتياطية بنجاح:\n<code>{backup_path.name}</code>"
    except Exception as exc:
        msg = f"❌ فشل إنشاء النسخة الاحتياطية: {exc}"

    if callback.message:
        await callback.message.edit_text(
            text=msg,
            reply_markup=get_section_submenu_keyboard("settings"),
            parse_mode="HTML",
        )
    await callback.answer()


@settings_router.callback_query(F.data == "sys:health_check")
async def cb_database_health_check(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    report = await DatabaseBackupManager.check_database_integrity(db_manager)
    text = (
        "🩺 <b>فحص سلامة قاعدة البيانات</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>فحص التكامل (Integrity):</b> {'🟢 سليم (OK)' if report['integrity_ok'] else '🔴 يوجد خلل'}\n"
        f"• <b>وضع السجل (Journal Mode):</b> <code>{report['journal_mode'].upper()}</code>\n"
        f"• <b>المفاتيح الأجنبية (Foreign Keys):</b> {'🟢 مفعلة' if report['foreign_keys_enabled'] else '🔴 معطلة'}"
    )
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_section_submenu_keyboard("settings"),
            parse_mode="HTML",
        )
    await callback.answer("تم فحص سلامة قاعدة البيانات")
