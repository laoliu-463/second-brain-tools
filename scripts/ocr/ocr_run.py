#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 入口：读 manifest → 起 monitor → 按书分派给 PageWorker。

设计原则
---------
- 完全由 books.yaml 驱动，不在代码里硬编码任何书
- 默认开 3 worker，psutil 自动节流到 0/1/2/3
- 单页失败 → 重试 3 次（指数退避）→ 永久移到 skipped 表
- --sample N 只跑前 N 页（每书），便于先期验证
- --book id 只跑一本书
- --resume 不重建 progress 表（默认即 resume）
- --dry-run 校验 manifest 不真正跑

环境前置
--------
- ocrmypdf.exe 在 PATH 或默认候选路径
- Tesseract 在 C:\\Program Files\\Tesseract-OCR
- chi_sim.traineddata 已就位

示例
----
    python ocr_run.py --manifest books.yaml --sample 3
    python ocr_run.py --manifest books.yaml --book macro_econ_mankiw
    python ocr_run.py --manifest books.yaml --max-workers 2
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

# 添加 config/ocr 到 Python 路径
ocr_pipeline_path = Path(__file__).resolve().parent.parent.parent / "config" / "ocr"
sys.path.insert(0, str(ocr_pipeline_path))

from ocr_pipeline import (
    BookEntry,
    Manifest,
    MemoryMonitor,
    OcrRunner,
    PageWorker,
    PipelineState,
    load_manifest,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="OCR pipeline runner (manifest-driven)")
    p.add_argument(
        "--manifest", "-m",
        default=str(PROJECT_ROOT / "config" / "ocr" / "books.yaml"),
        help="YAML manifest 路径",
    )
    p.add_argument(
        "--book", "-b",
        default=None,
        help="只跑指定 book_id（manifest 里要有）",
    )
    p.add_argument(
        "--sample", "-s",
        type=int, default=0,
        help="只跑每本书的前 N 页（用于先期验证）",
    )
    p.add_argument(
        "--max-workers", "-w",
        type=int, default=3,
        help="最大并发 OCR 子进程数（实际数仍受内存节流）",
    )
    p.add_argument(
        "--max-attempts", type=int, default=3,
        help="单页最多重试次数（含首次）",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="只校验 manifest，不跑 OCR",
    )
    return p.parse_args()


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def validate(manifest: Manifest) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    for b in manifest.books:
        if not b.enabled:
            continue  # 跳过禁用的书籍
        if b.id in seen_ids:
            errors.append(f"duplicate book_id: {b.id}")
        seen_ids.add(b.id)
        errors.extend(f"[{b.id}] {e}" for e in b.validate(PROJECT_ROOT))
    return errors


def sample_books(manifest: Manifest, sample_pages: int) -> Manifest:
    """把每本书的 pages 截到 sample_pages（仅用于 dry 跑）。"""
    if sample_pages <= 0:
        return manifest
    new_books = []
    for b in manifest.books:
        new_books.append(BookEntry(
            id=b.id, title=b.title, source=b.source, mode=b.mode,
            pages=min(b.pages, sample_pages),
            language=b.language, priority=b.priority,
            extra_args=b.extra_args, enabled=b.enabled,
        ))
    return Manifest(books=new_books, output_dir=manifest.output_dir, db_path=manifest.db_path)


def select_books(manifest: Manifest, book_id: str | None, sample: int) -> list[BookEntry]:
    """Sample only the selected books, never widen an explicit --book filter."""
    books = sample_books(manifest, sample).ordered_books()
    return [b for b in books if book_id is None or b.id == book_id]


def install_signal_handlers(worker: PageWorker) -> None:
    def _handler(signum, frame):
        logging.warning("signal %s received, stopping after current page", signum)
        worker.stop()
    signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGBREAK"):        # Windows
        signal.signal(signal.SIGBREAK, _handler)


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)
    log = logging.getLogger("ocr_run")

    manifest_path = Path(args.manifest)
    manifest = load_manifest(manifest_path, project_root=PROJECT_ROOT)
    log.info("loaded manifest: %d books, output=%s", len(manifest.books), manifest.output_dir)

    errs = validate(manifest)
    if errs:
        for e in errs:
            log.error("manifest error: %s", e)
        return 2

    if args.sample < 0 or args.max_workers < 1 or args.max_attempts < 1:
        log.error("sample must be nonnegative; workers and attempts must be positive")
        return 2
    books = select_books(manifest, args.book, args.sample)
    if not books:
        log.error("--book %s not found or disabled in manifest", args.book)
        return 2

    if args.dry_run:
        log.info("dry run: manifest is valid, would run %d books", len(books))
        for b in books:
            log.info("  - %s | %s | pages=%d", b.id, b.title, b.pages)
        return 0

    # Sampling must not leave a book partially completed in its full-run DB.
    db_path = manifest.db_path
    output_dir = manifest.output_dir
    if args.sample:
        db_path = db_path.with_name(f"{db_path.stem}.sample-{args.sample}{db_path.suffix}")
        output_dir = output_dir / f"sample-{args.sample}"
    state = PipelineState(db_path)
    runner = OcrRunner()
    monitor = MemoryMonitor(max_workers=args.max_workers)
    worker = PageWorker(
        state=state,
        runner=runner,
        monitor=monitor,
        max_attempts=args.max_attempts,
    )
    install_signal_handlers(worker)

    overall_start = time.time()
    grand_total = 0
    for book in books:
        if worker._stop.is_set():           # 收到过 stop
            break
        log.info("=== %s ===", book.title)
        done = worker.run_book(book, output_dir)
        grand_total += done

    elapsed = time.time() - overall_start
    log.info("ALL DONE: %d pages in %.1fs (%.2f pages/s)",
             grand_total, elapsed, grand_total / max(elapsed, 1))

    # 打印每本书的最终统计
    incomplete = False
    for b in books:
        s = state.book_summary(b.id)
        log.info("summary %s: %s", b.id, s)
        incomplete |= s.get("done", 0) != b.pages
    return 1 if incomplete else 0


if __name__ == "__main__":
    sys.exit(main())
