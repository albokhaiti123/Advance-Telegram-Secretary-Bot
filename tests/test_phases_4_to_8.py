import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy import select

from database.database import DatabaseManager
from database.migrations import init_database
from database.models import (
    AutoReplyRule,
    BusinessConnection,
    Conversation,
    ConversationState,
    IgnoredUser,
    ManagedResponse,
    ScheduledTask,
)
from integrations.business.sender import BusinessSender
from services.auto_reply import AutoReplyService
from services.response_replacement import ResponseReplacementManager
from services.rule_engine import IncomingMessageEnvelope, RuleEngine
from services.template_manager import TemplateManager


async def _seed_business_connection(db_manager: DatabaseManager, conn_id: str = "biz_1") -> None:
    async with db_manager.session() as session:
        session.add(
            BusinessConnection(
                connection_id=conn_id,
                user_id=777000,
                user_chat_id=777000,
                can_reply=True,
                rights_json={
                    "can_reply": True,
                    "can_read_messages": True,
                    "can_delete_outgoing_messages": True,
                },
                is_active=True,
            )
        )
        session.add(
            Conversation(
                chat_id=1001,
                chat_type="private",
                title_or_name="عميل اختبار",
                business_connection_id=conn_id,
                auto_reply_enabled=True,
            )
        )
        session.add(ConversationState(chat_id=1001, first_message_handled=False))


@pytest.mark.asyncio
async def test_template_variables_and_buttons(tmp_path: Path) -> None:
    """اختبار المرحلة 6 و7: المتغيرات الديناميكية، حماية HTML، وبناء الأزرار التفاعلية."""
    db_manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'tpl.db'}")
    await init_database(db_manager)

    tpl_manager = TemplateManager(db_manager=db_manager, timezone_str="Asia/Aden")
    tpl = await tpl_manager.create_template(
        name="قالب ترحيب بالأزرار",
        content_type="text",
        text_content="مرحبًا {first_name} ({username})، تاريخ اليوم {date} {unknown_var}",
        buttons_data=[
            {
                "text": "موقعنا الرسمي",
                "button_type": "url",
                "url": "https://example.com",
                "row_index": 0,
                "col_index": 0,
            },
            {
                "text": "تواصل معنا",
                "button_type": "callback",
                "callback_data": "contact_us",
                "row_index": 1,
                "col_index": 0,
            },
        ],
    )

    payload = await tpl_manager.build_rendered_payload(
        template_id=tpl.id,
        context={
            "first_name": "أحمد <script>",
            "username": "ahmed_dev",
        },
    )
    assert payload is not None
    # التحقق من الهروب الآمن لـ HTML وعدم بقاء {unknown_var}
    assert "أحمد &lt;script&gt;" in payload.text
    assert "(@ahmed_dev)" in payload.text
    assert "{unknown_var}" not in payload.text
    assert payload.reply_markup is not None
    assert len(payload.reply_markup.inline_keyboard) == 2
    assert payload.reply_markup.inline_keyboard[0][0].url == "https://example.com"

    await db_manager.close()


