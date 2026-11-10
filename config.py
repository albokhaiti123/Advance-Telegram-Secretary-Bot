from pathlib import Path
from typing import Optional
from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """إعدادات النظام المركزية مع التحقق الصارم من القيم."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # إعدادات البوت والمالك
    BOT_TOKEN: str = Field(..., min_length=20, description="توكن بوت الإدارة")
    OWNER_ID: int = Field(..., gt=0, description="المعرف الرقمي لمالك النظام")

    # إعدادات MTProto للحساب الشخصي
    TG_API_ID: int = Field(..., gt=0, description="Telegram API ID")
    TG_API_HASH: str = Field(..., min_length=16, description="Telegram API Hash")

    # مفتاح التشفير للجلسة الشخصية
    SECRET_ENCRYPTION_KEY: str = Field(
        default_factory=lambda: Fernet.generate_key().decode("utf-8"),
        description="مفتاح Fernet لتشفير بيانات الجلسة في قاعدة البيانات",
    )

    # المسارات وقاعدة البيانات
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./data/secretary_bot.db"
    )
    TIMEZONE: str = Field(default="Asia/Aden")
    LOG_LEVEL: str = Field(default="INFO")

    DATA_DIR: Path = Field(default=Path("./data"))
    MEDIA_CACHE_DIR: Path = Field(default=Path("./data/media_cache"))
    BACKUP_DIR: Path = Field(default=Path("./backups"))
    LOG_DIR: Path = Field(default=Path("./logs"))
    PID_LOCK_FILE: Path = Field(default=Path("./data/secretary_bot.lock"))

    # ضوابط الإرسال والموثوقية
    BROADCAST_MIN_DELAY_SECONDS: float = Field(default=5.0, ge=1.0)
    BROADCAST_MAX_DELAY_SECONDS: float = Field(default=12.0, ge=2.0)
    MAX_DELETE_RETRIES: int = Field(default=3, ge=1, le=10)
    MAX_TASK_RETRIES: int = Field(default=3, ge=1, le=10)

    @field_validator("SECRET_ENCRYPTION_KEY")
    @classmethod
    def validate_fernet_key(cls, value: str) -> str:
        try:
            Fernet(value.encode("utf-8"))
        except Exception as exc:
            raise ValueError(
                "قيمة SECRET_ENCRYPTION_KEY غير صالحة؛ يجب أن تكون مفتاح Fernet بصيغة Base64 مكونًا من 32 بايت."
            ) from exc
        return value

    def ensure_directories(self) -> None:
        """إنشاء المجلدات اللازمة للتشغيل بأمان."""
        for directory in (
            self.DATA_DIR,
            self.MEDIA_CACHE_DIR,
            self.BACKUP_DIR,
            self.LOG_DIR,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def load_settings(env_file: Optional[str] = ".env") -> Settings:
    """تحميل الإعدادات مع تهيئة المجلدات الأساسية."""
    settings = Settings(_env_file=env_file) if env_file else Settings()
    settings.ensure_directories()
    return settings
