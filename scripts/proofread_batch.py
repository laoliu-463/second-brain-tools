"""Read-only full-page comparison; reuse ocr/ocr_run.py for OCR candidates.

scan -> ocr (manifest only unless --run) -> prepare -> apply (dry unless --write).
Machine equality and successful OCR never constitute visual proofreading.
All run data stays below the vault's .ecc directory. Source PDFs are read-only.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import fitz
import yaml

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1:" + fitz.VersionBind
PAGE = re.compile(r"^## Page (\d+)[ \t]*\r?$", re.M)
FRONT = re.compile(r"\A\ufeff?---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)
EMPTY = re.compile(r"^\[(?:本页未提取到文本|本页 OCR 未识别到文本|经原始 PDF 视觉核验)[^\n]*\]\s*$", re.M)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dump(path: Path, value) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".proofread-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def within(base: Path, value: str | Path) -> Path:
    value = Path(value)
    result = (value if value.is_absolute() else base / value).resolve()
    if not result.is_relative_to(base.resolve()):
        raise ValueError(f"path escapes {base}: {value}")
    return result


def text_path(root: Path, value: str | Path) -> Path:
    path = within(root, value)
    parts = path.relative_to(root.resolve()).parts
    if (not parts or parts[0] != "资料库" or "文字层" not in parts
            or ".ecc" in parts or "原始资料" in parts or "原始资料层" in parts
            or path.suffix.lower() != ".md" or path.name.lower() == "readme.md"):
        raise ValueError(f"not a library text-layer file: {value}")
    return path


@contextmanager
def work_lock(work: Path):
    work.mkdir(parents=True, exist_ok=True)
    lock = work / "run.lock"
    try:
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
    except FileExistsError as exc:
        raise ValueError(f"work directory is locked; verify no running process before removing {lock}") from exc
    try:
        yield
    finally:
        lock.unlink()


def parse(text: str):
    match = FRONT.match(text)
    issues = []
    meta = {}
    if match:
        try:
            meta = yaml.safe_load(match[1])
            if not isinstance(meta, dict):
                raise ValueError("frontmatter is not a mapping")
        except (yaml.YAMLError, ValueError):
            issues.append("invalid_yaml")
            # Recover identity for diagnostics; this does NOT validate the YAML.
            meta = {k: v.strip().strip('\"\'') for k, v in
                    re.findall(r"^([a-z0-9_]+):[ \t]*(.*)$", match[1], re.M)}
    else:
        issues.append("missing_frontmatter")
    marks = list(PAGE.finditer(text))
    numbers = [int(m[1]) for m in marks]
    if numbers != list(range(1, len(marks) + 1)) or not numbers:
        issues.append("invalid_page_sequence")
    sections = {int(m[1]): text[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(text)].strip()
                for i, m in enumerate(marks)}
    return meta, marks, sections, issues


def clean(text: str) -> str:
    return re.sub(r"\s+", "", EMPTY.sub("", text))


def compare(a: str, b: str) -> str:
    a, b = clean(a), clean(b)
    if not a and not b:
        return "both_empty_requires_visual_or_ocr"
    if not a:
        return "text_missing_native_available"
    if not b:
        return "source_image_requires_visual"
    if a == b:
        return "exact_after_layout_whitespace_only"
    return "character_difference_requires_review"


def diagnostics(text: str) -> list[str]:
    flags = []
    if re.search(r"/G[0-9A-Fa-f]{2,}", text):
        flags.append("glyph_encoding")
    if "\ufffd" in text:
        flags.append("replacement_character")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
        flags.append("control_character")
    return flags


def connect(work: Path):
    db = sqlite3.connect(work / "comparison.sqlite")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            path TEXT PRIMARY KEY, text_hash TEXT, source TEXT, source_hash TEXT,
            signature TEXT, status TEXT, issues TEXT, pages INTEGER);
        CREATE TABLE IF NOT EXISTS pages (
            path TEXT, page INTEGER, result TEXT, text_chars INTEGER,
            native_chars INTEGER, flags TEXT, text_layer TEXT, native_text TEXT,
            PRIMARY KEY(path,page));
    """)
    return db


def discover(root: Path):
    texts, sources = [], defaultdict(list)
    for path in sorted((root / "资料库").rglob("*")):
        if not path.is_file() or ".ecc" in path.parts:
            continue
        if path.suffix.lower() == ".md" and "文字层" in path.parts and path.name.lower() != "readme.md":
            texts.append(text_path(root, path))
        elif path.suffix.lower() == ".pdf" and ({"原始资料", "原始资料层"} & set(path.parts)):
            path = within(root, path)
            sources[file_hash(path)].append(path)
    return texts, sources