@pytest.mark.asyncio
async def test_rule_engine_triggers_priorities_cooldown_and_exclusions(tmp_path: Path) -> None:
    """اختبار المرحلة 5: المحفزات الأربعة، الأولويات، Cooldown، والاستثناءات."""
    db_manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'rules.db'}")
    await init_database(db_manager)
    await _seed_business_connection(db_manager)

    tpl_manager = TemplateManager(db_manager=db_manager)
    t_first = await tpl_manager.create_template(name="T_First", text_content="أهلاً لأول مرة")
    t_price = await tpl_manager.create_template(name="T_Price", text_content="الأسعار كالتالي")
    t_regex = await tpl_manager.create_template(name="T_Regex", text_content="رقم طلبك قيد المراجعة")
    t_any = await tpl_manager.create_template(name="T_Any", text_content="رد عام")

    async with db_manager.session() as session:
        session.add_all(
            [
                AutoReplyRule(
                    name="قاعدة أول رسالة",
                    trigger_type="first_message",
                    template_id=t_first.id,
                    priority=200,
                    cooldown_seconds=60,
                ),
                AutoReplyRule(
                    name="قاعدة الأسعار",
                    trigger_type="keyword",
                    trigger_value="سعر, الأسعار، تكلفة",
                    template_id=t_price.id,
                    priority=150,
                    cooldown_seconds=120,
                ),
                AutoReplyRule(
                    name="قاعدة رقم الطلب",
                    trigger_type="regex",
                    trigger_value=r"ORD-\d{4}",
                    template_id=t_regex.id,
                    priority=140,
                    cooldown_seconds=60,
                ),
                AutoReplyRule(
                    name="قاعدة عامة",
                    trigger_type="any",
                    template_id=t_any.id,
                    priority=10,
                    cooldown_seconds=0,
                ),
            ]
        )

    engine = RuleEngine(db_manager=db_manager)

    # 1. الرسالة الأولى في المحادثة: تطابق قاعدة first_message ذات الأولوية 200
    env1 = IncomingMessageEnvelope(chat_id=1001, sender_id=5001, text="كم سعر الخدمة؟")
    matched1 = await engine.evaluate_message(env1)
    assert len(matched1) == 1
    assert matched1[0].trigger_type == "first_message"

    # تحديث حالة المحادثة بأن الرسالة الأولى تمت معالجتها
    async with db_manager.session() as session:
        st = await session.get(ConversationState, 1001)
        st.first_message_handled = True

    # 2. الآن نفس الرسالة تطابق قاعدة keyword (الأسعار) ذات الأولوية 150
    matched2 = await engine.evaluate_message(env1)
    assert len(matched2) == 1
    assert matched2[0].trigger_type == "keyword"

    # تسجيل Cooldown لقاعدة الأسعار، ثم إعادة إرسال نفس الرسالة -> يجب تخطي قاعدة الأسعار بسبب Cooldown والوصول لقاعدة any
    async with db_manager.session() as session:
        st = await session.get(ConversationState, 1001)
        st.rule_cooldowns_json = {str(matched2[0].id): datetime.now(timezone.utc).isoformat()}

    matched_cooldown = await engine.evaluate_message(env1)
    assert len(matched_cooldown) == 1
    assert matched_cooldown[0].trigger_type == "any"

    # 3. اختبار Regex
    env_regex = IncomingMessageEnvelope(chat_id=1001, sender_id=5001, text="استفسار عن الطلب ORD-4821")
    matched_regex = await engine.evaluate_message(env_regex)
    assert len(matched_regex) == 1
    assert matched_regex[0].trigger_type == "regex"

    # 4. اختبار استثناء المستخدم
    async with db_manager.session() as session:
        session.add(IgnoredUser(user_id=5001, list_type="blacklist"))

    matched_ignored = await engine.evaluate_message(env_regex)
    assert matched_ignored == []

    await db_manager.close()


