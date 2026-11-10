from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.keyboards.main_menu import get_section_submenu_keyboard
from services.statistics import StatisticsService
from utils.formatting import (
    format_activity_logs_list,
    format_campaigns_report,
    format_comprehensive_report,
    format_replies_report,
    format_system_errors_list,
)

statistics_router = Router(name="statistics_router")


@statistics_router.callback_query(F.data.startswith("stat:period:"))
async def cb_stats_by_period(
    callback: CallbackQuery,
    statistics_service: StatisticsService,
) -> None:
    period = (callback.data or "").split(":")[-1]
    replies = await statistics_service.get_replies_statistics(period=period)
    campaigns = await statistics_service.get_campaigns_statistics(period=period)
    health = await statistics_service.get_system_health_statistics()

    text = format_comprehensive_report(replies, campaigns, health, period=period)
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_section_submenu_keyboard("statistics"),
            parse_mode="HTML",
        )
    await callback.answer()


@statistics_router.callback_query(F.data == "stat:replies")
async def cb_stats_replies(
    callback: CallbackQuery,
    statistics_service: StatisticsService,
) -> None:
    report = await statistics_service.get_replies_statistics(period="all")
    text = format_replies_report(report, period="all")
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_section_submenu_keyboard("statistics"),
            parse_mode="HTML",
        )
    await callback.answer()


@statistics_router.callback_query(F.data == "stat:campaigns")
async def cb_stats_campaigns(
    callback: CallbackQuery,
    statistics_service: StatisticsService,
) -> None:
    report = await statistics_service.get_campaigns_statistics(period="all")
    text = format_campaigns_report(report, period="all")
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_section_submenu_keyboard("statistics"),
            parse_mode="HTML",
        )
    await callback.answer()


# --- معالجات القسم 10: سجل النشاط والأخطاء ---
@statistics_router.callback_query(F.data.startswith("logs:errors"))
async def cb_logs_errors(
    callback: CallbackQuery,
    statistics_service: StatisticsService,
) -> None:
    errors = await statistics_service.list_system_errors(limit=10)
    text = format_system_errors_list(errors)
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_section_submenu_keyboard("logs"),
            parse_mode="HTML",
        )
    await callback.answer()


@statistics_router.callback_query(F.data.startswith("logs:admin"))
async def cb_logs_admin(
    callback: CallbackQuery,
    statistics_service: StatisticsService,
) -> None:
    logs = await statistics_service.list_activity_logs(category="admin", limit=15)
    text = format_activity_logs_list(logs, "سجل عمليات الإدارة")
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_section_submenu_keyboard("logs"),
            parse_mode="HTML",
        )
    await callback.answer()


@statistics_router.callback_query(F.data.startswith("logs:deletions"))
async def cb_logs_deletions(
    callback: CallbackQuery,
    statistics_service: StatisticsService,
) -> None:
    logs = await statistics_service.list_activity_logs(category="deletion", limit=15)
    text = format_activity_logs_list(logs, "سجل حذف واستبدال الردود السابقة")
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_section_submenu_keyboard("logs"),
            parse_mode="HTML",
        )
    await callback.answer()


@statistics_router.callback_query(F.data.startswith("logs:connections"))
async def cb_logs_connections(
    callback: CallbackQuery,
    statistics_service: StatisticsService,
) -> None:
    logs = await statistics_service.list_activity_logs(category="connection", limit=15)
    text = format_activity_logs_list(logs, "سجل اتصالات Business والحساب الشخصي")
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_section_submenu_keyboard("logs"),
            parse_mode="HTML",
        )
    await callback.answer()
