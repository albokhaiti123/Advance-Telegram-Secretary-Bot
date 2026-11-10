import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select

from database.database import DatabaseManager
from database.migrations import init_database
from database.models import (
    AutoReplyRule,
    BroadcastCampaign,
    BroadcastDeliveryLog,
    BroadcastRun,
    BusinessConnection,
    Conversation,
    ConversationState,
    Group,
    ManagedResponse,
    SystemSetting,
)
from integrations.business.sender import BusinessSender
from services.auto_reply import AutoReplyService
from services.message_processor import MessageProcessor
from services.notification_manager import NotificationManager
from services.response_replacement import ResponseReplacementManager
from services.rule_engine import RuleEngine
from services.statistics import StatisticsService
from services.template_manager import TemplateManager
from utils.formatting import format_comprehensive_report
from utils.helpers import DatabaseBackupManager, SingleInstanceLock


@pytest.mark.asyncio
async def test_phase14_work_hours_away_mode_greeting_and_quick_replies(tmp_path: Path) -> None:
    """
    اختبار المرحلة 14:
    - فحص جدول ساعات العمل.
    - تفعيل وضع الغياب (Away Mode) والتأكد من إرسال رسالة الغياب مرة واحدة فقط خلال فترة Cooldown.
    - اختبار الردود السريعة (Quick Replies).
    """
    db_manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'p14.db'}")
    await init_database(db_manager)

    tpl_mgr = TemplateManager(db_manager)
    away_tpl = await tpl_mgr.create_template(
        name="قالب الغياب",
        text_content="نحن خارج أوقات الدوام حاليًا يا {first_name}، سنرد عليك قريبًا.",
    )
    quick_tpl = await tpl_mgr.create_template(
        name="رد سريع: العنوان",
        text_content="عنواننا: صنعاء - شارع حدة",
        is_quick_reply=True,
        category="معلومات عامة",
    )

    async with db_manager.session() as session:
        session.add(
            BusinessConnection(
                connection_id="biz_p14",
                user_id=777000,
                user_chat_id=777000,
                can_reply=True,
                rights_json={"can_reply": True, "can_delete_outgoing_messages": True},
                is_active=True,
            )
        )
        session.add(
            Conversation(
                chat_id=2002,
                chat_type="private",
                title_or_name="خالد",
                business_connection_id="biz_p14",
                auto_reply_enabled=True,
                message_count=1,
            )
        )
        # تفعيل وضع الغياب في جميع الأوقات لأغراض الاختبار مع Cooldown ساعة
        away_setting = await session.scalar(
            select(SystemSetting).where(SystemSetting.key == "away_mode")
        )
        away_setting.value_json = {
            "enabled": True,
            "template_id": away_tpl.id,
            "cooldown_seconds": 3600,
            "only_outside_work_hours": False,
            "stop_other_rules": True,
        }

    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=801))
    mock_bot.delete_business_messages = AsyncMock(return_value=True)

    sender = BusinessSender(bot=mock_bot, db_manager=db_manager)
    repl_mgr = ResponseReplacementManager(db_manager=db_manager, business_sender=sender)
    rule_engine = RuleEngine(db_manager=db_manager)
    auto_reply_svc = AutoReplyService(
        db_manager=db_manager,
        rule_engine=rule_engine,
        template_manager=tpl_mgr,
        replacement_manager=repl_mgr,
    )
    processor = MessageProcessor(
        db_manager=db_manager,
        auto_reply_service=auto_reply_svc,
        template_manager=tpl_mgr,
        replacement_manager=repl_mgr,
    )

    fake_msg = SimpleNamespace(
        business_connection_id="biz_p14",
        chat=SimpleNamespace(id=2002, type="private", title=None, full_name="خالد"),
        from_user=SimpleNamespace(id=2002, first_name="خالد", last_name="", username="khalid"),
        text="السلام عليكم",
        caption=None,
        photo=None,
        video=None,
        document=None,
        voice=None,
        audio=None,
        sticker=None,
    )

    # 1. الرسالة الأولى: يجب أن ترسل رسالة الغياب (801)
    sent_first = await processor.handle_customer_message(fake_msg)
    assert sent_first == [801]

    # 2. الرسالة الثانية بعد ثوانٍ: يجب ألا تكرر رسالة الغياب احترامًا لـ Cooldown
    sent_second = await processor.handle_customer_message(fake_msg)
    assert sent_second == []

    # 3. التحقق من استرجاع الردود السريعة
    quicks = await processor.list_quick_replies(category="معلومات عامة")
    assert len(quicks) == 1
    assert quicks[0].id == quick_tpl.id

    await db_manager.close()


