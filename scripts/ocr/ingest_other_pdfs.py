#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""库模式 ingest: 扫描 资料库/原始资料/ 下其他 PDF，分类后入库。

设计要点
--------
- 不在代码里硬编码书清单，由 --pdf-root + --skip-manifest YAML 发现
- 复用 ocr_pipeline 的 PipelineState / OcrRunner / PageWorker / sha256_file
- SHA-256 dedup: 源 PDF 未变（已 done 且 hash 匹配）→ 跳过整书
- 文字层 PDF（pypdf 抽第 1 页 >100 字符）走纯抽 text-layer 路径，不起 ocrmypdf
- 扫描件走 PageWorker，6 worker + MemoryMonitor 自动节流
- 输出：文字层 → 资料库/文字层/<镜像路径>.md；OCR 单页 PDF → <output_dir>/

用法
----
    python ingest_other_pdfs.py --dry-run --limit 20
    python ingest_other_pdfs.py --max-workers 6
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml
from pypdf import PdfReader

# 复用 ocr_pipeline 库（位于 config/ocr/）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OCR_PIPELINE_PATH = PROJECT_ROOT / "config" / "ocr"
sys.path.insert(0, str(OCR_PIPELINE_PATH))

from ocr_pipeline import (  # noqa: E402
    BookEntry,
    MemoryMonitor,
    OcrRunner,
    PageWorker,
    PipelineState,
    sha256_file,
)


DEFAULT_PDF_ROOTS: list[Path] = [
    PROJECT_ROOT / "资料库" / "3_普通人系列" / "原始资料层",
    PROJECT_ROOT / "资料库" / "4_中美博弈系列" / "原始资料层",
    PROJECT_ROOT / "资料库" / "5_摆万—认知红利系列" / "原始资料层",
]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "ocr-other-20260826"
DEFAULT_DB_PATH = DEFAULT_OUTPUT_DIR / "ocr_progress.db"
DEFAULT_SKIP_MANIFESTS: list[Path] = [
    PROJECT_ROOT / "config" / "ocr" / "books.yaml",
    PROJECT_ROOT / "config" / "ocr" / "cognitive.yaml",
    PROJECT_ROOT / "config" / "ocr" / "cognitive_other.yaml",
    PROJECT_ROOT / "config" / "ocr" / "normal.yaml",
]
DEFAULT_TEXT_LAYER_OUTPUT_ROOT = PROJECT_ROOT / "资料库" / "文字层"

TEXT_LAYER_THRESHOLD = 100  # 第 1 页 extract_text() 字符数 > 此值视为有文字层
EXTRACT_DATE = "2026-08-26"
LARGE_PDF_BYTES = 80 * 1024 * 1024  # 80MB+ 默认按扫描件处理，绕开 pypdf OOM


# ---------- 数据类 ----------

@dataclass(frozen=True)
class Candidate:
    path: Path
    sha256: str
    pages: int
    is_text_layer: bool
    rel: Path  # 相对于 pdf_root


# ---------- 日志 ----------

def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------- 发现 ----------

def discover_pdfs(root: Path) -> list[Path]:
    """递归扫描所有 .pdf 文件。"""
    if not root.exists():
        raise FileNotFoundError(f"pdf root not found: {root}")
    return sorted(p for p in root.rglob("*.pdf") if p.is_file())


def load_skip_source_paths(*manifest_paths: Path) -> set[Path]:
    """从 manifest YAML 提取所有 source 路径（resolved 后用作 skip key）。"""
    skip: set[Path] = set()
    for path in manifest_paths:
        if not path.exists():
            logging.warning("skip manifest not found: %s", path)
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for raw in data.get("books", []):
            src_raw = raw.get("source")
            if not src_raw:
                continue
            try:
                skip.add(Path(src_raw).resolve())
            except OSError:
                pass
    return skip


# ---------- 分类 ----------

