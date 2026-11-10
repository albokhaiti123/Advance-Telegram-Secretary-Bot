from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from bot.keyboards.main_menu import get_section_submenu_keyboard
from bot.keyboards.navigation import build_back_and_home_row
from database.database import DatabaseManager
from database.models import SystemSetting
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

    enabled = bool(cfg.get("enabled", False))
    cooldown = cfg.get("cooldown_seconds", 3600)
    tpl_id = cfg.get("template_id")

    text = (
        "🌙 <b>إعدادات وضع الغياب (Away Mode)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>الحالة:</b> {'🟢 مفعل' if enabled else '🔴 متوقف'}\n"
        f"• <b>القالب المخصص:</b> {f'#{tpl_id}' if tpl_id else 'غير محدد'}\n"
        f"• <b>منع التكرار (Cooldown):</b> كل {cooldown} ثانية"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏯️ تفعيل / إيقاف وضع الغياب",
                    callback_data="ar:toggle_away_mode",
                )
            ],
            build_back_and_home_row("sec:auto_reply"),
        ]
    )
    if callback.message:
        await callback.message.edit_text(text=text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


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


@settings_router.callback_query(F.data == "ar:quick_replies")
async def cb_quick_replies_list(
    callback: CallbackQuery,
    message_processor: MessageProcessor,
) -> None:
    q_list = await message_processor.list_quick_replies()
    if not q_list:
        body = "لا توجد ردود سريعة محفوظة حاليًا."
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
