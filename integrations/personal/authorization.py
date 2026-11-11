import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import delete
from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from database.database import DatabaseManager
from database.models import ActivityLog, PersonalAccountSession
from integrations.personal.client import PersonalClientManager
from security.secrets import SecretManager

logger = logging.getLogger("secretary_bot.personal.auth")


@dataclass
class AuthStepResult:
    status: str  # code_sent | need_2fa | authorized | error
    message: str
    phone_masked: Optional[str] = None
    telegram_user_id: Optional[int] = None
    username: Optional[str] = None


class PersonalAuthService:
    """إدارة عملية تسجيل الدخول التفاعلية للحساب الشخصي وتشفير الجلسة بـ Fernet."""

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        db_manager: DatabaseManager,
        secret_manager: SecretManager,
        client_manager: PersonalClientManager,
    ) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.db_manager = db_manager
        self.secret_manager = secret_manager
        self.client_manager = client_manager

        self._temp_client: Optional[TelegramClient] = None
        self._pending_phone: Optional[str] = None
        self._phone_code_hash: Optional[str] = None

    async def start_login(self, phone_number: str) -> AuthStepResult:
        """الخطوة 1: إرسال طلب رمز التحقق إلى رقم الهاتف."""
        await self._cleanup_temp_client()
        cleaned_phone = phone_number.strip().replace(" ", "")

        if int(self.api_id) == 12345678 or "0123456789abcdef" in self.api_hash.lower():
            return AuthStepResult(
                status="error",
                message=(
                    "قيم TG_API_ID و TG_API_HASH في ملف .env ما زالت القيم التجريبية.\n"
                    "تم تحديث ملف .env تلقائيًا، يرجى إعادة تشغيل البوت والمحاولة مجددًا."
                ),
            )

        try:
            self._temp_client = TelegramClient(
                StringSession(),
                int(self.api_id),
                self.api_hash.strip().strip('"').strip("'"),
                device_model="SecretaryBot Ubuntu VPS",
                system_version="Linux 6.8",
                app_version="1.0.0",
            )
            await self._temp_client.connect()
            sent = await self._temp_client.send_code_request(cleaned_phone)
            self._pending_phone = cleaned_phone
            self._phone_code_hash = sent.phone_code_hash

            masked = self.secret_manager.mask_phone(cleaned_phone)
            return AuthStepResult(
                status="code_sent",
                message=f"تم إرسال رمز التحقق إلى الرقم {masked}.",
                phone_masked=masked,
            )
        except ApiIdInvalidError:
            await self._cleanup_temp_client()
            return AuthStepResult(
                status="error",
                message="خطأ ApiIdInvalidError: تأكد من صحة TG_API_ID و TG_API_HASH في ملف .env.",
            )
        except Exception as exc:
            await self._cleanup_temp_client()
            return AuthStepResult(
                status="error",
                message=f"تعذر إرسال رمز التحقق: {type(exc).__name__}",
            )

    async def submit_code(self, raw_code: str) -> AuthStepResult:
        """الخطوة 2: التحقق من الرمز المرسل."""
        if not self._temp_client or not self._pending_phone or not self._phone_code_hash:
            return AuthStepResult(
                status="error",
                message="لا توجد جلسة تفويض جارية؛ يرجى البدء بإرسال رقم الهاتف مجددًا.",
            )

        clean_code = "".join(ch for ch in raw_code if ch.isdigit())

        try:
            user = await self._temp_client.sign_in(
                phone=self._pending_phone,
                code=clean_code,
                phone_code_hash=self._phone_code_hash,
            )
            return await self._finalize_authorized_session(user)
        except SessionPasswordNeededError:
            return AuthStepResult(
                status="need_2fa",
                message="الحساب محمي بالتحقق بخطوتين (2FA). أرسل الآن كلمة المرور السحابية:",
            )
        except PhoneCodeInvalidError:
            return AuthStepResult(
                status="error",
                message="رمز التحقق غير صحيح. يرجى التأكد وإعادة المحاولة.",
            )
        except PhoneCodeExpiredError:
            await self._cleanup_temp_client()
            return AuthStepResult(
                status="error",
                message="انتهت صلاحية رمز التحقق. يرجى إعادة بدء عملية الربط وإرسال الرمز مفصولًا بمسافات.",
            )
        except Exception as exc:
            await self._cleanup_temp_client()
            return AuthStepResult(
                status="error",
                message=f"فشل التحقق من الرمز: {type(exc).__name__}",
            )

    async def submit_2fa_password(self, password: str) -> AuthStepResult:
        """الخطوة 3: إكمال تسجيل الدخول باستخدام كلمة مرور التحقق بخطوتين (2FA)."""
        if not self._temp_client or not self._pending_phone:
            return AuthStepResult(
                status="error",
                message="انتهت الجلسة المؤقتة؛ يرجى إعادة بدء التفويض.",
            )
        try:
            user = await self._temp_client.sign_in(password=password)
            return await self._finalize_authorized_session(user)
        except PasswordHashInvalidError:
            return AuthStepResult(
                status="error",
                message="كلمة مرور التحقق بخطوتين غير صحيحة.",
            )
        except Exception as exc:
            await self._cleanup_temp_client()
            return AuthStepResult(
                status="error",
                message=f"فشل التحقق من كلمة المرور: {type(exc).__name__}",
            )

    async def _finalize_authorized_session(self, tg_user: object) -> AuthStepResult:
        assert self._temp_client is not None
        assert self._pending_phone is not None

        raw_session_str = self._temp_client.session.save()
        encrypted_str = self.secret_manager.encrypt_session(raw_session_str)
        masked_phone = self.secret_manager.mask_phone(self._pending_phone)
        tg_uid = getattr(tg_user, "id", None)
        tg_username = getattr(tg_user, "username", None)
        now = datetime.now(timezone.utc)

        await self._cleanup_temp_client()

        async with self.db_manager.session() as session:
            await session.execute(delete(PersonalAccountSession))
            session.add(
                PersonalAccountSession(
                    phone_masked=masked_phone,
                    telegram_user_id=tg_uid,
                    username=tg_username,
                    encrypted_session=encrypted_str,
                    is_authorized=True,
                    status_message="متصل ومفوض بنجاح",
                    last_verified_at=now,
                )
            )
            session.add(
                ActivityLog(
                    category="connection",
                    action="personal_account_authorized",
                    target_id=str(tg_uid),
                    details_json={
                        "phone_masked": masked_phone,
                        "username": tg_username,
                    },
                )
            )

        return AuthStepResult(
            status="authorized",
            message="تم ربط وتفويض الحساب الشخصي وتشفير الجلسة بنجاح.",
            phone_masked=masked_phone,
            telegram_user_id=tg_uid,
            username=tg_username,
        )

    async def logout_and_clear_session(self) -> bool:
        """فصل جلسة الحساب الشخصي بطريقة آمنة وحذفها من قاعدة البيانات."""
        client = await self.client_manager.get_authorized_client()
        if client:
            try:
                await client.log_out()
            except Exception:
                pass
        await self.client_manager.disconnect_safely()

        async with self.db_manager.session() as session:
            await session.execute(delete(PersonalAccountSession))
            session.add(
                ActivityLog(
                    category="connection",
                    action="personal_account_logged_out",
                )
            )
        return True

    async def _cleanup_temp_client(self) -> None:
        if self._temp_client is not None:
            try:
                await self._temp_client.disconnect()
            except Exception:
                pass
            self._temp_client = None
        self._pending_phone = None
        self._phone_code_hash = None
