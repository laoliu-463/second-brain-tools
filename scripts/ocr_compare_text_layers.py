"""Compare governed text layers against source-PDF text or fresh OCR.

PDF pages with usable embedded text are compared directly. Image-only pages are
rendered from the original PDF and recognized with local Tesseract. Nothing is
written to the source PDFs or Markdown text layers.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import proofread_batch as batch


def ngram_similarity(left: str, right: str, size: int = 3) -> float:
    left, right = batch.clean(left), batch.clean(right)
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    if min(len(left), len(right)) < size:
        return 0.0
    a = Counter(left[i:i + size] for i in range(len(left) - size + 1))
    b = Counter(right[i:i + size] for i in range(len(right) - size + 1))
    overlap = sum((a & b).values())
    return 2 * overlap / (sum(a.values()) + sum(b.values()))


def classify(text: str, reference: str, score: float) -> str:
    a, b = batch.clean(text), batch.clean(reference)
    if not a and not b:
        return "both_empty"
    if not a:
        return "text_layer_missing"
    if not b:
        return "recognition_missing"
    if a == b:
        return "exact_ignoring_layout_whitespace"
    if score >= 0.98:
        return "near_match_review"
    if score >= 0.90:
        return "difference_review"
    return "major_difference_review"


def render_and_ocr(
    source: Path,
    source_hash: str,
    page_number: int,
    output_root: Path,
    tesseract: str,
    width: int,
) -> tuple[str, Path, bool]:
    page_root = output_root / source_hash.lower() / f"page-{page_number:04d}"
    page_root.mkdir(parents=True, exist_ok=True)
    image = page_root / "source.png"
    text_path = page_root / "tesseract.txt"
    metadata_path = page_root / "metadata.json"
    signature = {"source_sha256": source_hash.lower(), "page": page_number,
                 "render_width": width, "language": "chi_sim+eng", "psm": 6}
    if metadata_path.is_file() and text_path.is_file():
        old = json.loads(metadata_path.read_text(encoding="utf-8"))
        if all(old.get(key) == value for key, value in signature.items()) and old.get("returncode") == 0:
            return text_path.read_text(encoding="utf-8-sig"), image, True

    before = batch.file_hash(source)
    if before != source_hash.lower():
        raise ValueError(f"source hash changed before OCR: {source}")
    with fitz.open(source) as pdf:
        page = pdf[page_number - 1]
        scale = width / page.rect.width
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        if pixmap.height > 30000 or pixmap.width * pixmap.height > 40_000_000:
            raise ValueError(
                f"rendered page exceeds safe full-page OCR limit: {pixmap.width}x{pixmap.height}: {source}#{page_number}"
            )
        pixmap.save(image)
    env = os.environ.copy()
    env["OMP_THREAD_LIMIT"] = "1"
    prefix = text_path.with_suffix("")
    result = subprocess.run(
        [tesseract, str(image), str(prefix), "-l", "chi_sim+eng", "--psm", "6"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    metadata = {**signature, "source": str(source), "image": str(image),
                "image_sha256": batch.file_hash(image), "returncode": result.returncode,
                "stderr": result.stderr[-1000:], "finished": datetime.now().isoformat(timespec="seconds")}
    batch.dump(metadata_path, metadata)
    if result.returncode or not text_path.is_file():
        raise RuntimeError(f"Tesseract failed rc={result.returncode}: {source}#{page_number}")
    if batch.file_hash(source) != before:
        raise ValueError(f"source hash changed during OCR: {source}")
    return text_path.read_text(encoding="utf-8-sig"), image, False


def select_documents(root: Path, db: sqlite3.Connection, text_roots: list[Path]) -> list[dict]:
    selected = []
    seen = set()
    for relative in text_roots:
        directory = batch.within(root, relative)
        if "文字层" not in directory.relative_to(root).parts:
            raise ValueError(f"collection must be a text-layer directory: {relative}")
        for path in sorted(directory.glob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            row = batch.checked_document(db, root, path.relative_to(root).as_posix())
            batch.original_pdf(root, row["source"])
            if row["path"] not in seen:
                selected.append(row)
                seen.add(row["path"])
    return selected


def run(args) -> dict:
    root = args.root.resolve()
    work = batch.within(root, args.work_dir)
    if not work.is_relative_to(root / ".ecc"):
        raise ValueError("work directory must stay below .ecc")
    work.mkdir(parents=True, exist_ok=True)
    tesseract = batch.tesseract_binary(args.tesseract)
    db = batch.connect(root / ".ecc" / "proofreading")
    try:
        documents = select_documents(root, db, args.text_root)
        rows = []
        reused_ocr = fresh_ocr = 0
        for index, document in enumerate(documents, 1):
            source = batch.original_pdf(root, document["source"])
            if batch.file_hash(source) != document["source_hash"]:
                raise ValueError(f"source changed since scan: {source}")
            pages = db.execute(
                "SELECT page,text_chars,native_chars,text_layer,native_text "
                "FROM pages WHERE path=? ORDER BY page", (document["path"],)
            ).fetchall()
            for page in pages:
                current = page["text_layer"]
                native = page["native_text"]
                evidence = ""
                cached = False
                if batch.clean(native) and not args.force_ocr_all:
                    reference = native
                    method = "pdf_embedded_text"
                else:
                    reference, evidence_path, cached = render_and_ocr(
                        source, document["source_hash"], page["page"],
                        work / "page-ocr", tesseract, args.render_width,
                    )
                    evidence = evidence_path.relative_to(root).as_posix()
                    method = "tesseract_fresh"
                    reused_ocr += int(cached)
                    fresh_ocr += int(not cached)
                score = ngram_similarity(current, reference)
                rows.append({
                    "collection": Path(document["path"]).parts[1],
                    "text_path": document["path"], "source_path": document["source"],
                    "page": page["page"], "method": method,
                    "text_chars": len(batch.clean(current)),
                    "reference_chars": len(batch.clean(reference)),
                    "comparison": classify(current, reference, score),
                    "trigram_similarity": round(score, 6), "evidence_image": evidence,
                    "text_sha256": hashlib.sha256(batch.clean(current).encode()).hexdigest(),
                    "reference_sha256": hashlib.sha256(batch.clean(reference).encode()).hexdigest(),
                })
            print(f"audit {index}/{len(documents)} {document['path']}", flush=True)
    finally:
        db.close()

    comparison_counts = Counter(row["comparison"] for row in rows)
    method_counts = Counter(row["method"] for row in rows)
    by_collection = defaultdict(Counter)
    for row in rows:
        by_collection[row["collection"]][row["comparison"]] += 1
    changed_sources = [document["source"] for document in documents
                       if batch.file_hash(batch.original_pdf(root, document["source"])) != document["source_hash"]]
    changed_texts = [document["path"] for document in documents
                     if batch.file_hash(batch.text_path(root, document["path"])) != document["text_hash"]]
    summary = {
        "finished": datetime.now().isoformat(timespec="seconds"),
        "documents": len(documents), "pages": len(rows),
        "methods": dict(method_counts), "comparison_counts": dict(comparison_counts),
        "by_collection": {key: dict(value) for key, value in by_collection.items()},
        "fresh_ocr_pages": fresh_ocr, "reused_ocr_pages": reused_ocr,
        "source_pdfs_modified": bool(changed_sources),
        "text_layers_modified": bool(changed_texts),
        "changed_sources_during_run": changed_sources,
        "changed_text_layers_during_run": changed_texts,
        "visual_proofreading_complete": False,
        "scope": "Every selected source-PDF page compared. Embedded PDF text is reused; image-only pages receive fresh local Tesseract OCR.",
    }
    batch.dump(work / "summary.json", summary)
    with (work / "page-comparison.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["text_path"]].append(row)
    document_rows = []
    for path, page_rows in sorted(grouped.items()):
        counts = Counter(row["comparison"] for row in page_rows)
        document_rows.append({
            "collection": page_rows[0]["collection"], "text_path": path,
            "source_path": page_rows[0]["source_path"], "pages": len(page_rows),
            "fresh_ocr_pages": sum(row["method"] == "tesseract_fresh" for row in page_rows),
            "exact_pages": counts["exact_ignoring_layout_whitespace"],
            "near_match_pages": counts["near_match_review"],
            "difference_pages": counts["difference_review"],
            "major_difference_pages": counts["major_difference_review"],
            "minimum_similarity": min(float(row["trigram_similarity"]) for row in page_rows),
        })
    with (work / "document-summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(document_rows[0]))
        writer.writeheader()
        writer.writerows(document_rows)
    priority = [row for row in rows if row["comparison"] == "major_difference_review"]
    with (work / "priority-review.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(priority)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--work-dir", type=Path, default=Path(".ecc/ocr-text-comparison"))
    parser.add_argument("--text-root", type=Path, action="append", required=True)
    parser.add_argument("--tesseract")
    parser.add_argument("--render-width", type=int, default=1100)
    parser.add_argument("--force-ocr-all", action="store_true",
                        help="fresh OCR even when a PDF already contains usable embedded text")
    args = parser.parse_args(argv)
    try:
        if not 700 <= args.render_width <= 2400:
            raise ValueError("render width must be between 700 and 2400")
        result = run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
