"""线程池 + 内存节流的派发器。"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable

from .manifest import BookEntry
from .ocr import OcrResult, OcrRunner
from .state import MemoryMonitor, MemoryStatus, PipelineState


logger = logging.getLogger(__name__)


def sha256_file(path: Path, chunk_bytes: int = 8 * 1024 * 1024) -> str:
    """分块读 SHA-256（8MB 块），统一输出大写 hex。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_bytes), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


class PageWorker:
    """一页一个 future：派发 → 内存节流 → 重试 → 永久跳过。

    工作模型：
    - 主线程负责按时间序"取一个 pending 页"
    - ThreadPoolExecutor 真正跑 ocrmypdf 子进程
    - 子线程跑完后回调 mark_done / mark_failed_retryable / mark_failed_terminal
    - 内存紧张时主动 sleep / 暂停派发
    - 整书级源 SHA-256 在 run_book 入口算一次，复用给 mark_done 做 dedup 元数据
    """

    def __init__(
        self,
        state: PipelineState,
        runner: OcrRunner,
        monitor: MemoryMonitor,
        max_attempts: int = 3,
        retry_backoff_s: float = 1.0,
        memory_poll_interval_s: float = 0.5,
        retryable_returncodes: tuple[int, ...] = (-1,),       # -1 表示我们的封装错误（timeout/missing）
        skip_returncodes: tuple[int, ...] = (4, 6, 11, 30),  # invalid output/xref/unfixable
    ) -> None:
        self.state = state
        self.runner = runner
        self.monitor = monitor
        self.max_attempts = max_attempts
        self.retry_backoff_s = retry_backoff_s
        self.memory_poll_interval_s = memory_poll_interval_s
        self.retryable_returncodes = set(retryable_returncodes)
        self.skip_returncodes = set(skip_returncodes)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run_book(self, book: BookEntry, output_dir) -> int:
        """处理一本书的所有页，返回 done 数。"""
        self.state.ensure_book_pages(book.id, book.title, book.pages)
        recovered = self.state.recover_processing(book.id)
        if recovered:
            logger.warning(
                "recovered book=%s processing_pages=%s", book.id, recovered
            )
        output_dir.mkdir(parents=True, exist_ok=True)

        # 整书级源哈希：源 PDF 一本书一个，跨页共享；先算一次避免 per-page 重复 IO
        source_path = Path(book.source)
        source_hash: str | None = None
        if source_path.exists():
            try:
                source_hash = sha256_file(source_path)
            except OSError as exc:
                logger.warning("hash failed book=%s err=%s", book.id, exc)
        else:
            logger.warning("source missing book=%s path=%s", book.id, source_path)

        max_workers = self.monitor.max_workers
        pending_count = self._pending_count(book.id)
        logger.info(
            "starting book=%s pages=%s pending=%s workers<=%s hash=%s",
            book.id, book.pages, pending_count, max_workers,
            (source_hash or "")[:12],
        )
        if pending_count == 0:
            logger.info("book=%s already complete", book.id)
            return 0

        futures: dict[Future, tuple[BookEntry, int, int, Path, str | None]] = {}
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"ocr-{book.id}") as pool:
            while not self._stop.is_set():
                mem = self.monitor.sample()
                live = sum(1 for f in futures if not f.done())
                slot = mem.max_workers - live

                if mem.should_pause:
                    logger.warning("memory tight avail=%sMB, pausing", mem.available_mb)
                    self._drain(futures)
                    time.sleep(self.memory_poll_interval_s * 5)
                    continue

                if slot <= 0:
                    self._drain(futures)
                    time.sleep(self.memory_poll_interval_s)
                    continue

                claim = self.state.claim_next_page(book.id)
                if claim is None:
                    # In-flight pages can fail and requeue themselves. Wait for
                    # those results before deciding that no work remains.
                    if futures:
                        self._drain(futures, wait_all=True)
                        continue
                    break

                page, attempts = claim
                output_path = book.output_pdf_for(page, output_dir)
                fut = pool.submit(
                    self._run_one, book, page, attempts, output_path, source_hash,
                )
                futures[fut] = (book, page, attempts, output_path, source_hash)

                # 非阻塞地尝试清理已完成的
                self._drain(futures)

            pool.shutdown(wait=True)
            self._drain(futures, wait_all=True)

        summary = self.state.book_summary(book.id)
        logger.info("book=%s done summary=%s", book.id, summary)
        return summary.get("done", 0)

    # ---------- internals ----------

    def _run_one(
        self,
        book: BookEntry,
        page: int,
        attempts_so_far: int,
        output_path,
        source_hash: str | None,
    ) -> None:
        result = self.runner.process_page(book, page, output_path)
        self._handle_result(book, page, attempts_so_far, result, source_hash)

    def _handle_result(
        self,
        book: BookEntry,
        page: int,
        attempts_so_far: int,
        result: OcrResult,
        source_hash: str | None = None,
    ) -> None:
        if result.ok and result.output_path.exists() and result.output_path.stat().st_size > 0:
            self.state.mark_done(book.id, page, str(result.output_path), source_hash)
            logger.info("done book=%s page=%s", book.id, page)
            return

        error = self._err(result)
        quarantined = self._quarantine_failed_output(result.output_path)
        if quarantined is not None:
            error += f" | failed_artifact={quarantined}"

        # 不可恢复错误码 → 直接跳过
        if result.returncode in self.skip_returncodes:
            attempts = self.state.mark_failed_terminal(book.id, page, error)
            logger.warning(
                "skip book=%s page=%s attempts=%s rc=%s reason=nonretryable",
                book.id, page, attempts, result.returncode,
            )
            return

        self._record_retryable_failure(book, page, error, result.returncode)

    def _record_retryable_failure(
        self, book: BookEntry, page: int, error: str, returncode: int
    ) -> None:
        attempts_after = self.state.mark_failed_retryable(book.id, page, error)

        # 已到最大重试次数 → 永久跳过
        if attempts_after >= self.max_attempts:
            self.state.mark_failed_terminal(
                book.id, page, error, increment=False
            )
            logger.warning(
                "skip book=%s page=%s attempts=%s reason=max_attempts",
                book.id, page, attempts_after,
            )
            return

        logger.info(
            "retry book=%s page=%s attempts=%s/%s rc=%s",
            book.id, page, attempts_after, self.max_attempts, returncode,
        )
        # 重试前 backoff（指数）
        time.sleep(self.retry_backoff_s * (2 ** attempts_after))

    def _record_worker_exception(
        self,
        book: BookEntry,
        page: int,
        output_path: Path,
        exc: Exception,
    ) -> None:
        logger.exception(
            "worker exception book=%s page=%s", book.id, page
        )
        error = f"worker_exception={type(exc).__name__}: {exc}"
        quarantined = self._quarantine_failed_output(output_path)
        if quarantined is not None:
            error += f" | failed_artifact={quarantined}"
        self._record_retryable_failure(book, page, error, returncode=-1)

    def _err(self, result: OcrResult) -> str:
        parts = [f"rc={result.returncode}"]
        if result.timed_out:
            parts.append("timed_out")
        if result.stderr.strip():
            parts.append(result.stderr.strip()[:300])
        return " | ".join(parts)

    def _quarantine_failed_output(self, output_path) -> str | None:
        """Keep failed output for diagnosis without exposing it as a PDF result."""
        if not output_path.exists():
            return None
        candidate = output_path.with_name(output_path.name + ".failed")
        suffix = 1
        while candidate.exists():
            candidate = output_path.with_name(
                f"{output_path.name}.{suffix}.failed"
            )
            suffix += 1
        try:
            output_path.replace(candidate)
        except OSError:
            logger.exception("failed to quarantine output=%s", output_path)
            return None
        return str(candidate)

    def _drain(
        self,
        futures: dict[Future, tuple[BookEntry, int, int, Path, str | None]],
        wait_all: bool = False,
    ) -> None:
        if wait_all and futures:
            wait(futures)
        done = [f for f in futures if f.done()]
        for f in done:
            book, page, _attempts, output_path, _source_hash = futures.pop(f)
            try:
                f.result()
            except Exception as exc:
                self._record_worker_exception(book, page, output_path, exc)
        if not wait_all and not done:
            time.sleep(0)        # 立即让出

    def _pending_count(self, book_id: str) -> int:
        s = self.state.book_summary(book_id)
        return s.get("pending", 0)