def is_text_layer_pdf(path: Path) -> bool:
    """用 pypdf 抽第 1 页文本：>TEXT_LAYER_THRESHOLD 字符视为有文字层。

    >80MB 的文件直接判为扫描件——pypdf 构造会爆内存，且扫描件不可能有文字层。
    """
    try:
        if path.stat().st_size > LARGE_PDF_BYTES:
            logging.info("large pdf → assume scanned: %s", path.name)
            return False
    except OSError:
        return False
    try:
        reader = PdfReader(str(path), strict=False)
        if not reader.pages:
            return False
        text = reader.pages[0].extract_text() or ""
        return len(text.strip()) > TEXT_LAYER_THRESHOLD
    except Exception as exc:
        logging.warning("first-page read failed: %s err=%s", path, exc)
        return False


def page_count(path: Path) -> int:
    try:
        if path.stat().st_size > LARGE_PDF_BYTES:
            # 跳过 pypdf 读 xref；让 ocrmypdf 自己数（它用 mupdf 不会爆）
            return 0
    except OSError:
        pass
    try:
        reader = PdfReader(str(path), strict=False)
        return len(reader.pages)
    except Exception as exc:
        logging.warning("page count failed: %s err=%s", path, exc)
        return 0


def build_candidates(
    pdfs_by_root: dict[Path, list[Path]],
    skip_paths: set[Path],
) -> tuple[list[Candidate], list[tuple[Path, Path]], int]:
    """分类 + 哈希；返回 (候选, [(失败 PDF, 所属 root)], 跳过计数)。

    pdfs_by_root: {root_path: [pdf, ...], ...}；rel 路径会冠上 root.parent.name
    (系列目录名，如 3_普通人系列)，确保跨 root 的同名文件也唯一。
    注意：root.name 只是 "原始资料层" 这一层目录，3 个 series 共享，
    不能用作前缀——否则同名 PDF 会互相覆盖。

    单 PDF 的 hash / pypdf 解析失败不应拖垮全集——捕获后放进 failed 列表，
    OCR 阶段直接走 ocrmypdf（ocrmypdf 对损坏 PDF 通常更鲁棒）。"""
    candidates: list[Candidate] = []
    failed_classify: list[tuple[Path, Path]] = []
    skipped = 0
    for root, pdfs in pdfs_by_root.items():
        for pdf in pdfs:
            try:
                resolved = pdf.resolve()
            except OSError:
                logging.warning("unresolvable path: %s", pdf)
                skipped += 1
                continue

            if resolved in skip_paths:
                logging.info("skip %s (in skip manifest)", pdf.name)
                skipped += 1
                continue

            # 单独 try：sha256 / pypdf / page_count 任一炸都降级到 failed
            try:
                sha = sha256_file(pdf)
                text_layer = is_text_layer_pdf(pdf)
                pages = page_count(pdf)
            except MemoryError as exc:
                logging.error("classify OOM: %s err=%s", pdf.name, exc)
                failed_classify.append((pdf, root))
                continue
            except Exception as exc:
                logging.error(
                    "classify failed (will retry in OCR): %s err=%s: %s",
                    pdf.name, type(exc).__name__, exc,
                )
                failed_classify.append((pdf, root))
                continue

            try:
                rel_inner = pdf.relative_to(root)
            except ValueError:
                rel_inner = Path(pdf.name)
            rel = Path(root.parent.name) / rel_inner
            candidates.append(
                Candidate(
                    path=pdf, sha256=sha, pages=pages,
                    is_text_layer=text_layer, rel=rel,
                )
            )
    return candidates, failed_classify, skipped


# ---------- 文字层 PDF → .md ----------

