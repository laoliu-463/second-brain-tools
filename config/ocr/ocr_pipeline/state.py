"""SQLite progress + skipped tables, psutil memory checks."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil


# 内存水位阈值（MB），根据 6GB 总内存 + 系统占 1.5GB 推算
MEMORY_PAUSE_MB = 800     # 低于此暂停派发
MEMORY_SINGLE_MB = 1500   # 仅允许 1 worker
MEMORY_DOUBLE_MB = 2500   # 允许 2 worker
# 更高则允许 3 worker


@dataclass(frozen=True)
class MemoryStatus:
    available_mb: int
    max_workers: int      # 0 表示暂停派发
    total_mb: int

    @property
    def should_pause(self) -> bool:
        return self.max_workers == 0


class MemoryMonitor:
    """查询当前可用内存并推导允许的 worker 数。"""

    def __init__(self, max_workers: int = 3) -> None:
        self.max_workers = max_workers

    def sample(self) -> MemoryStatus:
        mem = psutil.virtual_memory()
        avail_mb = int(mem.available / 1024 / 1024)
        total_mb = int(mem.total / 1024 / 1024)
        if avail_mb < MEMORY_PAUSE_MB:
            workers = 0
        elif avail_mb < MEMORY_SINGLE_MB:
            workers = 1
        elif avail_mb < MEMORY_DOUBLE_MB:
            workers = min(2, self.max_workers)
        else:
            workers = self.max_workers
        return MemoryStatus(available_mb=avail_mb, max_workers=workers, total_mb=total_mb)


class PipelineState:
    """SQLite 持久化层：progress + skipped 两张表。

    progress: 单页处理状态（pending / processing / done / failed）
    skipped:  重试耗尽后永久跳过（reason + 错误样例）
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id TEXT NOT NULL,
        book_title TEXT NOT NULL,
        page INTEGER NOT NULL,
        status TEXT NOT NULL,
        output_path TEXT,
        error_message TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        started_at TEXT,
        finished_at TEXT,
        source_hash TEXT,
        UNIQUE(book_id, page)
    );

    CREATE TABLE IF NOT EXISTS skipped (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id TEXT NOT NULL,
        book_title TEXT NOT NULL,
        page INTEGER NOT NULL,
        reason TEXT NOT NULL,
        attempts INTEGER NOT NULL,
        first_error TEXT,
        last_error TEXT,
        skipped_at TEXT NOT NULL,
        UNIQUE(book_id, page)
    );

    CREATE INDEX IF NOT EXISTS idx_progress_book_status
        ON progress(book_id, status);
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(progress)").fetchall()
            }
            if columns and "book_id" not in columns:
                conn.execute("ALTER TABLE progress RENAME TO progress_legacy")
            conn.executescript(self.SCHEMA)
            # 兜底：老库（缺 source_hash 列）在线 ALTER TABLE
            if columns and "source_hash" not in columns:
                try:
                    conn.execute("ALTER TABLE progress ADD COLUMN source_hash TEXT")
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    # ---------- progress ----------

    def ensure_book_pages(self, book_id: str, book_title: str, pages: int) -> None:
        """首次见到一本书时，初始化所有页为 pending。"""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM progress WHERE book_id=?", (book_id,)
            )
            existing = cur.fetchone()[0]
            if existing < pages:
                self._import_legacy_pages(conn, book_id, book_title)
                existing = conn.execute(
                    "SELECT COUNT(*) FROM progress WHERE book_id=?", (book_id,)
                ).fetchone()[0]
            if existing >= pages:
                return
            now = datetime.now().isoformat(timespec="seconds")
            for page in range(1, pages + 1):
                conn.execute(
                    "INSERT OR IGNORE INTO progress "
                    "(book_id, book_title, page, status, started_at) "
                    "VALUES (?, ?, ?, 'pending', ?)",
                    (book_id, book_title, page, now),
                )
            conn.commit()

    def _import_legacy_pages(
        self, conn: sqlite3.Connection, book_id: str, book_title: str
    ) -> None:
        """Import rows written by the deleted scheduler, matched by book title."""
        legacy = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='progress_legacy'"
        ).fetchone()
        if legacy is None:
            return

        rows = conn.execute(
            "SELECT page, status, output_path, error_message, timestamp, retry_count "
            "FROM progress_legacy WHERE book_name=? ORDER BY page",
            (book_title,),
        ).fetchall()
        for page, status, output_path, error_message, timestamp, retry_count in rows:
            imported_status = (
                "pending" if status in {"failed", "processing"} else status
            )
            started_at = timestamp if status == "processing" else None
            finished_at = timestamp if status in {"done", "failed"} else None
            conn.execute(
                "INSERT OR IGNORE INTO progress "
                "(book_id, book_title, page, status, output_path, error_message, "
                "attempts, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    book_id,
                    book_title,
                    page,
                    imported_status,
                    output_path,
                    error_message,
                    retry_count or 0,
                    started_at,
                    finished_at,
                ),
            )

    def recover_processing(self, book_id: str) -> int:
        """Return interrupted pages to pending after a process restart."""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE progress SET status='pending', started_at=NULL "
                "WHERE book_id=? AND status='processing'",
                (book_id,),
            )
            conn.commit()
            return cur.rowcount

    def reset_book(self, book_id: str) -> dict[str, int]:
        """Requeue every page after generated outputs are invalidated."""
        with self._lock, self._connect() as conn:
            skipped = conn.execute(
                "DELETE FROM skipped WHERE book_id=?", (book_id,)
            ).rowcount
            pages = conn.execute(
                "UPDATE progress SET status='pending', output_path=NULL, "
                "error_message=NULL, attempts=0, started_at=NULL, "
                "finished_at=NULL, source_hash=NULL WHERE book_id=?",
                (book_id,),
            ).rowcount
            conn.commit()
            return {"pages": pages, "skipped": skipped}

    def claim_next_page(self, book_id: str) -> tuple[int, int] | None:
        """原子地认领一个 pending 或 failed-and-under-attempts 的页。

        Returns (page, attempts) or None if nothing to claim.
        """
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "SELECT page, attempts FROM progress "
                "WHERE book_id=? AND status='pending' "
                "ORDER BY page LIMIT 1",
                (book_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            page, attempts = row
            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                "UPDATE progress SET status='processing', started_at=? "
                "WHERE book_id=? AND page=?",
                (now, book_id, page),
            )
            conn.commit()
            return page, attempts

    def mark_done(
        self,
        book_id: str,
        page: int,
        output_path: str,
        source_hash: str | None = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                "UPDATE progress SET status='done', output_path=?, "
                "finished_at=?, error_message=NULL, source_hash=? "
                "WHERE book_id=? AND page=?",
                (output_path, now, source_hash, book_id, page),
            )
            conn.commit()

    def skip_if_hash_matches(
        self, book_id: str, page: int, current_hash: str
    ) -> bool:
        """源 PDF 未变（SHA-256 与上次 done 记录一致）→ 跳过这一页。

        用于 ingest_other_pdfs 的整书级 dedup：先查 page=1，所有页共享同一源
        PDF（hash 相同），所以 page=1 命中即代表整书可跳。
        """
        if not current_hash:
            return False
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT source_hash FROM progress "
                "WHERE book_id=? AND page=? AND status='done'",
                (book_id, page),
            ).fetchone()
        return bool(row and row[0] and row[0].upper() == current_hash.upper())

    def mark_failed_retryable(self, book_id: str, page: int, error: str) -> int:
        """单次失败但仍可重试。返回当前 attempts 数。"""
        with self._lock, self._connect() as conn:
            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                "UPDATE progress SET status='pending', attempts=attempts+1, "
                "error_message=?, finished_at=? "
                "WHERE book_id=? AND page=?",
                (error[:500], now, book_id, page),
            )
            row = conn.execute(
                "SELECT attempts FROM progress WHERE book_id=? AND page=?",
                (book_id, page),
            ).fetchone()
            conn.commit()
            return row[0] if row else 0

    def mark_failed_terminal(
        self, book_id: str, page: int, error: str, *, increment: bool = True
    ) -> int:
        """彻底失败，移到 skipped 表。返回总 attempts。"""
        with self._lock, self._connect() as conn:
            now = datetime.now().isoformat(timespec="seconds")
            row = conn.execute(
                "SELECT attempts, error_message, book_title FROM progress "
                "WHERE book_id=? AND page=?",
                (book_id, page),
            ).fetchone()
            if row is None:
                return 0
            current_attempts, previous_error, title = row
            attempts = current_attempts + (1 if increment else 0)
            conn.execute(
                "UPDATE progress SET status='skipped', attempts=?, "
                "error_message=?, finished_at=? "
                "WHERE book_id=? AND page=?",
                (attempts, error[:500], now, book_id, page),
            )
            conn.execute(
                "INSERT OR REPLACE INTO skipped "
                "(book_id, book_title, page, reason, attempts, "
                " first_error, last_error, skipped_at) "
                "VALUES (?, ?, ?, 'ocr_failed', ?, ?, ?, ?)",
                (
                    book_id,
                    title,
                    page,
                    attempts,
                    previous_error or error[:500],
                    error[:500],
                    now,
                ),
            )
            conn.commit()
            return attempts

    def book_summary(self, book_id: str) -> dict[str, int]:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "SELECT status, COUNT(*) FROM progress "
                "WHERE book_id=? GROUP BY status",
                (book_id,),
            )
            return {row[0]: row[1] for row in cur.fetchall()}

    def all_book_ids(self) -> list[str]:
        with self._lock, self._connect() as conn:
            return [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT book_id FROM progress ORDER BY book_id"
                ).fetchall()
            ]
