import asyncio
import logging
import sys
from pathlib import Path
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.handlers.account import account_router
from bot.handlers.broadcasts import broadcasts_router
from bot.handlers.conversations import conversations_router
from bot.handlers.dashboard import dashboard_router
from bot.handlers.rules import rules_router
from bot.handlers.scheduler import scheduler_router
from bot.handlers.settings import settings_router
from bot.handlers.start import start_router
from bot.handlers.statistics import statistics_router
from bot.handlers.templates import templates_router
from bot.middlewares.error_handler import ErrorHandlerMiddleware
from bot.middlewares.owner_only import OwnerOnlyMiddleware
from config import Settings, load_settings
from database.database import DatabaseManager
from database.migrations import init_database
from integrations.business.connection import (
    BusinessConnectionManager,
    business_connection_router,
)
from integrations.business.sender import BusinessSender
from integrations.business.updates import (
    BusinessUpdateProcessor,
    business_updates_router,
)
from integrations.personal.account_monitor import PersonalAccountMonitor
from integrations.personal.authorization import PersonalAuthService
from integrations.personal.client import PersonalClientManager
from integrations.personal.dialogs import PersonalDialogsInspector
from integrations.personal.group_sender import PersonalGroupSender
from scheduler.manager import UnifiedSchedulerManager
from scheduler.task_recovery import TaskRecoveryService
from security.access_control import AccessControl
from security.secrets import SecretManager
from services.auto_reply import AutoReplyService
from services.broadcast_manager import BroadcastManager
from services.campaign_manager import CampaignManager
from services.conversation_manager import ConversationManager
from services.group_manager import GroupManager
from services.media_manager import MediaManager
from services.message_processor import MessageProcessor
from services.notification_manager import NotificationManager
from services.response_replacement import ResponseReplacementManager
from services.rule_engine import RuleEngine
from services.statistics import StatisticsService
from services.template_manager import TemplateManager
from utils.helpers import DatabaseBackupManager, SingleInstanceLock
from utils.logger import setup_logging


def resolve_sqlite_file_path(database_url: str) -> Path:
    """استخراج مسار ملف SQLite الفعلي من رابط الاتصال."""
    prefix = "sqlite+aiosqlite:///"
    if database_url.startswith(prefix):
        return Path(database_url[len(prefix) :]).resolve()
    return Path("./data/secretary_bot.db").resolve()


