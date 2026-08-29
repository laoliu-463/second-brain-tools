"""Compile completed OCR page PDFs into the local book text layer.

The command is deliberately conservative: it validates the OCR database, source
hashes, page continuity, and one-page PDF invariant before staging any writes.
Use ``--apply`` to write; the default is a read-only dry run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal, Mapping, Sequence

import yaml
from pypdf import PdfReader

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "config" / "ocr"))

from ocr_pipeline.manifest import BookEntry, load_manifest


Strategy = Literal["ocr", "hybrid"]
TARGET_BOOK_IDS = (
    "econ_micro_经济学原理微观",
    "econ_macro_经济学原理宏观",
    "econ_mankiw_宏观经济学第十版",
    "econ_political_马政经济学概论",
    "history_古本竹书纪年译注",
)
OLD_EMPTY_MARKERS = {
    "[本页未提取到文本，可能需要 OCR。]",
    "[本页 OCR 未识别到文本，请对照原始 PDF。]",
    "[经原始 PDF 视觉核验，本页无独立正文（空白页或背页透印）。]",
}
GENERATED_WARNING_PREFIX = "> [!warning] 本页"
PAGE_HEADING = re.compile(r"^## Page (\d+)\s*$", re.MULTILINE)


class IngestError(RuntimeError):
    """Raised when an invariant blocks a safe OCR ingest."""


@dataclass(frozen=True)
class BookPolicy:
    strategy: Strategy
    status: str
    confidence: str
    quality_note: str
    watermark: str | None = None
    manual_text_overrides: Mapping[int, str] = field(default_factory=dict)
    verified_blank_pages: tuple[int, ...] = ()
    verified_short_pages: tuple[int, ...] = ()
    visual_review_date: str | None = None


@dataclass(frozen=True)
class BookSpec:
    book_id: str
    title: str
    ocr_prefix: str
    expected_pages: int
    source_relative: Path
    output_relative: Path
    source_sha256: str
    strategy: Strategy
    status: str
    confidence: str
    quality_note: str
    watermark: str | None = None
    review_threshold: int = 50
    manual_text_overrides: Mapping[int, str] = field(default_factory=dict)
    verified_blank_pages: tuple[int, ...] = ()
    verified_short_pages: tuple[int, ...] = ()
    visual_review_date: str | None = None


@dataclass(frozen=True)
class PreparedBook:
    spec: BookSpec
    source_path: Path
    output_path: Path
    historical_source_file: str
    native_pages: Mapping[int, str]


@dataclass(frozen=True)
class BookDocument:
    content: str
    text_pages: int
    text_chars: int
    review_pages: tuple[int, ...]
    ocr_fallback_pages: tuple[int, ...]
    manual_override_pages: tuple[int, ...] = ()
    verified_blank_pages: tuple[int, ...] = ()
    verified_short_pages: tuple[int, ...] = ()


BOOK_POLICIES: Mapping[str, BookPolicy] = {
    "econ_micro_经济学原理微观": BookPolicy(
        strategy="ocr",
        status="extracted_needs_review",
        confidence="medium",
        quality_note="正文可检索；图表、数字和专名必须对照原始页复核。",
        manual_text_overrides={
            5: "献给 Catherine、Nicholas 和 Peter，\n作为我给下一代的另一种贡献",
            20: "第1篇 导言",
            88: "第2篇 市场如何运行",
            160: "第3篇 市场和福利",
            228: "第4篇 公共部门经济学",
            294: "第5篇 企业行为与产业组织",
            414: "第6篇 劳动市场经济学",
            480: "第7篇 深入研究的论题",
        },
        verified_blank_pages=(21, 89, 295, 415, 481),
        visual_review_date="2026-08-26",
    ),
    "econ_macro_经济学原理宏观": BookPolicy(
        strategy="ocr",
        status="extracted_needs_review",
        confidence="medium",
        quality_note="正文可检索；公式、变量、图表和数字必须对照原始页复核。",
        manual_text_overrides={
            1: (
                "经济学原理（第7版）\n宏观经济学分册\n"
                "（美）曼昆 著\nN. Gregory Mankiw\n梁小民、梁砾 译\n"
                "CENGAGE Learning\nPRINCIPLES OF ECONOMICS\n7TH EDITION\n"
                "北京大学出版社\nPEKING UNIVERSITY PRESS"
            ),
            5: "献给 Catherine、Nicholas 和 Peter，\n作为我给下一代的另一种贡献",
            18: "第8篇 宏观经济学的数据",
            64: "第9篇 长期中的真实经济",
            156: "第10篇 长期中的货币与物价",
            208: "第11篇 开放经济的宏观经济学",
            254: "第12篇 短期经济波动",
            344: "第13篇 最后的思考",
        },
        verified_blank_pages=(19, 65, 157, 209, 255, 345),
        verified_short_pages=(406,),
        visual_review_date="2026-08-26",
    ),
    "econ_mankiw_宏观经济学第十版": BookPolicy(
        strategy="hybrid",
        status="extracted",
        confidence="high",
        quality_note="原生文本为主，仅在原生文本缺失或过短时使用 OCR 补页。",
        manual_text_overrides={
            2: (
                "“十三五”国家重点出版物出版规划项目\n经济科学译丛\n"
                "宏观经济学（第十版）\n"
                "N. 格里高利·曼昆（N. Gregory Mankiw）著\n卢远瞩 译"
            ),
            231: "第4篇 经济周期理论：短期中的经济",
            349: "第5篇 宏观经济理论和政策专题",
        },
        verified_blank_pages=(5, 15, 21, 56, 58, 178, 180, 230, 232, 348, 350),
        visual_review_date="2026-08-26",
    ),
    "econ_political_马政经济学概论": BookPolicy(
        strategy="ocr",
        status="extracted_needs_review",
        confidence="medium",
        quality_note="OCR 正文可检索；已移除重复下载站水印，仍需抽样校对。",
        watermark="see more please visit: https://homeofpdf.com",
        manual_text_overrides={
            444: "ISBN 978-7-01-009875-3\n定价：40.00元",
        },
        visual_review_date="2026-08-26",
    ),
    "history_古本竹书纪年译注": BookPolicy(
        strategy="ocr",
        status="extracted_needs_review",
        confidence="low",
        quality_note="古字、异体字、注文、标点和引文噪声较高，引用前必须对照原始页。",
        watermark="see more please visit: https://homeofbook.com",
        manual_text_overrides={
            1: "古本竹書紀年譯註\n李民、楊擇令、孫順霖、史道祥\n中州古籍出版社",
            3: "古本竹书纪年译注\n李民、杨择令、孙顺霖、史道祥\n中州古籍出版社",
        },
        verified_short_pages=(348,),
        visual_review_date="2026-08-26",
    ),
}


def compact_length(text: str) -> int:
    return len("".join(text.split()))


def clean_ocr_text(text: str, watermark: str | None = None) -> str:
    """Remove a known exact watermark and normalize harmless whitespace."""
    watermark_key = watermark.strip().casefold() if watermark else None
    cleaned_lines: list[str] = []
    blank_pending = False
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        if watermark_key and line.strip().casefold() == watermark_key:
            continue
        if not line.strip():
            blank_pending = bool(cleaned_lines)
            continue
        if blank_pending:
            cleaned_lines.append("")
            blank_pending = False
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def _clean_native_text(text: str) -> str:
    lines = []
    for line in clean_ocr_text(text).splitlines():
        stripped = line.strip()
        if stripped in OLD_EMPTY_MARKERS:
            continue
        if stripped.startswith(GENERATED_WARNING_PREFIX):
            continue
        lines.append(line)
    return clean_ocr_text("\n".join(lines))


def choose_hybrid_text(
    native_text: str,
    ocr_text: str,
    threshold: int = 50,
) -> tuple[str, bool]:
    """Prefer native text unless it is short and OCR is more informative."""
    native_text = _clean_native_text(native_text)
    ocr_text = clean_ocr_text(ocr_text)
    native_size = compact_length(native_text)
    ocr_size = compact_length(ocr_text)
    if native_size >= threshold or ocr_size <= native_size:
        return native_text, False
    return ocr_text, True


def parse_frontmatter(markdown: str) -> dict[str, object]:
    if not markdown.startswith("---\n"):
        raise IngestError("text-layer Markdown has no YAML frontmatter")
    end = markdown.find("\n---\n", 4)
    if end < 0:
        raise IngestError("text-layer Markdown has unterminated YAML frontmatter")
    loaded = yaml.safe_load(markdown[4:end]) or {}
    if not isinstance(loaded, dict):
        raise IngestError("text-layer frontmatter must be a mapping")
    return loaded


def parse_markdown_pages(markdown: str) -> dict[int, str]:
    matches = list(PAGE_HEADING.finditer(markdown))
    pages: dict[int, str] = {}
    for index, match in enumerate(matches):
        page = int(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        if page in pages:
            raise IngestError(f"duplicate Page heading in Markdown: {page}")
        pages[page] = _clean_native_text(markdown[match.end() : end])
    return pages


def discover_page_files(
    ocr_dir: Path,
    prefix: str,
    expected_pages: int,
) -> dict[int, Path]:
    pattern = re.compile(rf"^{re.escape(prefix)}page(\d+)\.pdf$")
    pages: dict[int, Path] = {}
    for path in sorted(ocr_dir.glob(f"{prefix}page*.pdf")):
        match = pattern.match(path.name)
        if not match:
            continue
        page = int(match.group(1))
        if page in pages:
            raise IngestError(
                f"duplicate OCR page {page}: {pages[page].name}, {path.name}"
            )
        pages[page] = path

    expected = set(range(1, expected_pages + 1))
    actual = set(pages)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    problems = []
    if missing:
        problems.append("missing pages: " + ", ".join(map(str, missing)))
    if unexpected:
        problems.append("unexpected pages: " + ", ".join(map(str, unexpected)))
    if problems:
        raise IngestError("; ".join(problems))
    return pages


def extract_ocr_pages(page_files: Mapping[int, Path]) -> dict[int, str]:
    extracted: dict[int, str] = {}
    for page, path in sorted(page_files.items()):
        try:
            reader = PdfReader(path, strict=False)
            if len(reader.pages) != 1:
                raise IngestError(
                    f"OCR artifact must contain exactly one page: {path}"
                )
            extracted[page] = reader.pages[0].extract_text() or ""
        except IngestError:
            raise
        except Exception as exc:
            raise IngestError(
                f"cannot read OCR artifact {path}: {type(exc).__name__}: {exc}"
            ) from exc
    return extracted


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _format_page_list(pages: Sequence[int]) -> str:
    return "[" + ", ".join(map(str, pages)) + "]"


def _validate_review_classifications(spec: BookSpec) -> None:
    manual_pages = set(spec.manual_text_overrides)
    blank_pages = set(spec.verified_blank_pages)
    short_pages = set(spec.verified_short_pages)
    classified = manual_pages | blank_pages | short_pages
    invalid = sorted(
        page for page in classified if page < 1 or page > spec.expected_pages
    )
    if invalid:
        raise IngestError(
            f"review classification pages out of range for {spec.book_id}: {invalid}"
        )

    overlaps = sorted(
        (manual_pages & blank_pages)
        | (manual_pages & short_pages)
        | (blank_pages & short_pages)
    )
    if overlaps:
        raise IngestError(
            f"review classification overlap for {spec.book_id}: {overlaps}"
        )

    empty_manual = sorted(
        page
        for page, text in spec.manual_text_overrides.items()
        if not compact_length(clean_ocr_text(text))
    )
    if empty_manual:
        raise IngestError(
            f"manual text overrides are empty for {spec.book_id}: {empty_manual}"
        )


def build_book_document(
    spec: BookSpec,
    ocr_pages: Mapping[int, str],
    native_pages: Mapping[int, str],
    historical_source_file: str,
    ocr_dir: Path,
    extraction_date: str,
) -> BookDocument:
    _validate_review_classifications(spec)
    expected = set(range(1, spec.expected_pages + 1))
    if set(ocr_pages) != expected:
        missing = sorted(expected - set(ocr_pages))
        extra = sorted(set(ocr_pages) - expected)
        raise IngestError(
            f"OCR text map mismatch for {spec.book_id}: missing={missing}, extra={extra}"
        )

    selected: dict[int, str] = {}
    review_pages: list[int] = []
    fallback_pages: list[int] = []
    manual_pages = set(spec.manual_text_overrides)
    blank_pages = set(spec.verified_blank_pages)
    short_pages = set(spec.verified_short_pages)
    for page in range(1, spec.expected_pages + 1):
        ocr_text = clean_ocr_text(ocr_pages[page], spec.watermark)
        if page in manual_pages:
            text = clean_ocr_text(spec.manual_text_overrides[page])
        elif page in blank_pages:
            text = ""
        elif spec.strategy == "hybrid":
            text, used_ocr = choose_hybrid_text(
                native_pages.get(page, ""),
                ocr_text,
                threshold=spec.review_threshold,
            )
            if used_ocr:
                fallback_pages.append(page)
        else:
            text = ocr_text
        selected[page] = text
        if page in short_pages and not compact_length(text):
            raise IngestError(
                f"verified short page {page} is empty for {spec.book_id}"
            )
        if (
            page not in manual_pages
            and page not in blank_pages
            and page not in short_pages
            and compact_length(text) < spec.review_threshold
        ):
            review_pages.append(page)

    text_pages = sum(bool(compact_length(text)) for text in selected.values())
    text_chars = sum(len(text) for text in selected.values())
    source_link = os.path.relpath(
        spec.source_relative,
        spec.output_relative.parent,
    ).replace("\\", "/")
    method = "native_text_with_ocr_fallback" if spec.strategy == "hybrid" else "ocrmypdf"

    frontmatter = [
        "---",
        "type: source-transcript",
        f"status: {spec.status}",
        f"confidence: {spec.confidence}",
        "source_format: pdf",
        f"source_file: {_yaml_string(historical_source_file)}",
        f"source_sha256: {spec.source_sha256}",
        f"vault_source_file: {_yaml_string(spec.source_relative.as_posix())}",
        f"pages: {spec.expected_pages}",
        f"text_pages: {text_pages}",
        f"text_chars: {text_chars}",
        f"extracted: {extraction_date}",
        f"extraction_method: {method}",
        f"ocr_engine: {_yaml_string('OCRmyPDF 17.10.0 / Tesseract 5.5.3')}",
        f"ocr_output_dir: {_yaml_string(str(ocr_dir))}",
        f"manual_override_pages: {_format_page_list(sorted(manual_pages))}",
        f"verified_blank_pages: {_format_page_list(sorted(blank_pages))}",
        f"verified_short_pages: {_format_page_list(sorted(short_pages))}",
        f"review_pages: {_format_page_list(review_pages)}",
    ]
    if spec.visual_review_date and (manual_pages or blank_pages or short_pages):
        frontmatter.extend(
            [
                f"visual_reviewed: {spec.visual_review_date}",
                "visual_review_method: original_pdf_visual_inspection",
            ]
        )
    if spec.strategy == "hybrid":
        frontmatter.append(
            f"ocr_fallback_pages: {_format_page_list(fallback_pages)}"
        )
    frontmatter.extend(
        [
            f"quality_note: {_yaml_string(spec.quality_note)}",
            "---",
            "",
        ]
    )

    body = [
        f"# {spec.title}",
        "",
        "> 本页是原始 PDF 的自动文字层，不等同于人工校对稿；原始文件保持只读。",
        f"> 质量边界：{spec.quality_note}",
        f"> 原始 PDF：[{spec.source_relative.name}]({source_link})",
        f"> OCR 页文件：`{ocr_dir}`",
    ]
    if manual_pages or blank_pages or short_pages:
        body.append(
            f"> 低文本页视觉核验：人工补录 {len(manual_pages)} 页，"
            f"无独立正文 {len(blank_pages)} 页，短内容有效 {len(short_pages)} 页；"
            f"仍待核验 {len(review_pages)} 页。此记录不代表全书已逐页校对。"
        )
    for page in range(1, spec.expected_pages + 1):
        text = selected[page]
        body.extend(["", f"## Page {page}", ""])
        if page in blank_pages:
            body.append(
                "[经原始 PDF 视觉核验，本页无独立正文（空白页或背页透印）。]"
            )
        elif text:
            body.append(text)
            if page in review_pages:
                if spec.strategy == "ocr":
                    body.extend(
                        [
                            "",
                            "> [!warning] 本页 OCR 文本较少，请对照原始 PDF 复核。",
                        ]
                    )
                else:
                    body.extend(
                        [
                            "",
                            "> [!warning] 本页可检索文本较少，请对照原始 PDF 复核。",
                        ]
                    )
        else:
            body.append("[本页 OCR 未识别到文本，请对照原始 PDF。]")

    content = "\n".join(frontmatter + body).rstrip() + "\n"
    return BookDocument(
        content=content,
        text_pages=text_pages,
        text_chars=text_chars,
        review_pages=tuple(review_pages),
        ocr_fallback_pages=tuple(fallback_pages),
        manual_override_pages=tuple(sorted(manual_pages)),
        verified_blank_pages=tuple(sorted(blank_pages)),
        verified_short_pages=tuple(sorted(short_pages)),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _output_relative_for_source(source_relative: Path) -> Path:
    parts = source_relative.parts
    if len(parts) < 3 or parts[:2] != ("资料库", "原始资料"):
        raise IngestError(f"source is outside the governed original layer: {source_relative}")
    return Path(parts[0], "文字层", *parts[2:]).with_suffix(".md")


def _ocr_prefix(book: BookEntry, output_dir: Path) -> str:
    name = book.output_pdf_for(1, output_dir).name
    suffix = "page001.pdf"
    if not name.endswith(suffix):
        raise IngestError(f"unexpected OCR output naming for {book.id}: {name}")
    return name[: -len(suffix)]


def load_target_books(
    vault_root: Path,
    manifest_path: Path,
) -> tuple[list[PreparedBook], Path, Path]:
    manifest = load_manifest(manifest_path, vault_root)
    by_id = {book.id: book for book in manifest.books}
    missing_ids = [book_id for book_id in TARGET_BOOK_IDS if book_id not in by_id]
    if missing_ids:
        raise IngestError(f"manifest is missing target books: {missing_ids}")

    prepared: list[PreparedBook] = []
    resolved_root = vault_root.resolve()
    for book_id in TARGET_BOOK_IDS:
        book = by_id[book_id]
        policy = BOOK_POLICIES[book_id]
        source_path = Path(book.source)
        if not source_path.is_absolute():
            source_path = vault_root / source_path
        source_path = source_path.resolve()
        try:
            source_relative = source_path.relative_to(resolved_root)
        except ValueError as exc:
            raise IngestError(f"source is outside vault root: {source_path}") from exc
        output_relative = _output_relative_for_source(source_relative)
        output_path = vault_root / output_relative
        if not source_path.is_file():
            raise IngestError(f"source PDF not found: {source_path}")
        if not output_path.is_file():
            raise IngestError(f"existing text-layer file not found: {output_path}")

        existing = output_path.read_text(encoding="utf-8")
        metadata = parse_frontmatter(existing)
        expected_hash = str(metadata.get("source_sha256", "")).upper()
        if not re.fullmatch(r"[0-9A-F]{64}", expected_hash):
            raise IngestError(f"invalid source_sha256 in {output_path}")
        if int(metadata.get("pages", 0)) != book.pages:
            raise IngestError(
                f"page count disagreement for {book.id}: "
                f"manifest={book.pages}, text_layer={metadata.get('pages')}"
            )
        actual_hash = sha256_file(source_path)
        if actual_hash != expected_hash:
            raise IngestError(
                f"source hash mismatch for {book.id}: expected={expected_hash}, actual={actual_hash}"
            )

        native_pages: dict[int, str] = {}
        if policy.strategy == "hybrid":
            native_pages = parse_markdown_pages(existing)
            old_fallback = metadata.get("ocr_fallback_pages", []) or []
            if isinstance(old_fallback, list):
                for page in old_fallback:
                    native_pages[int(page)] = ""

        historical_source = str(metadata.get("source_file", source_path))
        spec = BookSpec(
            book_id=book.id,
            title=book.title,
            ocr_prefix=_ocr_prefix(book, manifest.output_dir),
            expected_pages=book.pages,
            source_relative=source_relative,
            output_relative=output_relative,
            source_sha256=expected_hash,
            strategy=policy.strategy,
            status=policy.status,
            confidence=policy.confidence,
            quality_note=policy.quality_note,
            watermark=policy.watermark,
            manual_text_overrides=policy.manual_text_overrides,
            verified_blank_pages=policy.verified_blank_pages,
            verified_short_pages=policy.verified_short_pages,
            visual_review_date=policy.visual_review_date,
        )
        prepared.append(
            PreparedBook(
                spec=spec,
                source_path=source_path,
                output_path=output_path,
                historical_source_file=historical_source,
                native_pages=native_pages,
            )
        )
    return prepared, manifest.output_dir.resolve(), manifest.db_path.resolve()


def validate_progress_db(db_path: Path, books: Sequence[PreparedBook]) -> None:
    if not db_path.is_file():
        raise IngestError(f"OCR progress database not found: {db_path}")
    connection = sqlite3.connect(db_path)
    try:
        for prepared in books:
            rows = connection.execute(
                "SELECT status, COUNT(*) FROM progress "
                "WHERE book_id=? GROUP BY status",
                (prepared.spec.book_id,),
            ).fetchall()
            counts = {str(status): int(count) for status, count in rows}
            expected = {"done": prepared.spec.expected_pages}
            if counts != expected:
                raise IngestError(
                    f"OCR database is not complete for {prepared.spec.book_id}: "
                    f"expected={expected}, actual={counts}"
                )
    finally:
        connection.close()


def update_manifest_bytes(
    path: Path,
    documents: Mapping[str, tuple[BookSpec, BookDocument]],
) -> tuple[bytes, Counter[str]]:
    original = path.read_bytes()
    has_bom = original.startswith(b"\xef\xbb\xbf")
    newline = "\r\n" if b"\r\n" in original else "\n"
    decoded = original.decode("utf-8-sig" if has_bom else "utf-8")
    reader = csv.DictReader(io.StringIO(decoded, newline=""))
    fieldnames = reader.fieldnames
    rows = list(reader)
    if not fieldnames:
        raise IngestError(f"manifest has no header: {path}")

    matched: Counter[str] = Counter()
    changed = False
    for row in rows:
        key = row.get("Hash", "").upper()
        if key not in documents:
            continue
        spec, document = documents[key]
        matched[key] += 1
        expected_values = {
            "status": spec.status,
            "confidence": spec.confidence,
            "pages": str(spec.expected_pages),
            "text_pages": str(document.text_pages),
            "text_chars": str(document.text_chars),
            "Errors": "",
        }
        for field, value in expected_values.items():
            if row.get(field) != value:
                row[field] = value
                changed = True

    for key in documents:
        if matched[key] != 1:
            raise IngestError(
                f"文字化清单 must contain exactly one row for {key}; found {matched[key]}"
            )

    counts = Counter(row.get("status", "") for row in rows)
    if not changed:
        return original, counts

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator=newline)
    writer.writeheader()
    writer.writerows(rows)
    prefix = b"\xef\xbb\xbf" if has_bom else b""
    payload = prefix + buffer.getvalue().encode("utf-8")
    return payload, counts


def update_index_text(text: str, counts: Mapping[str, int]) -> str:
    replacements = {
        r"(?m)^- 可直接检索：\d+ 份，状态为 `extracted`。$": (
            f"- 可直接检索：{counts.get('extracted', 0)} 份，状态为 `extracted`。"
        ),
        r"(?m)^- 需要复核：\d+ 份，状态为 `extracted_needs_review`。$": (
            "- 需要复核："
            f"{counts.get('extracted_needs_review', 0)} 份，状态为 `extracted_needs_review`。"
        ),
        r"(?m)^- 需要 OCR：\d+ 份，状态为 `needs_ocr`。$": (
            f"- 需要 OCR：{counts.get('needs_ocr', 0)} 份，状态为 `needs_ocr`。"
        ),
    }
    updated = text
    for pattern, replacement in replacements.items():
        updated, changed = re.subn(pattern, replacement, updated, count=1)
        if changed != 1:
            raise IngestError(f"文字化索引 replacement did not match exactly once: {pattern}")

    table_counts = {
        "中文 OCR": counts.get("needs_ocr", 0),
        "文本质量复核": counts.get("extracted_needs_review", 0),
    }
    for label, count in table_counts.items():
        pattern = rf"(?m)^(\| {re.escape(label)} \| )\d+( \|.*\|)$"
        updated, changed = re.subn(
            pattern,
            lambda match, value=count: f"{match.group(1)}{value}{match.group(2)}",
            updated,
            count=1,
        )
        if changed != 1:
            raise IngestError(
                f"文字化索引 replacement did not match exactly once: {pattern}"
            )

    old_note = (
        "- 中文 OCR 试验使用本地 Tesseract.js 处理单页，识别噪声较大，因此暂停批量 OCR，"
        "等待更可靠的中文 OCR 引擎或人工复核流程。"
    )
    new_note = (
        "- 2026-08-25 已完成 5 本重点书籍的 OCR 接入：4 本以 OCR 文本重建，"
        "曼昆《宏观经济学》第十版保留原生文本并仅用 OCR 补缺页。\n"
        "- OCR 结果尚未逐页人工校对；公式、图表、数字、专名、古字和注文仍须回到原始 PDF 核验。"
    )
    old_count = updated.count(old_note)
    new_count = updated.count(new_note)
    if old_count == 1 and new_count == 0:
        return updated.replace(old_note, new_note, 1)
    if old_count == 0 and new_count == 1:
        return updated
    raise IngestError(
        "文字化索引 OCR status note must contain exactly one old or new block"
    )


def write_files_atomically(payloads: Mapping[Path, bytes]) -> None:
    originals: dict[Path, bytes | None] = {
        path: path.read_bytes() if path.exists() else None for path in payloads
    }
    staged: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for path, payload in payloads.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_name(f".{path.name}.ocr-ingest-{os.getpid()}.tmp")
            with temp_path.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            staged[path] = temp_path
        for path, temp_path in staged.items():
            os.replace(temp_path, path)
            replaced.append(path)
    except Exception:
        for path in reversed(replaced):
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
                continue
            restore = path.with_name(f".{path.name}.ocr-restore-{os.getpid()}.tmp")
            restore.write_bytes(original)
            os.replace(restore, path)
        raise
    finally:
        for temp_path in staged.values():
            temp_path.unlink(missing_ok=True)


def compile_documents(
    books: Sequence[PreparedBook],
    ocr_dir: Path,
    extraction_date: str,
) -> dict[str, tuple[BookSpec, BookDocument]]:
    documents: dict[str, tuple[BookSpec, BookDocument]] = {}
    for prepared in books:
        spec = prepared.spec
        page_files = discover_page_files(
            ocr_dir,
            prefix=spec.ocr_prefix,
            expected_pages=spec.expected_pages,
        )
        ocr_pages = extract_ocr_pages(page_files)
        document = build_book_document(
            spec,
            ocr_pages=ocr_pages,
            native_pages=prepared.native_pages,
            historical_source_file=prepared.historical_source_file,
            ocr_dir=ocr_dir,
            extraction_date=extraction_date,
        )
        documents[spec.source_sha256] = (spec, document)
        print(
            f"compiled {spec.book_id}: pages={spec.expected_pages}, "
            f"text_pages={document.text_pages}, text_chars={document.text_chars}, "
            f"review={len(document.review_pages)}, "
            f"ocr_fallback={len(document.ocr_fallback_pages)}",
            flush=True,
        )
    return documents


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "books.yaml")
    parser.add_argument("--ocr-dir", type=Path)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the generated text layer, manifest, and index.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    vault_root = args.vault_root.resolve()
    manifest_path = args.manifest.resolve()
    books, manifest_ocr_dir, db_path = load_target_books(vault_root, manifest_path)
    ocr_dir = args.ocr_dir.resolve() if args.ocr_dir else manifest_ocr_dir
    validate_progress_db(db_path, books)
    documents = compile_documents(books, ocr_dir, args.date)

    text_manifest_path = vault_root / "资料库" / "文字化清单.csv"
    manifest_bytes, counts = update_manifest_bytes(text_manifest_path, documents)
    index_path = vault_root / "资料库" / "文字化索引.md"
    index_text = update_index_text(index_path.read_text(encoding="utf-8"), counts)

    payloads: dict[Path, bytes] = {
        prepared.output_path: documents[prepared.spec.source_sha256][1].content.encode(
            "utf-8"
        )
        for prepared in books
    }
    payloads[text_manifest_path] = manifest_bytes
    payloads[index_path] = index_text.encode("utf-8")
    changed_payloads = {
        path: payload
        for path, payload in payloads.items()
        if not path.exists() or path.read_bytes() != payload
    }

    if args.apply:
        write_files_atomically(changed_payloads)
        print(
            f"applied {len(changed_payloads)} changed files "
            f"({len(payloads)} validated)",
            flush=True,
        )
    else:
        print(
            f"dry-run passed; {len(changed_payloads)} of {len(payloads)} files "
            "would be updated",
            flush=True,
        )
        for path in changed_payloads:
            print(f"would update: {path}", flush=True)
    print("status counts: " + json.dumps(counts, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except IngestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