def extract_text_layer_to_markdown(candidate: Candidate, shadow_root: Path) -> Path | None:
    """文字层 PDF：抽所有页文本 → 写一个 .md（带 frontmatter）。"""
    try:
        reader = PdfReader(str(candidate.path), strict=False)
        page_texts: dict[int, str] = {
            idx: (page.extract_text() or "") for idx, page in enumerate(reader.pages, start=1)
        }
    except Exception as exc:
        logging.error("text layer extract failed: %s err=%s", candidate.path, exc)
        return None

    text_chars = sum(len(t) for t in page_texts.values())
    text_pages = sum(1 for t in page_texts.values() if t.strip())

    md_path = shadow_root / candidate.rel.with_suffix(".md")
    md_path.parent.mkdir(parents=True, exist_ok=True)

    title = candidate.path.stem
    frontmatter = (
        "---\n"
        "type: source-transcript\n"
        "status: extracted\n"
        "confidence: high\n"
        "source_format: pdf\n"
        f"source_file: \"{candidate.path}\"\n"
        f"source_sha256: {candidate.sha256}\n"
        f"pages: {candidate.pages}\n"
        f"text_pages: {text_pages}\n"
        f"text_chars: {text_chars}\n"
        f"extracted: {EXTRACT_DATE}\n"
        "extraction_method: pypdf-text-layer\n"
        "quality_note: 直接提取 PDF 内嵌文本层；扫描件 OCR 已跳过。\n"
        "---\n\n"
        f"# {title}\n\n"
        "> 本页是对原始文件的自动文字提取层，不等同于人工校对稿。原始文件保持只读。\n"
        "> 数字、专名、图表和强因果判断仍须对照原始文件核验。\n\n"
    )

    body_parts: list[str] = []
    for idx in range(1, candidate.pages + 1):
        text = page_texts.get(idx, "")
        body_parts.append(f"\n## Page {idx}\n")
        if text.strip():
            body_parts.append(text)
        else:
            body_parts.append("[本页未提取到文本，请对照原始 PDF。]")

    md_path.write_text(frontmatter + "\n".join(body_parts), encoding="utf-8")
    return md_path


# ---------- OCR 分支 ----------

