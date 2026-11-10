from sqlalchemy import select
from database.database import DatabaseManager
from database.models import Base, SystemSetting

DEFAULT_SYSTEM_SETTINGS = {
    "auto_reply_master_enabled": {
        "value": {"enabled": True},
        "description": "التفعيل العام لمحرك الردود التلقائية",
    },
    "work_hours": {
        "value": {
            "enabled": False,
            "timezone": "Asia/Aden",
            "days": [0, 1, 2, 3, 4, 6],  # السبت إلى الخميس
            "start_time": "09:00",
            "end_time": "17:00",
        },
        "description": "جدول ساعات العمل الرسمية",
    },
    "away_mode": {
        "value": {
            "enabled": False,
            "template_id": None,
            "cooldown_seconds": 3600,
            "only_outside_work_hours": True,
        },
        "description": "إعدادات وضع الغياب",
    },
    "greeting_message": {
        "value": {
            "enabled": False,
            "template_id": None,
            "cooldown_days": 30,
        },
        "description": "إعدادات رسالة الترحيب التلقائية",
    },
}


async def init_database(db_manager: DatabaseManager) -> None:
    """إنشاء الجداول الـ 23 وإدراج الإعدادات الافتراضية عند عدم وجودها."""
    async with db_manager.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with db_manager.session() as session:
        for key, data in DEFAULT_SYSTEM_SETTINGS.items():
            existing = await session.scalar(
                select(SystemSetting).where(SystemSetting.key == key)
            )
            if existing is None:
                session.add(
                    SystemSetting(
                        key=key,
                        value_json=data["value"],
                        description=data["description"],
                    )
                )
