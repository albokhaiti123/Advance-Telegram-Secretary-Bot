import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from sqlalchemy import text

from database.database import DatabaseManager

logger = logging.getLogger("secretary_bot.utils.helpers")


class SingleInstanceLock:
    """
    قفل ملف نظام (PID Lock) يضمن عدم تشغيل أكثر من نسخة واحدة من البرنامج
    لحماية جلسة الحساب الشخصي (Telethon) من التضارب.
    """

    def __init__(self, lock_file_path: Path) -> None:
        self.lock_file_path = lock_file_path
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        """محاولة حجز القفل؛ يرجع False إذا كانت هناك نسخة أخرى قيد التشغيل."""
        self.lock_file_path.parent.mkdir(parents=True, exist_ok=True)

        if self.lock_file_path.exists():
            try:
                existing_pid = int(self.lock_file_path.read_text(encoding="utf-8").strip())
                if self._is_pid_running(existing_pid) and existing_pid != os.getpid():
                    return False
            except Exception:
                pass

        try:
            self.lock_file_path.write_text(str(os.getpid()), encoding="utf-8")
            return True
        except Exception as exc:
            logger.error("تعذر كتابة ملف القفل %s: %s", self.lock_file_path, exc)
            return False

    def release(self) -> None:
        """تحرير ملف القفل عند إيقاف البرنامج."""
        try:
            if self.lock_file_path.exists():
                recorded_pid = int(self.lock_file_path.read_text(encoding="utf-8").strip())
                if recorded_pid == os.getpid():
                    self.lock_file_path.unlink(missing_ok=True)
        except Exception:
            pass

    @staticmethod
    def _is_pid_running(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False


class DatabaseBackupManager:
    """
    إدارة النسخ الاحتياطي والاستعادة وفحص سلامة قاعدة البيانات SQLite.
    """

    def __init__(
        self,
        db_file_path: Path,
        backup_dir: Path,
        max_backups_to_keep: int = 10,
    ) -> None:
        self.db_file_path = db_file_path
        self.backup_dir = backup_dir
        self.max_backups_to_keep = max_backups_to_keep
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create_backup(self, tag: str = "manual") -> Path:
        """إنشاء نسخة احتياطية ذرية آمنة باستخدام SQLite Online Backup API."""
        if not self.db_file_path.exists():
            raise FileNotFoundError(f"ملف قاعدة البيانات غير موجود: {self.db_file_path}")

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = self.backup_dir / f"secretary_bot_{tag}_{ts}.db"

        src_conn = sqlite3.connect(str(self.db_file_path))
        dst_conn = sqlite3.connect(str(backup_path))
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

        self._rotate_old_backups()
        return backup_path

    def restore_from_backup(self, backup_file_path: Path) -> bool:
        """استعادة قاعدة البيانات من ملف نسخة احتياطية بعد التحقق من سلامته."""
        if not backup_file_path.exists():
            raise FileNotFoundError(f"ملف النسخة الاحتياطية غير موجود: {backup_file_path}")

        # فحص سلامة النسخة الاحتياطية قبل الاستعادة
        check_conn = sqlite3.connect(str(backup_file_path))
        try:
            integrity = check_conn.execute("PRAGMA integrity_check;").fetchone()
            if not integrity or str(integrity[0]).lower() != "ok":
                raise RuntimeError("ملف النسخة الاحتياطية تالف ولا يمكن استعادته.")
        finally:
            check_conn.close()

        src_conn = sqlite3.connect(str(backup_file_path))
        dst_conn = sqlite3.connect(str(self.db_file_path))
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

        return True

    def list_backups(self) -> List[Path]:
        return sorted(
            self.backup_dir.glob("secretary_bot_*.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    def _rotate_old_backups(self) -> None:
        backups = self.list_backups()
        for old_file in backups[self.max_backups_to_keep :]:
            try:
                old_file.unlink(missing_ok=True)
            except Exception:
                pass

    @staticmethod
    async def check_database_integrity(db_manager: DatabaseManager) -> dict:
        """فحص سلامة قاعدة البيانات ووضع WAL."""
        async with db_manager.engine.connect() as conn:
            integrity = (await conn.execute(text("PRAGMA integrity_check;"))).scalar()
            journal = (await conn.execute(text("PRAGMA journal_mode;"))).scalar()
            fk = (await conn.execute(text("PRAGMA foreign_keys;"))).scalar()

        return {
            "integrity_ok": str(integrity).lower() == "ok",
            "journal_mode": str(journal).lower(),
            "foreign_keys_enabled": bool(fk),
        }
