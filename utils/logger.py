import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable, List


class SecretScrubberFilter(logging.Filter):
    """فلتر أمني يحجب التوكنات، مفاتيح التشفير، وأكواد التحقق من السجلات."""

    TOKEN_PATTERN = re.compile(r"\b\d{8,11}:[A-Za-z0-9_-]{30,}\b")
    OTP_PATTERN = re.compile(
        r"(?i)(code|otp|password|2fa|رمز التحقق|كلمة المرور)[\s:=]+[^\s,]+"
    )

    def __init__(self, explicit_secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.explicit_secrets: List[str] = [
            s for s in explicit_secrets if s and len(s) >= 6
        ]

    def sanitize(self, text: str) -> str:
        if not text:
            return text
        sanitized = self.TOKEN_PATTERN.sub("[REDACTED_BOT_TOKEN]", text)
        sanitized = self.OTP_PATTERN.sub(r"\1=[REDACTED_SECRET]", sanitized)
        for secret in self.explicit_secrets:
            if secret in sanitized:
                sanitized = sanitized.replace(secret, "[REDACTED_SECRET]")
        return sanitized

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.sanitize(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self.sanitize(str(v)) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self.sanitize(str(a)) if isinstance(a, str) else a
                    for a in record.args
                )
        return True


def setup_logging(
    log_dir: Path,
    log_level: str = "INFO",
    secrets_to_scrub: Iterable[str] = (),
) -> logging.Logger:
    """تهيئة نظام التسجيل الموحد مع تدوير الملفات وحجب الأسرار."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "secretary_bot.log"

    root_logger = logging.getLogger("secretary_bot")
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    root_logger.handlers.clear()

    scrubber = SecretScrubberFilter(explicit_secrets=secrets_to_scrub)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(scrubber)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(scrubber)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return root_logger
