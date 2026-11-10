import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from telethon.errors import ChatWriteForbiddenError, FloodWaitError

from database.database import DatabaseManager
from database.migrations import init_database
from database.models import (
    BroadcastCampaign,
    BroadcastDeliveryLog,
    BroadcastRun,
    Conversation,
    ConversationState,
    Group,
    PersonalAccountSession,
    ScheduledTask,
)
from integrations.personal.account_monitor import PersonalAccountMonitor
from integrations.personal.client import PersonalClientManager
from integrations.personal.dialogs import PersonalDialogsInspector
from integrations.personal.group_sender import PersonalGroupSender
from scheduler.manager import UnifiedSchedulerManager
from scheduler.task_recovery import TaskRecoveryService
from security.secrets import SecretManager
from services.broadcast_manager import BroadcastManager
from services.campaign_manager import CampaignManager
from services.conversation_manager import ConversationManager
from services.group_manager import GroupManager


@pytest.mark.asyncio
async def test_phase9_conversation_and_exception_management(tmp_path: Path) -> None:
    """اختبار المرحلة 9: التحكم بالمحادثات، إعادة الضبط، وقوائم الاستثناءات."""
    db_manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'p9.db'}")
    await init_database(db_manager)

    async with db_manager.session() as session:
        session.add(
            Conversation(
                chat_id=4001,
                chat_type="private",
                title_or_name="سالم العولقي",
                auto_reply_enabled=True,
                message_count=5,
                reply_count=2,
            )
        )
        session.add(
            ConversationState(
                chat_id=4001,
                first_message_handled=True,
                rule_cooldowns_json={"1": "2026-11-10T10:00:00+00:00"},
                pending_operation_json={"op": "stuck"},
            )
        )

    conv_mgr = ConversationManager(db_manager)

    # تعطيل الرد التلقائي للمحادثة
    new_state = await conv_mgr.toggle_conversation_auto_reply(4001)
    assert new_state is False

    # إعادة ضبط حالة المحادثة
    reset_ok = await conv_mgr.reset_conversation_state(4001)
    assert reset_ok is True

    details = await conv_mgr.get_conversation_details(4001)
    assert details is not None
    assert details.conversation.auto_reply_enabled is False
    assert details.state.first_message_handled is False
    assert details.state.rule_cooldowns_json == {}
    assert details.state.pending_operation_json is None

    # إضافة وإزالة استثناء مستخدم ومحادثة
    await conv_mgr.set_user_exception(9001, list_type="blacklist", reason="Spam")
    await conv_mgr.set_chat_exception(4001, list_type="blacklist")
    summary = await conv_mgr.list_exceptions_summary()
    assert len(summary["users"]) == 1
    assert len(summary["chats"]) == 1

    await conv_mgr.remove_user_exception(9001)
    await conv_mgr.remove_chat_exception(4001)
    summary_after = await conv_mgr.list_exceptions_summary()
    assert len(summary_after["users"]) == 0
    assert len(summary_after["chats"]) == 0

    await db_manager.close()