@pytest.mark.asyncio
async def test_response_replacement_atomic_workflow_and_failure_safety(tmp_path: Path) -> None:
    """
    اختبار المرحلة 8:
    - إرسال الرد الأول.
    - إرسال الرد الثاني -> يرسل الجديد (202) ثم يحذف القديم (201).
    - فشل إرسال الرد الثالث -> لا يحذف الرد الثاني (202).
    - نجاح الرد الرابع (204) وفشل حذف (202) -> يبقي (204) ويجدول مهمة retry_delete.
    """
    db_manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'replace.db'}")
    await init_database(db_manager)
    await _seed_business_connection(db_manager)

    tpl_manager = TemplateManager(db_manager=db_manager)
    tpl = await tpl_manager.create_template(name="رد قياسي", text_content="رد تلقائي")
    payload = await tpl_manager.build_rendered_payload(tpl.id)

    mock_bot = AsyncMock()
    sender = BusinessSender(bot=mock_bot, db_manager=db_manager)
    replacement_mgr = ResponseReplacementManager(db_manager=db_manager, business_sender=sender)

    # أ. إرسال الرد التلقائي الأول (معرف الرسالة 201)
    mock_bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=201))
    mock_bot.delete_business_messages = AsyncMock(return_value=True)

    res1 = await replacement_mgr.send_and_replace_previous(
        chat_id=1001,
        business_connection_id="biz_1",
        payload=payload,
    )
    assert res1.success is True
    assert res1.new_message_id == 201
    assert res1.replaced_old_message_id is None
    mock_bot.delete_business_messages.assert_not_called()

    # ب. إرسال الرد الثاني (معرف الرسالة 202) -> يجب أن يرسل 202 أولًا ثم يحذف 201
    mock_bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=202))
    res2 = await replacement_mgr.send_and_replace_previous(
        chat_id=1001,
        business_connection_id="biz_1",
        payload=payload,
    )
    assert res2.success is True
    assert res2.new_message_id == 202
    assert res2.replaced_old_message_id == 201
    assert res2.old_delete_succeeded is True
    mock_bot.delete_business_messages.assert_awaited_once_with(
        business_connection_id="biz_1",
        message_ids=[201],
    )

    # ج. فشل إرسال الرد الثالث -> يجب ألا يحذف الرسالة 202
    mock_bot.delete_business_messages.reset_mock()
    mock_bot.send_message = AsyncMock(side_effect=RuntimeError("Network Timeout"))
    res3 = await replacement_mgr.send_and_replace_previous(
        chat_id=1001,
        business_connection_id="biz_1",
        payload=payload,
    )
    assert res3.success is False
    mock_bot.delete_business_messages.assert_not_called()

    # التأكد أن الرسالة 202 ما زالت هي النشطة والأخيرة في قاعدة البيانات
    async with db_manager.session() as session:
        latest = await session.scalar(
            select(ManagedResponse).where(
                ManagedResponse.chat_id == 1001,
                ManagedResponse.is_latest_in_chat.is_(True),
            )
        )
        assert latest is not None
        assert latest.message_id == 202
        assert latest.status == "active"

    # د. نجاح إرسال الرد الرابع (204) ولكن فشل حذف الرد السابق (202) -> يجب بقاء 204 وجدولة retry_delete
    from aiogram.exceptions import TelegramBadRequest
    mock_bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=204))
    mock_bot.delete_business_messages = AsyncMock(
        side_effect=TelegramBadRequest(method=MagicMock(), message="Temporary network glitch")
    )

    res4 = await replacement_mgr.send_and_replace_previous(
        chat_id=1001,
        business_connection_id="biz_1",
        payload=payload,
    )
    assert res4.success is True
    assert res4.new_message_id == 204
    assert res4.old_delete_succeeded is False

    async with db_manager.session() as session:
        retry_task = await session.scalar(
            select(ScheduledTask).where(ScheduledTask.task_type == "retry_delete")
        )
        assert retry_task is not None
        assert retry_task.payload_json["message_id"] == 202

    await db_manager.close()


@pytest.mark.asyncio
async def test_per_chat_lock_prevents_race_condition(tmp_path: Path) -> None:
    """التحقق من أن قفل المحادثة يمنع تداخل عمليتين متزامنتين ويحذف الأقدم بالترتيب الصحيح."""
    db_manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'lock.db'}")
    await init_database(db_manager)
    await _seed_business_connection(db_manager)

    tpl_manager = TemplateManager(db_manager=db_manager)
    tpl = await tpl_manager.create_template(name="قالب تزامن", text_content="اختبار قفل")
    payload = await tpl_manager.build_rendered_payload(tpl.id)

    counter = 300

    async def _slow_send(*args, **kwargs):
        nonlocal counter
        await asyncio.sleep(0.05)
        counter += 1
        return SimpleNamespace(message_id=counter)

    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(side_effect=_slow_send)
    mock_bot.delete_business_messages = AsyncMock(return_value=True)

    sender = BusinessSender(bot=mock_bot, db_manager=db_manager)
    replacement_mgr = ResponseReplacementManager(db_manager=db_manager, business_sender=sender)

    # إطلاق عمليتين في نفس اللحظة لنفس المحادثة 1001
    r1, r2 = await asyncio.gather(
        replacement_mgr.send_and_replace_previous(1001, "biz_1", payload),
        replacement_mgr.send_and_replace_previous(1001, "biz_1", payload),
    )

    assert r1.new_message_id == 301
    assert r2.new_message_id == 302
    assert r2.replaced_old_message_id == 301

    async with db_manager.session() as session:
        active_list = list(
            (
                await session.scalars(
                    select(ManagedResponse).where(
                        ManagedResponse.chat_id == 1001,
                        ManagedResponse.is_latest_in_chat.is_(True),
                    )
                )
            ).all()
        )
        assert len(active_list) == 1
        assert active_list[0].message_id == 302

    await db_manager.close()