def scan(root: Path, work: Path) -> dict:
    texts, sources = discover(root)
    db = connect(work)
    scanned = resumed = 0
    active = {p.relative_to(root).as_posix() for p in texts}
    with db:
        for row in db.execute("SELECT path FROM documents").fetchall():
            if row["path"] not in active:
                db.execute("DELETE FROM documents WHERE path=?", (row["path"],))
                db.execute("DELETE FROM pages WHERE path=?", (row["path"],))
    try:
        for i, path in enumerate(texts, 1):
            rel = path.relative_to(root).as_posix()
            raw = path.read_bytes()
            text_hash = digest(raw)
            source = source_hash = ""
            issues, rows = [], []
            status = "complete"
            try:
                meta, _, sections, issues = parse(raw.decode("utf-8-sig"))
                source_hash = str(meta.get("source_sha256", "")).lower()
                if source_hash not in sources:
                    raise ValueError("source_sha256 has no matching original PDF")
                source_path = sources[source_hash][0]
                source = source_path.relative_to(root).as_posix()
                signature = digest((VERSION + text_hash + source_hash + source).encode())
                old = db.execute("SELECT signature,status FROM documents WHERE path=?", (rel,)).fetchone()
                if old and old["signature"] == signature and old["status"] == "complete":
                    resumed += 1
                    continue
                with fitz.open(source_path) as pdf:
                    if list(sections) != list(range(1, len(pdf) + 1)):
                        issues.append("source_page_count_mismatch")
                    if str(meta.get("pages")) != str(len(pdf)):
                        issues.append("declared_page_count_mismatch")
                    for number, page in enumerate(pdf, 1):
                        a = sections.get(number, "")
                        try:
                            b = page.get_text(sort=False)
                            result = compare(a, b)
                        except Exception as exc:
                            b, result = "", "native_extraction_error"
                            issues.append(f"page {number}: {type(exc).__name__}: {exc}")
                        rows.append((rel, number, result, len(clean(a)), len(clean(b)),
                                     json.dumps(diagnostics(a), ensure_ascii=False), a, b))
                if file_hash(path) != text_hash or file_hash(source_path) != source_hash:
                    raise ValueError("file changed during scan; rerun to refresh")
            except Exception as exc:
                status = "error"
                issues.append(f"{type(exc).__name__}: {exc}")
            signature = digest((VERSION + text_hash + source_hash + source).encode())
            with db:
                db.execute("DELETE FROM pages WHERE path=?", (rel,))
                db.executemany("INSERT INTO pages VALUES (?,?,?,?,?,?,?,?)", rows)
                db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?)",
                           (rel, text_hash, source, source_hash, signature, status,
                            json.dumps(issues, ensure_ascii=False), len(rows)))
            scanned += 1
            if i % 10 == 0 or i == len(texts):
                print(f"scan {i}/{len(texts)}", flush=True)
        counts = dict(db.execute("SELECT result,count(*) FROM pages GROUP BY result"))
        documents = [dict(row) for row in db.execute("SELECT * FROM documents ORDER BY path")]
        changed = [r["path"] for r in documents if file_hash(root / r["path"]) != r["text_hash"]]
        summary = dict(files=len(texts), pages=sum(counts.values()), scanned=scanned, resumed=resumed,
                       comparison_counts=counts, changed_during_run=changed,
                       error_files=sum(r["status"] != "complete" for r in documents),
                       files_with_issues=sum(r["issues"] != "[]" for r in documents),
                       visual_proofreading_complete=False,
                       scope="All PDF pages and all text characters compared, ignoring whitespace only; NOT visual proofreading.")
        dump(work / "summary.json", summary)
        dump(work / "documents.json", documents)
        with (work / "pages.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["text_path", "page", "result", "text_chars", "native_chars", "flags"])
            writer.writerows(db.execute("SELECT path,page,result,text_chars,native_chars,flags FROM pages ORDER BY path,page"))
        return summary
    finally:
        db.close()


def checked_document(db, root: Path, path: str):
    target = text_path(root, path)
    row = db.execute("SELECT * FROM documents WHERE path=?", (target.relative_to(root).as_posix(),)).fetchone()
    if not row or row["status"] != "complete":
        raise ValueError(f"run scan successfully first: {path}")
    source = within(root, row["source"])
    if file_hash(target) != row["text_hash"] or file_hash(source) != row["source_hash"]:
        raise ValueError(f"file changed; rerun scan: {path}")
    return dict(row)


def original_pdf(root: Path, value: str | Path) -> Path:
    path = within(root, value)
    parts = path.relative_to(root).parts
    if (path.suffix.lower() != ".pdf"
            or not ({"原始资料", "原始资料层"} & set(parts))
            or ".ecc" in parts):
        raise ValueError(f"OCR input must be a PDF in an original-material layer: {value}")
    return path


def ocr_manifest(root: Path, work: Path, paths: list[str]) -> Path:
    """Feed verified current source paths into the EXISTING OCR runner."""
    db = connect(work)
    try:
        rows = [checked_document(db, root, p) for p in paths]
    finally:
        db.close()
    books = {}
    for row in rows:
        source = original_pdf(root, row["source"])
        # Hash-bound IDs avoid reusing progress for a different source revision.
        ident = "proof_" + row["source_hash"]
        books[ident] = dict(id=ident, title=Path(row["source"]).stem[:40],
                            source=str(source), pages=row["pages"],
                            mode="force", language="chi_sim+eng", extra_args=[])
    job_id = digest(json.dumps(books, sort_keys=True).encode())[:20]
    job = work / "ocr" / job_id
    job.mkdir(parents=True, exist_ok=True)
    manifest = job / "manifest.yaml"
    data = dict(output_dir=str(job / "candidates"), db_path=str(job / "progress.sqlite"),
                books=list(books.values()))
    atomic_write(manifest, yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode("utf-8"))
    dump(job / "source-lock.json", rows)
    return manifest


def tiles(rect, height=1600, overlap=60):
    if height <= overlap or overlap < 0:
        raise ValueError("tile height must exceed nonnegative overlap")
    y = rect.y0
    while y < rect.y1:
        end = min(y + height, rect.y1)
        yield fitz.Rect(rect.x0, y, rect.x1, end)
        if end == rect.y1:
            break
        y = end - overlap


def page_numbers(value: str | None, count: int) -> list[int]:
    if value is None:
        return list(range(1, count + 1))
    chosen = set()
    for part in value.split(","):
        endpoints = part.split("-")
        lo, hi = int(endpoints[0]), int(endpoints[-1])
        if len(endpoints) > 2 or not 1 <= lo <= hi <= count:
            raise ValueError(f"page selection outside 1-{count}: {part}")
        chosen.update(range(lo, hi + 1))
    return sorted(chosen)


def prepare(root: Path, work: Path, path: str, selection: str | None) -> Path:
    db = connect(work)
    try:
        row = checked_document(db, root, path)
        if any(i in json.loads(row["issues"]) for i in ("invalid_page_sequence", "source_page_count_mismatch")):
            raise ValueError("repair page structure with source evidence before preparing replacements")
        job = work / "reviews" / uuid.uuid4().hex
        job.mkdir(parents=True)
        entries = []
        with fitz.open(original_pdf(root, row["source"])) as pdf:
            for n in page_numbers(selection, len(pdf)):
                evidence = []
                page = pdf[n - 1]
                for i, clip in enumerate(tiles(page.rect), 1):
                    png = job / f"page-{n:04d}-tile-{i:03d}.png"
                    page.get_pixmap(matrix=fitz.Matrix(1100 / page.rect.width, 1100 / page.rect.width), clip=clip).save(png)
                    evidence.append(dict(path=str(png.relative_to(work)), sha256=file_hash(png)))
                contents = db.execute("SELECT text_layer,native_text FROM pages WHERE path=? AND page=?", (row["path"], n)).fetchone()
                replacement = job / f"page-{n:04d}-replacement.txt"
                replacement.write_text(contents["text_layer"], encoding="utf-8", newline="")
                (job / f"page-{n:04d}-native.txt").write_text(contents["native_text"], encoding="utf-8", newline="")
                entries.append(dict(page=n, replacement=str(replacement.relative_to(work)),
                                    approved=False, reviewer="", reason="", evidence=evidence))
        if (file_hash(text_path(root, row["path"])) != row["text_hash"]
                or file_hash(original_pdf(root, row["source"])) != row["source_hash"]):
            raise ValueError("source/text changed while preparing review; discard and retry")
        plan = dict(version=1, root=str(root), text_path=row["path"], text_sha256=row["text_hash"],
                    source_path=row["source"], source_sha256=row["source_hash"], pages=entries)
        result = job / "review.json"
        dump(result, plan)
        return result
    finally:
        db.close()


def fallback_pages(root: Path, work: Path, path: str, sample: int) -> str | None:
    if not sample:
        return None
    db = connect(work)
    try:
        count = checked_document(db, root, path)["pages"]
    finally:
        db.close()
    return f"1-{min(sample, count)}"


def tesseract_binary(value: str | None = None) -> str:
    candidates = [value, shutil.which("tesseract"), r"C:\Program Files\Tesseract-OCR\tesseract.exe"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise ValueError("Tesseract not found; pass --tesseract with the executable path")


def tile_ocr(review: Path, work: Path, executable: str | None = None) -> dict:
    """OCR every rendered source tile; keep candidates separate and auditable."""
    review = within(work, review)
    plan = json.loads(review.read_text(encoding="utf-8"))
    binary = tesseract_binary(executable)
    env = os.environ.copy()
    env["OMP_THREAD_LIMIT"] = "1"
    results = []
    for entry in plan["pages"]:
        entry["ocr_candidates"] = []
        for evidence in entry["evidence"]:
            image = within(work, evidence["path"])
            if file_hash(image) != evidence["sha256"]:
                raise ValueError("visual evidence changed before OCR")
            prefix = image.with_name(image.stem + "-ocr")
            result = subprocess.run(
                [binary, str(image), str(prefix), "-l", "chi_sim+eng", "--psm", "6"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            output = prefix.with_suffix(".txt")
            record = dict(image=str(image.relative_to(work)), returncode=result.returncode,
                          output=str(output.relative_to(work)) if output.is_file() else "",
                          stderr=result.stderr[-500:])
            entry["ocr_candidates"].append(record)
            results.append(record)
    plan["tile_ocr"] = dict(engine="Tesseract chi_sim+eng", completed=datetime.now().isoformat(timespec="seconds"),
                            candidates=len(results), failures=sum(r["returncode"] != 0 or not r["output"] for r in results),
                            note="Per-tile OCR candidates are not merged and are not approved text.")
    dump(review, plan)
    dump(review.parent / "tile-ocr-summary.json", plan["tile_ocr"])
    return plan["tile_ocr"]


def apply_review(root: Path, work: Path, review: Path, write=False) -> dict:
    review = within(work, review)
    review_raw = review.read_bytes()
    plan = json.loads(review_raw)
    if plan.get("version") != 1 or Path(plan["root"]).resolve() != root.resolve():
        raise ValueError("review version/root mismatch")
    target = text_path(root, plan["text_path"])
    source = within(root, plan["source_path"])
    raw = target.read_bytes()
    if digest(raw) != plan["text_sha256"] or file_hash(source) != plan["source_sha256"]:
        raise ValueError("source/text changed since review; refusing overwrite")
    text = raw.decode("utf-8-sig")
    meta, marks, sections, issues = parse(text)
    if "invalid_page_sequence" in issues or not FRONT.match(text):
        raise ValueError("invalid document structure")
    if str(meta.get("source_sha256", "")).lower() != plan["source_sha256"]:
        raise ValueError("review source does not match document provenance")
    with fitz.open(source) as pdf:
        if len(pdf) != len(marks):
            raise ValueError("source page count mismatch")
    replacements = {}
    for entry in plan["pages"]:
        n = entry["page"]
        if n not in sections or n in replacements:
            raise ValueError("invalid/duplicate reviewed page")
        if entry.get("approved") is not True or not entry.get("reviewer", "").strip() or not entry.get("reason", "").strip():
            raise ValueError("each selected page needs approval, reviewer and source-based reason")
        if not entry.get("evidence"):
            raise ValueError("missing original-page visual evidence")
        for evidence in entry["evidence"]:
            if file_hash(within(work, evidence["path"])) != evidence["sha256"]:
                raise ValueError("visual evidence changed")
        replacement = within(work, entry["replacement"]).read_text(encoding="utf-8-sig").strip()
        if PAGE.search(replacement):
            raise ValueError("replacement must contain page body only")
        replacements[n] = replacement
    if not replacements:
        raise ValueError("no reviewed pages")
    output = text
    for i in range(len(marks) - 1, -1, -1):
        mark = marks[i]
        if int(mark[1]) in replacements:
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            output = output[:mark.end()] + "\n\n" + replacements[int(mark[1])] + "\n\n" + output[end:]
    # Record this review without promoting old confidence or visual-review claims.
    front = FRONT.match(output)
    header = front[1]
    if "invalid_yaml" in issues:
        header = re.sub(r"^source_file:.*$", lambda _: "source_file: " + json.dumps(meta.get("source_file", ""), ensure_ascii=False), header, flags=re.M)
    if not isinstance(yaml.safe_load(header), dict):
        raise ValueError("frontmatter still invalid")
    new_sections = parse(output)[2]
    updates = dict(text_chars=sum(len(clean(t)) for t in new_sections.values()),
                   text_pages=sum(bool(clean(t)) for t in new_sections.values()),
                   proofread_status="reviewed_selected_pages", proofread_record=str(review.relative_to(root)),
                   proofread_date=datetime.now().date().isoformat())
    for key, value in updates.items():
        line = key + ": " + json.dumps(value, ensure_ascii=False)
        pattern = rf"^{key}:.*$"
        header = re.sub(pattern, lambda _: line, header, flags=re.M) if re.search(pattern, header, re.M) else header + "\n" + line
    output = "---\n" + header + "\n---\n" + output[front.end():]
    new_raw = output.encode("utf-8")
    receipt = dict(text_path=plan["text_path"], before_sha256=digest(raw), after_sha256=digest(new_raw),
                   reviewed_pages=sorted(replacements), written=False, visual_proofreading_complete=False)
    run = work / "applications" / uuid.uuid4().hex
    run.mkdir(parents=True)
    (run / "changes.diff").write_text("".join(difflib.unified_diff(text.splitlines(True), output.splitlines(True), fromfile=plan["text_path"], tofile=plan["text_path"])), encoding="utf-8", newline="")
    if write:
        # An immutable per-application backup, even when the same book is edited again.
        (run / "before.md").write_bytes(raw)
        (run / "review.json").write_bytes(review_raw)
        receipt["backup"] = str((run / "before.md").relative_to(root))
        dump(run / "receipt.json", receipt)
        if file_hash(target) != digest(raw) or file_hash(source) != plan["source_sha256"]:
            raise ValueError("file changed immediately before write")
        atomic_write(target, new_raw)
        receipt["written"] = True
        if file_hash(target) != receipt["after_sha256"]:
            raise ValueError("post-write verification failed; see backup")
    dump(run / "receipt.json", receipt)
    return {**receipt, "receipt": str(run / "receipt.json")}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--work-dir", type=Path, default=Path(".ecc/proofreading"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("scan", help="read every page; unchanged documents resume by hashes")
    ocr = commands.add_parser("ocr", help="generate manifest; reuse existing OCR runner")
    ocr.add_argument("--path", action="append", required=True, help="repeat to batch multiple text files")
    ocr.add_argument("--run", action="store_true", help="actually run local OCR, never ingest automatically")
    ocr.add_argument("--sample", type=int, default=0)
    ocr.add_argument("--max-workers", type=int, default=2)
    ocr.add_argument("--tesseract", help="local Tesseract path for oversized-page fallback")
    ocr.add_argument("--no-tile-fallback", action="store_true",
                     help="do not render and OCR source tiles if OCRmyPDF fails")
    prep = commands.add_parser("prepare", help="render all selected pages and create unapproved review")
    prep.add_argument("--path", required=True)
    prep.add_argument("--pages", help="e.g. 1,3-5; default ALL pages, including tall-page tiles")
    apply = commands.add_parser("apply", help="reviewed replacements; dry run unless --write")
    apply.add_argument("--review", type=Path, required=True)
    apply.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    work = within(root, args.work_dir)
    if not work.is_relative_to(root / ".ecc"):
        parser.error("--work-dir must stay under the vault's .ecc directory")
    try:
        with work_lock(work):
            if args.command == "scan":
                result = scan(root, work)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return int(bool(result["error_files"] or result["changed_during_run"]))
            if args.command == "ocr":
                manifest = ocr_manifest(root, work, args.path)
                command = [sys.executable, "-X", "utf8", str(ROOT / "scripts/ocr/ocr_run.py"),
                           "--manifest", str(manifest), "--sample", str(args.sample),
                           "--max-workers", str(args.max_workers)]
                if not args.run:
                    command.append("--dry-run")
                print(f"manifest: {manifest}", flush=True)
                # Explicit pipes preserve child diagnostics in Windows hidden-console runs.
                with subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, encoding="utf-8", errors="replace",
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
                    for line in process.stdout:
                        print(line, end="", flush=True)
                    returncode = process.wait()
                if returncode and args.run and not args.no_tile_fallback:
                    print("OCRmyPDF failed; switching to original-PDF tile OCR.", flush=True)
                    failures = 0
                    for path in args.path:
                        pages = fallback_pages(root, work, path, args.sample)
                        review = prepare(root, work, path, pages)
                        result = tile_ocr(review, work, args.tesseract)
                        failures += result["failures"]
                        print(json.dumps({"review": str(review), **result}, ensure_ascii=False), flush=True)
                    return int(bool(failures))
                return returncode
            if args.command == "prepare":
                print(prepare(root, work, args.path, args.pages))
            else:
                print(json.dumps(apply_review(root, work, args.review, args.write), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