@pytest.mark.asyncio
async def test_phase10_and_11_personal_monitor_and_group_collections(tmp_path: Path) -> None:
    """اختبار المرحلة 10 و11: مراقبة جلسة الحساب الشخصي، اكتشاف المجموعات، وإدارة التصنيفات."""
    db_manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'p10_11.db'}")
    await init_database(db_manager)

    key = Fernet.generate_key().decode("utf-8")
    secret_mgr = SecretManager(key)

    async with db_manager.session() as session:
        session.add(
            PersonalAccountSession(
                phone_masked="+967*******89",
                telegram_user_id=777111,
                username="rabea_eng",
                encrypted_session=secret_mgr.encrypt_session("mock_session_str"),
                is_authorized=True,
            )
        )

    # محاكاة عميل Telethon
    mock_tl_client = AsyncMock()
    mock_tl_client.get_me = AsyncMock(
        return_value=SimpleNamespace(id=777111, username="rabea_eng")
    )
    mock_dialogs = [
        SimpleNamespace(
            is_group=True,
            id=-100111,
            title="مجموعة تقنية مفتوحة",
            entity=SimpleNamespace(
                id=-100111,
                access_hash=12345,
                title="مجموعة تقنية مفتوحة",
                username="tech_grp",
                megagroup=True,
                participants_count=1500,
                slowmode_seconds=0,
                left=False,
                kicked=False,
                creator=False,
                admin_rights=None,
                banned_rights=None,
                default_banned_rights=SimpleNamespace(
                    send_messages=False, send_media=False, send_inline=False
                ),
            ),
        ),
        SimpleNamespace(
            is_group=True,
            id=-100222,
            title="مجموعة للقراءة فقط",
            entity=SimpleNamespace(
                id=-100222,
                access_hash=67890,
                title="مجموعة للقراءة فقط",
                username="readonly_grp",
                megagroup=True,
                participants_count=800,
                slowmode_seconds=10,
                left=False,
                kicked=False,
                creator=False,
                admin_rights=None,
                banned_rights=None,
                default_banned_rights=SimpleNamespace(
                    send_messages=True, send_media=True, send_inline=True
                ),
            ),
        ),
    ]
    mock_tl_client.get_dialogs = AsyncMock(return_value=mock_dialogs)

    client_mgr = MagicMock(spec=PersonalClientManager)
    client_mgr.get_authorized_client = AsyncMock(return_value=mock_tl_client)

    # فحص صحة الجلسة
    monitor = PersonalAccountMonitor(db_manager, client_mgr)
    health = await monitor.verify_session_health()
    assert health["is_authorized"] is True
    assert health["username"] == "rabea_eng"

    # اكتشاف المجموعات وفحص صلاحياتها
    inspector = PersonalDialogsInspector(db_manager, client_mgr)
    disc_summary = await inspector.discover_and_sync_groups()
    assert disc_summary.total_discovered == 2
    assert disc_summary.sendable_count == 1
    assert disc_summary.restricted_count == 1

    # إدارة التصنيفات (Collections)
    grp_mgr = GroupManager(db_manager)
    col_work = await grp_mgr.create_collection("مجموعات العمل")
    col_promo = await grp_mgr.create_collection("مجموعات الترويج")

    await grp_mgr.add_group_to_collection(col_work.id, -100111)
    await grp_mgr.add_group_to_collection(col_work.id, -100222)

    # نقل مجموعة بين التصنيفات
    await grp_mgr.move_group_between_collections(col_work.id, col_promo.id, -100111)

    targets = await grp_mgr.resolve_campaign_target_groups(collection_id=col_promo.id)
    assert len(targets) == 1
    assert targets[0].group_id == -100111

    # حذف التصنيف والتأكد من بقاء المجموعة في جدول groups
    await grp_mgr.delete_collection(col_promo.id)
    all_groups = await grp_mgr.list_groups()
    assert len(all_groups) == 2

    await db_manager.close()


