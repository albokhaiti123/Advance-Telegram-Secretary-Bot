from typing import Sequence
from database.models import ActivityLog, SystemError
from services.statistics import (
    CampaignsStatsReport,
    RepliesStatsReport,
    SystemHealthReport,
)

PERIOD_LABELS = {
    "daily": "اليوم (آخر 24 ساعة)",
    "weekly": "الأسبوع (آخر 7 أيام)",
    "monthly": "الشهر (آخر 30 يومًا)",
    "all": "الإجمالي الكلي",
}


def format_duration_arabic(seconds: int) -> str:
    days, rem = divmod(max(0, seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days > 0:
        parts.append(f"{days} يوم")
    if hours > 0:
        parts.append(f"{hours} ساعة")
    parts.append(f"{minutes} دقيقة")
    return " و ".join(parts)


def format_replies_report(report: RepliesStatsReport, period: str = "all") -> str:
    label = PERIOD_LABELS.get(period, "الإجمالي")
    top_rules_text = (
        "\n".join(
            f"  {idx + 1}. <b>{name}</b> ({cnt} مرة)"
            for idx, (name, cnt) in enumerate(report.top_rules)
        )
        if report.top_rules
        else "  لا توجد قواعد منفذة بعد"
    )
    return (
        f"🤖 <b>تقرير إحصائيات الردود — {label}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>الرسائل المستلمة:</b> {report.total_messages_received}\n"
        f"• <b>الردود التلقائية المرسلة:</b> {report.total_auto_replies_sent}\n"
        f"• <b>إجمالي مرات تنفيذ القواعد:</b> {report.total_rules_executed}\n"
        f"• <b>عمليات استبدال الرد السابق:</b> {report.replaced_replies_count}\n"
        f"• <b>الردود المحذوفة:</b> {report.deleted_replies_count}\n"
        f"• <b>الردود الفاشلة:</b> {report.failed_replies_count}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>أكثر القواعد استخدامًا:</b>\n{top_rules_text}"
    )


def format_campaigns_report(report: CampaignsStatsReport, period: str = "all") -> str:
    label = PERIOD_LABELS.get(period, "الإجمالي")
    return (
        f"📢 <b>تقرير المجموعات والحملات — {label}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>المجموعات المكتشفة:</b> {report.discovered_groups_count}\n"
        f"• <b>المجموعات المؤهلة للإرسال:</b> {report.eligible_groups_count} 🟢\n"
        f"• <b>إجمالي الحملات:</b> {report.total_campaigns_count}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>إجمالي محاولات الإرسال:</b> {report.total_messages_attempted}\n"
        f"• <b>الإرسال الناجح:</b> {report.successful_deliveries} ✅\n"
        f"• <b>العمليات الفاشلة:</b> {report.failed_deliveries} ❌\n"
        f"• <b>العمليات المتخطاة (Skipped):</b> {report.skipped_deliveries} ⏭️\n"
        f"• <b>نسبة نجاح الإرسال:</b> {report.overall_success_rate}%"
    )


def format_comprehensive_report(
    replies: RepliesStatsReport,
    campaigns: CampaignsStatsReport,
    health: SystemHealthReport,
    period: str,
) -> str:
    label = PERIOD_LABELS.get(period, period)
    return (
        f"📊 <b>التقرير الشامل للنظام — {label}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 <b>الردود:</b> استلام {replies.total_messages_received} | رد {replies.total_auto_replies_sent} | استبدال {replies.replaced_replies_count}\n"
        f"📢 <b>الحملات:</b> مجموعات مؤهلة {campaigns.eligible_groups_count}/{campaigns.discovered_groups_count} | نجاح البث {campaigns.overall_success_rate}%\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🔌 <b>اتصال Business:</b> {'🟢 متصل' if health.business_connected else '🔴 متوقف'}\n"
        f"👤 <b>جلسة الحساب الشخصي:</b> {'🟢 مفوض' if health.personal_authorized else '⚪ غير مربوط'}\n"
        f"⏳ <b>المهام بالانتظار:</b> {health.pending_tasks_count} | <b>الفاشلة:</b> {health.failed_tasks_count}\n"
        f"⚠️ <b>الأخطاء غير المعالجة:</b> {health.unresolved_errors_count}\n"
        f"⏱️ <b>مدة التشغيل المستمر:</b> {format_duration_arabic(health.uptime_seconds)}"
    )


def format_activity_logs_list(logs: Sequence[ActivityLog], title: str) -> str:
    if not logs:
        return f"📝 <b>{title}</b>\n━━━━━━━━━━━━━━━━━━━━\nلا توجد سجلات مسجلة حتى الآن."
    lines = []
    for item in logs:
        ts = item.created_at.strftime("%m-%d %H:%M") if item.created_at else ""
        lines.append(
            f"• <code>[{ts}]</code> <b>{item.action}</b> (الهدف: {item.target_id or '—'})"
        )
    return f"📝 <b>{title}</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines)


def format_system_errors_list(errors: Sequence[SystemError]) -> str:
    if not errors:
        return "✅ <b>سجل أخطاء النظام</b>\n━━━━━━━━━━━━━━━━━━━━\nلا توجد أي أخطاء مسجلة في النظام."
    lines = []
    for err in errors:
        ts = err.created_at.strftime("%m-%d %H:%M") if err.created_at else ""
        lines.append(
            f"⚠️ <code>[{ts}]</code> <b>{err.component}</b> ({err.error_type}):\n"
            f"  <i>{err.error_message[:120]}</i>"
        )
    return "⚠️ <b>آخر الأخطاء والاستثناءات</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n\n".join(lines)
