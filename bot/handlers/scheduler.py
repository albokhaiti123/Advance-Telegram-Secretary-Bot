from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import delete, select

from bot.keyboards.main_menu import get_section_submenu_keyboard
from database.database import DatabaseManager
from database.models import ScheduledTask

scheduler_router = Router(name="scheduler_router")


@scheduler_router.callback_query(F.data.startswith("sch:pending"))
async def cb_list_pending_tasks(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    async with db_manager.session() as session:
        tasks = list(
            (
                await session.scalars(
                    select(ScheduledTask)
                    .where(ScheduledTask.status == "pending")
                    .order_by(ScheduledTask.run_at.asc())
                    .limit(15)
                )
            ).all()
        )

    if not tasks:
        text = "⏳ <b>المهام المجدولة القادمة</b>\n━━━━━━━━━━━━━━━━━━━━\nلا توجد مهام بالانتظار حاليًا."
    else:
        lines = []
        for t in tasks:
            run_str = (
                t.run_at.strftime("%Y-%m-%d %H:%M")
                if t.run_at
                else (t.cron_expression or "—")
            )
            lines.append(f"• <code>{t.task_id}</code> | النوع: <b>{t.task_type}</b> | الموعد: {run_str}")
        text = "⏳ <b>المهام المجدولة القادمة</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines)

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_section_submenu_keyboard("scheduler"),
            parse_mode="HTML",
        )
    await callback.answer()


@scheduler_router.callback_query(F.data.startswith("sch:recurring"))
async def cb_list_recurring_tasks(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    async with db_manager.session() as session:
        tasks = list(
            (
                await session.scalars(
                    select(ScheduledTask).where(
                        ScheduledTask.cron_expression.is_not(None),
                        ScheduledTask.status == "pending",
                    )
                )
            ).all()
        )
    if not tasks:
        text = "🔁 <b>المهام الدورية المتكررة</b>\n━━━━━━━━━━━━━━━━━━━━\nلا توجد مهام دورية نشطة."
    else:
        lines = [
            f"• <code>{t.task_id}</code> | الجدول: <code>{t.cron_expression}</code>"
            for t in tasks
        ]
        text = "🔁 <b>المهام الدورية المتكررة</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines)

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_section_submenu_keyboard("scheduler"),
            parse_mode="HTML",
        )
    await callback.answer()


@scheduler_router.callback_query(F.data == "sch:cleanup")
async def cb_cleanup_tasks(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    async with db_manager.session() as session:
        res = await session.execute(
            delete(ScheduledTask).where(
                ScheduledTask.status.in_(["completed", "cancelled", "failed"])
            )
        )
        removed = res.rowcount or 0

    await callback.answer(
        f"تم تنظيف {removed} مهمة منتهية/فاشلة من السجل ✅", show_alert=True
    )