@pytest.mark.asyncio
async def test_phase12_and_13_broadcast_and_run_id_invalidation_on_replace(tmp_path: Path) -> None:
    """
    اختبار المرحلة 12 و13 الحاسم:
    - التعامل مع FloodWait ومنع الكتابة في المجموعات.
    - استبدال حملة جارية (replace_campaign) والتحقق من أن التنفيذ القديم يتوقف فورًا
      بسبب إبطال run_id، وأن التنفيذ الجديد يرسل المحتوى المحدث.
    """
    db_manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'p12_13.db'}")
    await init_database(db_manager)

    # إدراج 3 مجموعات صالحة للنشر
    async with db_manager.session() as session:
        for gid, title in [(-1001, "G1"), (-1002, "G2"), (-1003, "G3")]:
            session.add(
                Group(
                    group_id=gid,
                    title=title,
                    can_send_messages=True,
                    is_active=True,
                )
            )

    grp_mgr = GroupManager(db_manager)
    camp_mgr = CampaignManager(db_manager, grp_mgr)

    campaign = await camp_mgr.create_campaign(
        name="حملة العروض الأولى",
        text_content="النص القديم للحملة",
        specific_group_ids=[-1001, -1002, -1003],
    )
    first_run = await camp_mgr.initiate_new_run(campaign.id)
    old_run_id = first_run.run_id

    sent_history = []
    new_run_holder = {}

    async def _simulated_send_to_group(group_id: int, text: str, **kwargs):
        sent_history.append((group_id, text))
        # بمجرد إرسال المجموعة الأولى (-1001) في الحملة القديمة، يقوم المالك باستبدال الحملة!
        if group_id == -1001 and text == "النص القديم للحملة":
            replaced_run = await camp_mgr.replace_campaign(
                campaign_id=campaign.id,
                new_name="حملة العروض المحدثة",
                new_text_content="النص الجديد بعد الاستبدال",
            )
            new_run_holder["run_id"] = replaced_run.run_id

        from integrations.personal.group_sender import GroupSendAttemptResult
        return GroupSendAttemptResult(status="sent", telegram_message_id=900 + abs(group_id))

    mock_sender = MagicMock(spec=PersonalGroupSender)
    mock_sender.send_to_group = AsyncMock(side_effect=_simulated_send_to_group)

    bc_mgr = BroadcastManager(
        db_manager=db_manager,
        group_sender=mock_sender,
        min_delay_seconds=0.01,
        max_delay_seconds=0.02,
    )

    # 1. تشغيل الحملة القديمة -> يجب أن ترسل لـ -1001 فقط ثم تكتشف أن run_id أُبطل فتتوقف فورًا!
    old_status = await bc_mgr.execute_campaign_run(campaign.id, old_run_id)
    assert old_status == "superseded"

    # التأكد أن النص القديم لم يُرسل إلى -1002 أو -1003
    assert sent_history == [(-1001, "النص القديم للحملة")]

    # 2. تشغيل الحملة الجديدة المستبدلة بـ run_id الجديد -> ترسل النص الجديد للمجموعات الثلاث
    new_run_id = new_run_holder["run_id"]
    assert new_run_id != old_run_id
    new_status = await bc_mgr.execute_campaign_run(campaign.id, new_run_id)
    assert new_status == "completed"

    new_sends = [item for item in sent_history if item[1] == "النص الجديد بعد الاستبدال"]
    assert len(new_sends) == 3

    await db_manager.close()


@pytest.mark.asyncio
async def test_phase13_task_recovery_after_restart(tmp_path: Path) -> None:
    """اختبار استعادة المهام المجدولة وتأمين الحملات المنقطعة بعد إعادة تشغيل VPS."""
    db_manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    await init_database(db_manager)

    past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    future_time = datetime.now(timezone.utc) + timedelta(hours=2)

    async with db_manager.session() as session:
        session.add(
            ScheduledTask(
                task_id="future_task_1",
                task_type="campaign_run",
                reference_id="1",
                payload_json={"campaign_id": 1},
                run_at=future_time,
                status="pending",
            )
        )
        session.add(
            BroadcastCampaign(
                id=1,
                name="حملة منقطعة",
                status="running",
                active_run_id="run_interrupted_1",
            )
        )
        session.add(
            BroadcastRun(
                run_id="run_interrupted_1",
                campaign_id=1,
                status="running",
                started_at=past_time,
            )
        )

    sched_mgr = UnifiedSchedulerManager(
        db_manager=db_manager,
        business_sender=MagicMock(),
        replacement_manager=MagicMock(),
        campaign_manager=MagicMock(),
        broadcast_manager=MagicMock(),
    )
    sched_mgr.start()

    recovery = TaskRecoveryService(db_manager, sched_mgr)
    stats = await recovery.recover_all_on_startup()

    assert stats["recovered_tasks"] == 1
    assert stats["paused_interrupted_runs"] == 1
    assert sched_mgr.scheduler.get_job("future_task_1") is not None

    sched_mgr.shutdown()
    await db_manager.close()
