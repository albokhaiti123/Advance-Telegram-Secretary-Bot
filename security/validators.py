import re
from datetime import datetime
from typing import Optional, Tuple
from urllib.parse import urlparse


class InputValidator:
    """التحقق من صحة المدخلات (Regex، روابط الأزرار، أوقات الدوام) لمنع الأخطاء والثغرات."""

    MAX_REGEX_LENGTH = 500
    ALLOWED_URL_SCHEMES = {"http", "https", "tg"}

    @classmethod
    def validate_regex(cls, pattern: str) -> Tuple[bool, Optional[str]]:
        """التحقق من صحة التعبير النمطي وحمايته من التعقيد الكارثي (ReDoS)."""
        if not pattern or not pattern.strip():
            return False, "التعبير النمطي لا يمكن أن يكون فارغًا."
        if len(pattern) > cls.MAX_REGEX_LENGTH:
            return False, f"طول التعبير النمطي يتجاوز الحد المسموح ({cls.MAX_REGEX_LENGTH} حرف)."

        # فحص أنماط التداخل الخطر مثل (a+)+ أو (.*)*
        if re.search(r"\([^)]*[\+\*][^)]*\)[\+\*]", pattern):
            return False, "التعبير النمطي يحتوي على تكرار متداخل قد يسبب بطء النظام (ReDoS)."

        try:
            re.compile(pattern)
            return True, None
        except re.error as exc:
            return False, f"خطأ في صيغة التعبير النمطي: {exc}"

    @classmethod
    def validate_button_url(cls, url: str) -> Tuple[bool, Optional[str]]:
        """التحقق من صلاحية رابط زر Inline Keyboard."""
        if not url or not url.strip():
            return False, "الرابط فارغ."
        cleaned = url.strip()
        try:
            parsed = urlparse(cleaned)
            if parsed.scheme.lower() not in cls.ALLOWED_URL_SCHEMES:
                return False, "يجب أن يبدأ الرابط بـ https:// أو http:// أو tg://"
            if parsed.scheme.lower() in {"http", "https"} and not parsed.netloc:
                return False, "الرابط لا يحتوي على نطاق (Domain) صالح."
            return True, None
        except Exception as exc:
            return False, f"رابط غير صالح: {exc}"

    @staticmethod
    def validate_time_hhmm(time_str: str) -> bool:
        """التحقق من صيغة الوقت HH:MM بنظام 24 ساعة."""
        try:
            datetime.strptime(time_str.strip(), "%H:%M")
            return True
        except ValueError:
            return False

    validate_hhmm_time = validate_time_hhmm