@pytest.mark.asyncio
async def test_phase15_statistics_and_notifications(tmp_path: Path) -> None:
    """اختبار المرحلة 15: دقة تقارير الإحصائيات (الردود، الحملات، صحة النظام) والتنبيهات."""
    db_manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'p15.db'}")
    await init_database(db_manager)

    tpl_mgr = TemplateManager(db_manager)
    tpl = await tpl_mgr.create_template(name="T1", text_content="Hello")

    async with db_manager.session() as session:
        session.add(
            Conversation(
                chat_id=3001,
                chat_type="private",
                message_count=10,
                reply_count=4,
            )
        )
        session.add(
            AutoReplyRule(
                name="قاعدة الترحيب",
                trigger_type="first_message",
                template_id=tpl.id,
                execution_count=4,
            )
        )
        session.add(
            ManagedResponse(
                chat_id=3001,
                message_id=91,
                status="replaced",
                is_latest_in_chat=False,
            )
        )
        session.add(
            ManagedResponse(
                chat_id=3001,
                message_id=92,
                status="active",
                is_latest_in_chat=True,
            )
        )
        session.add(
            Group(
                group_id=-10099,
                title="مجموعة اختبار",
                can_send_messages=True,
                is_active=True,
            )
        )
        camp = BroadcastCampaign(name="حملة رقم 1", status="completed")
        session.add(camp)
        await session.flush()

        run = BroadcastRun(
            run_id="run_stat_1",
            campaign_id=camp.id,
            status="completed",
            total_targets=1,
            sent_count=1,
        )
        session.add(run)
        await session.flush()
        session.add(
            BroadcastDeliveryLog(
                run_id="run_stat_1",
                campaign_id=camp.id,
                group_id=-10099,
                status="sent",
                telegram_message_id=555,
            )
        )

    stats_svc = StatisticsService(db_manager)
    replies_rep = await stats_svc.get_replies_statistics(period="daily")
    camp_rep = await stats_svc.get_campaigns_statistics(period="daily")
    health_rep = await stats_svc.get_system_health_statistics()

    assert replies_rep.total_messages_received == 10
    assert replies_rep.total_auto_replies_sent == 2
    assert replies_rep.replaced_replies_count == 1
    assert camp_rep.eligible_groups_count == 1
    assert camp_rep.successful_deliveries == 1
    assert camp_rep.overall_success_rate == 100.0

    formatted = format_comprehensive_report(
        replies_rep, camp_rep, health_rep, period="daily"
    )
    assert "التقرير الشامل للنظام" in formatted
    assert "100.0%" in formatted

    # اختبار إرسال وتخزين إشعار للمالك
    mock_bot = AsyncMock()
    notif_mgr = NotificationManager(db_manager, owner_id=777000, bot=mock_bot)
    notif = await notif_mgr.notify_owner(
        category="campaign",
        title="اكتمال حملة",
        message="تم إرسال الحملة بنجاح",
        level="info",
    )
    assert notif.is_sent_to_owner is True
    mock_bot.send_message.assert_awaited_once()

    await db_manager.close()


@pytest.mark.asyncio
async def test_phase16_backup_restore_and_single_instance_lock(tmp_path: Path) -> None:
    """اختبار المرحلة 16 و18: النسخ الاحتياطي الذري والاستعادة وقفل العملية الأحادي (PID Lock)."""
    db_file = tmp_path / "live_secretary.db"
    backup_dir = tmp_path / "backups"
    db_manager = DatabaseManager(f"sqlite+aiosqlite:///{db_file}")
    await init_database(db_manager)

    backup_mgr = DatabaseBackupManager(
        db_file_path=db_file,
        backup_dir=backup_dir,
        max_backups_to_keep=3,
    )

    # 1. فحص سلامة قاعدة البيانات
    health = await DatabaseBackupManager.check_database_integrity(db_manager)
    assert health["integrity_ok"] is True
    assert health["journal_mode"] == "wal"

    # 2. إنشاء نسخة احتياطية والتحقق من وجودها وسلامتها
    b_path = backup_mgr.create_backup(tag="test")
    assert b_path.exists()
    assert len(backup_mgr.list_backups()) == 1

    # 3. اختبار الاستعادة من النسخة الاحتياطية
    restored = backup_mgr.restore_from_backup(b_path)
    assert restored is True

    # 4. اختبار قفل العملية الأحادي (SingleInstanceLock)
    lock_file = tmp_path / "bot.lock"
    lock1 = SingleInstanceLock(lock_file)
    assert lock1.acquire() is True
    assert lock_file.exists()
    assert int(lock_file.read_text(encoding="utf-8")) == os.getpid()

    lock1.release()
    assert not lock_file.exists()

    await db_manager.close()