async def run_application(settings: Settings) -> None:
    logger = setup_logging(
        log_dir=settings.LOG_DIR,
        log_level=settings.LOG_LEVEL,
        secrets_to_scrub=[
            settings.BOT_TOKEN,
            settings.TG_API_HASH,
            settings.SECRET_ENCRYPTION_KEY,
        ],
    )

    # 1. حجز قفل العملية الأحادي لمنع تشغيل نسختين متعارضتين من عميل الحساب الشخصي
    instance_lock = SingleInstanceLock(settings.PID_LOCK_FILE)
    if not instance_lock.acquire():
        logger.critical(
            "يوجد مثيل آخر من Telegram Secretary Bot قيد التشغيل بالفعل! إيقاف التشغيل لمنع تضارب جلسة الحساب الشخصي."
        )
        sys.exit(1)

    # 2. تهيئة قاعدة البيانات وتفعيل وضع WAL
    db_manager = DatabaseManager(settings.DATABASE_URL)
    await init_database(db_manager)

    db_file_path = resolve_sqlite_file_path(settings.DATABASE_URL)
    backup_manager = DatabaseBackupManager(
        db_file_path=db_file_path,
        backup_dir=settings.BACKUP_DIR,
    )

    # 3. تهيئة خدمات الأمان والتشفير
    secret_manager = SecretManager(settings.SECRET_ENCRYPTION_KEY)
    access_control = AccessControl(owner_id=settings.OWNER_ID, db_manager=db_manager)

    # 4. تهيئة البوت ومكونات الأعمال (Telegram Business)
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    notification_manager = NotificationManager(
        db_manager=db_manager,
        owner_id=settings.OWNER_ID,
        bot=bot,
    )
    business_connection_manager = BusinessConnectionManager(
        db_manager=db_manager,
        owner_id=settings.OWNER_ID,
    )
    business_sender = BusinessSender(bot=bot, db_manager=db_manager)

    # 5. تهيئة عميل الحساب الشخصي (Telethon MTProto)
    personal_client_manager = PersonalClientManager(
        api_id=settings.TG_API_ID,
        api_hash=settings.TG_API_HASH,
        db_manager=db_manager,
        secret_manager=secret_manager,
    )
    personal_auth_service = PersonalAuthService(
        api_id=settings.TG_API_ID,
        api_hash=settings.TG_API_HASH,
        db_manager=db_manager,
        secret_manager=secret_manager,
        client_manager=personal_client_manager,
    )
    personal_monitor = PersonalAccountMonitor(
        db_manager=db_manager,
        client_manager=personal_client_manager,
    )
    dialogs_inspector = PersonalDialogsInspector(
        db_manager=db_manager,
        client_manager=personal_client_manager,
    )
    personal_group_sender = PersonalGroupSender(
        db_manager=db_manager,
        client_manager=personal_client_manager,
    )

    # 6. تهيئة محرك السكرتير وخدمات القواعد والقوالب والاستبدال والحملات
    media_manager = MediaManager(
        db_manager=db_manager,
        media_cache_dir=settings.MEDIA_CACHE_DIR,
    )
    template_manager = TemplateManager(
        db_manager=db_manager,
        timezone_str=settings.TIMEZONE,
    )
    rule_engine = RuleEngine(
        db_manager=db_manager,
        timezone_str=settings.TIMEZONE,
    )
    replacement_manager = ResponseReplacementManager(
        db_manager=db_manager,
        business_sender=business_sender,
        max_delete_retries=settings.MAX_DELETE_RETRIES,
    )
    auto_reply_service = AutoReplyService(
        db_manager=db_manager,
        rule_engine=rule_engine,
        template_manager=template_manager,
        replacement_manager=replacement_manager,
    )
    message_processor = MessageProcessor(
        db_manager=db_manager,
        auto_reply_service=auto_reply_service,
        template_manager=template_manager,
        replacement_manager=replacement_manager,
    )
    business_update_processor = BusinessUpdateProcessor(
        db_manager=db_manager,
        owner_id=settings.OWNER_ID,
        on_customer_message=message_processor.handle_customer_message,
    )

    conversation_manager = ConversationManager(db_manager=db_manager)
    group_manager = GroupManager(db_manager=db_manager)
    campaign_manager = CampaignManager(
        db_manager=db_manager,
        group_manager=group_manager,
    )
    broadcast_manager = BroadcastManager(
        db_manager=db_manager,
        group_sender=personal_group_sender,
        min_delay_seconds=settings.BROADCAST_MIN_DELAY_SECONDS,
        max_delay_seconds=settings.BROADCAST_MAX_DELAY_SECONDS,
    )
    statistics_service = StatisticsService(db_manager=db_manager)

    # 7. تشغيل محرك المهام المجدولة واستعادة المهام بعد إعادة التشغيل
    scheduler_manager = UnifiedSchedulerManager(
        db_manager=db_manager,
        business_sender=business_sender,
        replacement_manager=replacement_manager,
        campaign_manager=campaign_manager,
        broadcast_manager=broadcast_manager,
        timezone_str=settings.TIMEZONE,
    )
    scheduler_manager.start()

    recovery_service = TaskRecoveryService(
        db_manager=db_manager,
        scheduler_manager=scheduler_manager,
    )
    recovery_stats = await recovery_service.recover_all_on_startup()
    logger.info("اكتملت استعادة المهام عند الإقلاع: %s", recovery_stats)

    # 8. إعداد Dispatcher وربط Middlewares والـ Routers
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(ErrorHandlerMiddleware(db_manager))
    dp.callback_query.middleware(ErrorHandlerMiddleware(db_manager))
    dp.message.middleware(OwnerOnlyMiddleware(access_control))
    dp.callback_query.middleware(OwnerOnlyMiddleware(access_control))

    # تمرير الاعتماديات (Dependency Injection) لجميع المعالجات
    dp["db_manager"] = db_manager
    dp["backup_manager"] = backup_manager
    dp["access_control"] = access_control
    dp["business_connection_manager"] = business_connection_manager
    dp["business_update_processor"] = business_update_processor
    dp["personal_auth_service"] = personal_auth_service
    dp["personal_monitor"] = personal_monitor
    dp["dialogs_inspector"] = dialogs_inspector
    dp["media_manager"] = media_manager
    dp["template_manager"] = template_manager
    dp["message_processor"] = message_processor
    dp["conversation_manager"] = conversation_manager
    dp["group_manager"] = group_manager
    dp["campaign_manager"] = campaign_manager
    dp["broadcast_manager"] = broadcast_manager
    dp["statistics_service"] = statistics_service
    dp["notification_manager"] = notification_manager

    dp.include_routers(
        business_connection_router,
        business_updates_router,
        start_router,
        dashboard_router,
        rules_router,
        templates_router,
        conversations_router,
        broadcasts_router,
        scheduler_router,
        statistics_router,
        settings_router,
        account_router,
    )

    try:
        logger.info("بدأ تشغيل نظام Telegram Secretary Bot بنجاح...")
        await dp.start_polling(
            bot,
            allowed_updates=[
                "message",
                "callback_query",
                "business_connection",
                "business_message",
                "edited_business_message",
                "deleted_business_messages",
            ],
        )
    finally:
        logger.info("جاري إيقاف النظام بأمان...")
        scheduler_manager.shutdown()
        await personal_client_manager.disconnect_safely()
        await bot.session.close()
        await db_manager.close()
        instance_lock.release()


if __name__ == "__main__":
    app_settings = load_settings()
    try:
        asyncio.run(run_application(app_settings))
    except (KeyboardInterrupt, SystemExit):
        pass