def make_book_entry(candidate: Candidate) -> BookEntry:
    """为 OCR 分流构造 BookEntry。

    id 用相对路径走 sha256 → 前 12 hex（大写），保证稳定且全局唯一。
    title 用 PDF stem，priority 100（最后跑），mode redo（已有文字层则替换）。
    """
    import hashlib
    digest = hashlib.sha256(str(candidate.rel).encode("utf-8")).hexdigest()[:12].upper()
    safe_stem = "".join(ch if ch.isalnum() else "_" for ch in candidate.path.stem)[:32]
    book_id = f"ingest_{safe_stem}_{digest}"
    return BookEntry(
        id=book_id,
        title=candidate.path.stem,
        source=str(candidate.path),
        mode="redo",
        pages=candidate.pages,
        language="chi_sim+eng",
        priority=100,
        extra_args=[],
        enabled=True,
    )


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(description="库模式 ingest 其他原始 PDF")
    p.add_argument(
        "--pdf-root", type=Path, nargs="*", default=DEFAULT_PDF_ROOTS,
        help="一个或多个原始 PDF 根目录（递归扫描 *.pdf）",
    )
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    p.add_argument(
        "--skip-manifest", type=Path, nargs="*",
        default=DEFAULT_SKIP_MANIFESTS,
        help="要排除的 manifest（其 source 路径解析后跳过）",
    )
    p.add_argument(
        "--text-layer-output", type=Path, default=DEFAULT_TEXT_LAYER_OUTPUT_ROOT,
        help="文字层 .md 输出根目录（默认 资料库/文字层）",
    )
    p.add_argument("--max-workers", type=int, default=6)
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--limit", type=int, default=0, help="只处理前 N 个候选（跨 root 累计）")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    setup_logging(args.log_level)
    log = logging.getLogger("ingest")

    log.info("pdf_roots (%d):", len(args.pdf_root))
    for root in args.pdf_root:
        log.info("  - %s", root)
    log.info("output_dir=%s", args.output_dir)
    log.info("db_path=%s", args.db_path)
    log.info("text_layer_output=%s", args.text_layer_output)

    skip_paths = load_skip_source_paths(*args.skip_manifest)
    log.info("skip source paths: %d", len(skip_paths))

    pdfs_by_root: dict[Path, list[Path]] = {}
    total_discovered = 0
    for root in args.pdf_root:
        if not root.exists():
            log.warning("pdf root not found, skipping: %s", root)
            continue
        root_pdfs = discover_pdfs(root)
        pdfs_by_root[root] = root_pdfs
        total_discovered += len(root_pdfs)
    log.info("discovered %d PDFs across %d roots",
             total_discovered, len(pdfs_by_root))

    if args.limit > 0:
        # 按 root 顺序截断 limit；其余 root 置空
        capped = 0
        for root, ps in list(pdfs_by_root.items()):
            if capped >= args.limit:
                pdfs_by_root[root] = []
            else:
                take = min(len(ps), args.limit - capped)
                pdfs_by_root[root] = ps[:take]
                capped += take
        log.info("limited to first %d PDFs across roots", capped)

    candidates, failed_classify, skipped = build_candidates(pdfs_by_root, skip_paths)
    text_layer_count = sum(1 for c in candidates if c.is_text_layer)
    scanned_count = sum(1 for c in candidates if not c.is_text_layer)
    log.info(
        "classified: %d text-layer / %d scanned / %d failed / %d skipped",
        text_layer_count, scanned_count, len(failed_classify), skipped,
    )

    if args.dry_run:
        for c in candidates:
            kind = "TEXT" if c.is_text_layer else "SCAN"
            log.info("  [%s] %s pages=%d sha=%s",
                     kind, c.rel, c.pages, c.sha256[:12])
        for pdf, root in failed_classify:
            log.info("  [FAIL-CLASSIFY] %s (root=%s)", pdf, root.name)
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.text_layer_output.mkdir(parents=True, exist_ok=True)

    state = PipelineState(args.db_path)
    runner = OcrRunner()
    monitor = MemoryMonitor(max_workers=args.max_workers)
    worker = PageWorker(
        state=state,
        runner=runner,
        monitor=monitor,
        max_attempts=args.max_attempts,
    )

    grand_start = time.time()
    text_done = 0
    text_failed = 0
    ocr_done = 0
    ocr_skipped_hash = 0

    for c in candidates:
        if c.is_text_layer:
            md = extract_text_layer_to_markdown(c, args.text_layer_output)
            if md:
                text_done += 1
                log.info("text-layer wrote: %s", md)
            else:
                text_failed += 1
                log.warning("text-layer failed: %s", c.path)
            continue

        # OCR 分支：先 dedup，再交给 PageWorker
        entry = make_book_entry(c)
        if state.skip_if_hash_matches(entry.id, 1, c.sha256):
            log.info("hash match → skip book=%s file=%s",
                     entry.id, c.path.name)
            ocr_skipped_hash += 1
            continue

        log.info("=== OCR %s ===", entry.title)
        done = worker.run_book(entry, args.output_dir)
        ocr_done += done

    # classify 阶段失败的大文件也走 OCR 分支（pypdf 解析挂了不代表 ocrmypdf 跑不了）
    for pdf, root in failed_classify:
        # 用 0 页 + 占位 hash 构造伪 Candidate，让 OCR 分支跑
        sha = sha256_file(pdf) if pdf.exists() else ""
        try:
            rel_inner = pdf.relative_to(root)
        except ValueError:
            rel_inner = Path(pdf.name)
        rel = Path(root.parent.name) / rel_inner
        c = Candidate(
            path=pdf, sha256=sha, pages=0,
            is_text_layer=False, rel=rel,
        )
        entry = make_book_entry(c)
        log.info("=== OCR (classify-failed fallback) %s ===", entry.title)
        done = worker.run_book(entry, args.output_dir)
        ocr_done += done

    elapsed = time.time() - grand_start
    log.info(
        "DONE: text_layer_ok=%d text_layer_fail=%d ocr_done_pages=%d "
        "ocr_skip_hash=%d classify_failed=%d in %.1fs",
        text_done, text_failed, ocr_done, ocr_skipped_hash,
        len(failed_classify), elapsed,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())