import logging
from pathlib import Path
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import inspect, select, text

from database.database import DatabaseManager
from database.migrations import init_database
from database.models import SystemSetting
from security.secrets import SecretManager
from utils.logger import SecretScrubberFilter


@pytest.mark.asyncio
async def test_database_tables_and_wal_mode(tmp_path: Path) -> None:
    """التحقق من إنشاء جميع الـ 23 جدولًا وتفعيل وضع WAL في SQLite."""
    db_file = tmp_path / "test_secretary.db"
    db_manager = DatabaseManager(f"sqlite+aiosqlite:///{db_file}")

    await init_database(db_manager)

    async with db_manager.engine.connect() as conn:
        table_names = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )
        journal_mode = (await conn.execute(text("PRAGMA journal_mode;"))).scalar()
        foreign_keys = (await conn.execute(text("PRAGMA foreign_keys;"))).scalar()

    assert len(table_names) == 23
    assert str(journal_mode).lower() == "wal"
    assert foreign_keys == 1

    async with db_manager.session() as session:
        master_setting = await session.scalar(
            select(SystemSetting).where(
                SystemSetting.key == "auto_reply_master_enabled"
            )
        )
        assert master_setting is not None
        assert master_setting.value_json["enabled"] is True

    await db_manager.close()


def test_secret_manager_encryption_and_masking() -> None:
    """التحقق من تشفير وفك تشفير جلسة الحساب الشخصي وإخفاء رقم الهاتف."""
    key = Fernet.generate_key().decode("utf-8")
    manager = SecretManager(key)

    raw_session = "1BVtsOHwBu7_test_telethon_string_session_secret_payload"
    encrypted = manager.encrypt_session(raw_session)

    assert encrypted != raw_session
    assert manager.decrypt_session(encrypted) == raw_session
    assert manager.mask_phone("+967771234567") == "+967*******67"


def test_logger_secret_scrubber() -> None:
    """التحقق من عدم تسرب توكن البوت أو رمز التحقق في السجلات."""
    scrubber = SecretScrubberFilter(explicit_secrets=["my_super_secret_session"])
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=1,
        msg="Bot token 123456789:AAHabcdefghijklmnopqrstuvwx12345678 and code=94821 and my_super_secret_session",
        args=(),
        exc_info=None,
    )
    scrubber.filter(record)

    assert "AAHabcdefghijklmnopqrstuvwx12345678" not in record.msg
    assert "94821" not in record.msg
    assert "my_super_secret_session" not in record.msg
    assert "[REDACTED_BOT_TOKEN]" in record.msg
