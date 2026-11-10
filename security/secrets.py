from cryptography.fernet import Fernet, InvalidToken


class SecretManager:
    """تشفير وفك تشفير بيانات جلسة الحساب الشخصي (Telethon StringSession)."""

    def __init__(self, encryption_key: str) -> None:
        self._fernet = Fernet(encryption_key.encode("utf-8"))

    def encrypt_session(self, raw_session_string: str) -> str:
        """تشفير نص الجلسة قبل حفظه في قاعدة البيانات."""
        if not raw_session_string:
            raise ValueError("لا يمكن تشفير جلسة فارغة.")
        token = self._fernet.encrypt(raw_session_string.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt_session(self, encrypted_session_string: str) -> str:
        """فك تشفير نص الجلسة عند تشغيل عميل الحساب الشخصي."""
        if not encrypted_session_string:
            raise ValueError("لا توجد بيانات جلسة مشفرة لفكها.")
        try:
            raw_bytes = self._fernet.decrypt(
                encrypted_session_string.encode("utf-8")
            )
            return raw_bytes.decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError(
                "فشل فك تشفير جلسة الحساب الشخصي: مفتاح التشفير غير مطابق أو البيانات تالفة."
            ) from exc

    @staticmethod
    def mask_phone(phone_number: str) -> str:
        """إخفاء رقم الهاتف للعرض الآمن في لوحة التحكم (مثال: +967******89)."""
        cleaned = phone_number.strip()
        if len(cleaned) <= 5:
            return "*****"
        return f"{cleaned[:4]}{'*' * (len(cleaned) - 6)}{cleaned[-2:]}"
