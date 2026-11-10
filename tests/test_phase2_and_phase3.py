from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy import select

from aiogram.types import Message
from bot.handlers.dashboard import render_main_dashboard_text
from bot.keyboards.main_menu import get_main_dashboard_keyboard, get_section_submenu_keyboard
from bot.middlewares.owner_only import OwnerOnlyMiddleware
from database.database import DatabaseManager
from database.migrations import init_database
from database.models import ActivityLog, BusinessConnection, Conversation
from integrations.business.connection import BusinessConnectionManager
from integrations.business.sender import BusinessSender
from integrations.business.updates import BusinessUpdateProcessor
from security.access_control import AccessControl

OWNER_ID = 777000111
INTRUDER_ID = 999888777


@pytest.mark.asyncio
async def test_owner_only_middleware_blocks_intruder_and_allows_owner(tmp_path: Path) -> None:
    """التحقق من حظر غير المالك وتسجيل المحاولة، والسماح للمالك."""
    db_manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'sec.db'}")
    await init_database(db_manager)

    access_control = AccessControl(owner_id=OWNER_ID, db_manager=db_manager)
    middleware = OwnerOnlyMiddleware(access_control=access_control)
    handler = AsyncMock(return_value="OK")

    # 1. محاولة من مستخدم غريب
    intruder_msg = MagicMock(spec=Message)
    intruder_msg.business_connection_id = None
    intruder_msg.from_user = SimpleNamespace(id=INTRUDER_ID, username="intruder_user")
    intruder_msg.answer = AsyncMock()

    result = await middleware(handler, intruder_msg, {})
    assert result is None
    handler.assert_not_called()
    intruder_msg.answer.assert_called_once()

    # التحقق من تسجيل المحاولة في قاعدة البيانات
    async with db_manager.session() as session:
        log_entry = await session.scalar(
            select(ActivityLog).where(
                ActivityLog.action == "unauthorized_access_blocked"
            )
        )
        assert log_entry is not None
        assert log_entry.actor_id == INTRUDER_ID

    # 2. طلب شرعي من المالك
    owner_msg = MagicMock(spec=Message)
    owner_msg.business_connection_id = None
    owner_msg.from_user = SimpleNamespace(id=OWNER_ID, username="rabea_owner")

    result_owner = await middleware(handler, owner_msg, {})
    assert result_owner == "OK"
    handler.assert_called_once()

    await db_manager.close()


@pytest.mark.asyncio
async def test_dashboard_12_sections_and_rendering(tmp_path: Path) -> None:
    """التحقق من وجود الأقسام الـ 12 في لوحة التحكم وصحة توليد ملخص النظام."""
    db_manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'dash.db'}")
    await init_database(db_manager)

    text, enabled = await render_main_dashboard_text(db_manager)
    assert enabled is True
    assert "لوحة التحكم الرئيسية" in text

    kb = get_main_dashboard_keyboard(auto_reply_enabled=enabled)
    all_callbacks = [
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("sec:")
    ]
    assert len(all_callbacks) == 12

    for section_cb in all_callbacks:
        section_key = section_cb.split(":", 1)[1]
        sub_kb = get_section_submenu_keyboard(section_key)
        assert len(sub_kb.inline_keyboard) >= 2

    await db_manager.close()


@pytest.mark.asyncio
async def test_business_connection_lifecycle_and_sender(tmp_path: Path) -> None:
    """التحقق من ربط اتصال Business، وفحص الصلاحيات، وإرسال وحذف الرسائل."""
    db_manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'biz.db'}")
    await init_database(db_manager)

    conn_manager = BusinessConnectionManager(db_manager=db_manager, owner_id=OWNER_ID)

    fake_event = SimpleNamespace(
        id="biz_conn_001",
        user=SimpleNamespace(id=OWNER_ID),
        user_chat_id=OWNER_ID,
        can_reply=True,
        is_enabled=True,
        rights=SimpleNamespace(
            can_reply=True,
            can_read_messages=True,
            can_delete_outgoing_messages=True,
            can_delete_all_messages=False,
            can_edit_name=False,
            can_pin_messages=True,
        ),
    )

    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=501))
    mock_bot.delete_business_messages = AsyncMock(return_value=True)

    await conn_manager.handle_connection_update(fake_event, bot=mock_bot)

    # التحقق من الحفظ في قاعدة البيانات
    async with db_manager.session() as session:
        saved = await session.scalar(
            select(BusinessConnection).where(
                BusinessConnection.connection_id == "biz_conn_001"
            )
        )
        assert saved is not None
        assert saved.is_active is True
        assert saved.can_reply is True

    # اختبار الإرسال والحذف عبر BusinessSender
    sender = BusinessSender(bot=mock_bot, db_manager=db_manager)
    send_res = await sender.send_reply(
        business_connection_id="biz_conn_001",
        chat_id=123456,
        content_type="text",
        text="مرحبًا بك في خدمة السكرتير الذكي",
    )
    assert send_res.success is True
    assert send_res.message_id == 501

    del_res = await sender.delete_managed_messages(
        business_connection_id="biz_conn_001",
        message_ids=[500],
    )
    assert del_res.success is True
    mock_bot.delete_business_messages.assert_awaited_once_with(
        business_connection_id="biz_conn_001",
        message_ids=[500],
    )

    await db_manager.close()


@pytest.mark.asyncio
async def test_business_updates_ignores_owner_and_tracks_customer(tmp_path: Path) -> None:
    """التحقق من تجاهل الرسائل اليدوية للمالك وتسجيل رسائل العملاء."""
    db_manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'updates.db'}")
    await init_database(db_manager)

    callback_triggered = AsyncMock()
    processor = BusinessUpdateProcessor(
        db_manager=db_manager,
        owner_id=OWNER_ID,
        on_customer_message=callback_triggered,
    )

    # 1. رسالة يدوية صادرة من المالك داخل محادثة العميل -> يجب تجاهلها
    owner_manual_msg = SimpleNamespace(
        business_connection_id="biz_conn_001",
        from_user=SimpleNamespace(id=OWNER_ID, full_name="Owner", username="owner"),
        chat=SimpleNamespace(id=555666, type="private", full_name="Client", title=None, username="client1"),
    )
    processed_owner = await processor.handle_incoming_business_message(owner_manual_msg)
    assert processed_owner is False
    callback_triggered.assert_not_called()

    # 2. رسالة واردة من العميل -> يجب تسجيل المحادثة واستدعاء محرك السكرتير
    customer_msg = SimpleNamespace(
        business_connection_id="biz_conn_001",
        from_user=SimpleNamespace(id=555666, full_name="أحمد علي", username="ahmed_ali"),
        chat=SimpleNamespace(id=555666, type="private", full_name="أحمد علي", title=None, username="ahmed_ali"),
    )
    processed_customer = await processor.handle_incoming_business_message(customer_msg)
    assert processed_customer is True
    callback_triggered.assert_awaited_once_with(customer_msg)

    async with db_manager.session() as session:
        conv = await session.scalar(
            select(Conversation).where(Conversation.chat_id == 555666)
        )
        assert conv is not None
        assert conv.message_count == 1
        assert conv.title_or_name == "أحمد علي"

    await db_manager.close()
