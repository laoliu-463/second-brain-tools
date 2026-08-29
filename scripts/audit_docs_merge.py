"""审计 D:\\Docs 与当前第二大脑之间的内容合并状态。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


HASH_CHUNK_SIZE = 8 * 1024 * 1024
MEDIA_EXTENSIONS = {
    ".mp3",
    ".mp4",
    ".mov",
    ".flv",
    ".wav",
    ".m4a",
    ".avi",
    ".mkv",
}
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz"}
TEMP_EXTENSIONS = {".log", ".tmp", ".temp", ".pyc"}
REBUILDABLE_DIRS = {
    "tmp",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "site-packages",
    ".cache",
    ".pytest_cache",
    ".vite",
    ".next",
}
WINDOWS_DEVICE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass(frozen=True)
class FileRecord:
    path: Path
    relative_path: str
    size: int
    modified_ns: int


def gib(value: int) -> float:
    return round(value / (1024**3), 3)


def iter_files(root: Path, *, excluded_dirs: set[str]) -> list[FileRecord]:
    records: list[FileRecord] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [
            name
            for name in dirs
            if name.casefold() not in excluded_dirs
            and not (Path(current) / name).is_symlink()
        ]
        current_path = Path(current)
        for name in files:
            path = current_path / name
            try:
                stat = path.stat()
            except OSError:
                records.append(
                    FileRecord(
                        path=path,
                        relative_path=str(path.relative_to(root)),
                        size=-1,
                        modified_ns=0,
                    )
                )
                continue
            records.append(
                FileRecord(
                    path=path,
                    relative_path=str(path.relative_to(root)),
                    size=stat.st_size,
                    modified_ns=stat.st_mtime_ns,
                )
            )
    return records


def hash_file(path: Path) -> str:
    if path.stem.upper() in WINDOWS_DEVICE_NAMES:
        raise OSError(f"跳过 Windows 特殊设备名：{path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest().upper()


def hash_records(
    records: list[FileRecord],
    *,
    label: str,
    allowed_sizes: set[int] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    hashes: dict[str, str] = {}
    errors: dict[str, str] = {}
    selected = [
        record
        for record in records
        if record.size >= 0
        and (allowed_sizes is None or record.size in allowed_sizes)
    ]
    total_bytes = sum(record.size for record in selected)
    hashed_bytes = 0
    for index, record in enumerate(selected, start=1):
        key = str(record.path)
        try:
            hashes[key] = hash_file(record.path)
        except OSError as exc:
            errors[key] = f"{type(exc).__name__}: {exc}"
        hashed_bytes += record.size
        if index % 250 == 0 or index == len(selected):
            print(
                f"[{label}] {index}/{len(selected)} files, "
                f"{gib(hashed_bytes)}/{gib(total_bytes)} GiB",
                flush=True,
            )
    return hashes, errors


def classify(relative_path: str, extension: str, matched: bool) -> tuple[str, str]:
    parts = {part.casefold() for part in Path(relative_path).parts}
    if matched:
        return "已迁移（哈希匹配）", "复核来源映射后，可从旧库删除"
    if (
        bool(parts & REBUILDABLE_DIRS)
        or extension in TEMP_EXTENSIONS
    ):
        return "临时文件候选", "确认没有运行任务依赖后清理"
    if extension in MEDIA_EXTENSIONS or extension in ARCHIVE_EXTENSIONS:
        return "媒体或压缩包待决定", "迁入媒体归档，或明确授权删除"
    return "待合并审核", "合并到新第二大脑并复核后处理"


def directory_size(root: Path) -> tuple[int, int]:
    count = 0
    total = 0
    if not root.exists():
        return count, total
    for current, _, files in os.walk(root, followlinks=False):
        for name in files:
            try:
                total += (Path(current) / name).stat().st_size
                count += 1
            except OSError:
                continue
    return count, total


def write_outputs(
    *,
    old_root: Path,
    new_root: Path,
    output_csv: Path,
    output_json: Path,
) -> dict[str, object]:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    old_records = iter_files(old_root, excluded_dirs={".git"})
    new_records = iter_files(
        new_root,
        excluded_dirs={".git", ".ecc", "__pycache__"},
    )
    old_sizes = {record.size for record in old_records if record.size >= 0}

    print(
        f"[scan] old={len(old_records)} files/{gib(sum(max(r.size, 0) for r in old_records))} GiB; "
        f"new={len(new_records)} files/{gib(sum(max(r.size, 0) for r in new_records))} GiB",
        flush=True,
    )

    new_hashes, new_errors = hash_records(
        new_records,
        label="new",
        allowed_sizes=old_sizes,
    )
    new_by_hash: dict[str, list[str]] = defaultdict(list)
    for path, digest in new_hashes.items():
        new_by_hash[digest].append(path)

    old_hashes, old_errors = hash_records(old_records, label="old")
    old_hash_counts = Counter(old_hashes.values())

    rows: list[dict[str, object]] = []
    category_counts: Counter[str] = Counter()
    category_bytes: Counter[str] = Counter()
    extension_unmatched_bytes: Counter[str] = Counter()
    extension_unmatched_counts: Counter[str] = Counter()

    for record in sorted(old_records, key=lambda item: item.relative_path.casefold()):
        path_key = str(record.path)
        digest = old_hashes.get(path_key, "")
        matches = new_by_hash.get(digest, []) if digest else []
        extension = record.path.suffix.casefold() or "(无扩展名)"
        category, suggestion = classify(record.relative_path, extension, bool(matches))
        if path_key in old_errors or record.size < 0:
            category = "读取失败"
            suggestion = "人工检查路径和文件状态"
        category_counts[category] += 1
        category_bytes[category] += max(record.size, 0)
        if not matches:
            extension_unmatched_counts[extension] += 1
            extension_unmatched_bytes[extension] += max(record.size, 0)
        modified = (
            datetime.fromtimestamp(record.modified_ns / 1_000_000_000).isoformat(
                timespec="seconds"
            )
            if record.modified_ns
            else ""
        )
        rows.append(
            {
                "旧路径": path_key,
                "相对路径": record.relative_path,
                "大小字节": max(record.size, 0),
                "扩展名": extension,
                "修改时间": modified,
                "SHA256": digest,
                "旧库同哈希数量": old_hash_counts.get(digest, 0) if digest else 0,
                "新库状态": category,
                "新库匹配路径": " | ".join(sorted(matches)),
                "处理建议": suggestion,
                "错误": old_errors.get(path_key, ""),
            }
        )

    csv_temp = output_csv.with_suffix(output_csv.suffix + ".tmp")
    with csv_temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    os.replace(csv_temp, output_csv)

    git_count, git_bytes = directory_size(
        old_root / "Books" / "my second brain" / ".git"
    )
    unmatched_extensions = [
        {
            "extension": extension,
            "files": extension_unmatched_counts[extension],
            "bytes": extension_unmatched_bytes[extension],
            "GiB": gib(extension_unmatched_bytes[extension]),
        }
        for extension in sorted(
            extension_unmatched_bytes,
            key=extension_unmatched_bytes.get,
            reverse=True,
        )[:25]
    ]
    summary: dict[str, object] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "old_root": str(old_root),
        "new_root": str(new_root),
        "old_non_git_files": len(old_records),
        "old_non_git_bytes": sum(max(record.size, 0) for record in old_records),
        "old_non_git_GiB": gib(sum(max(record.size, 0) for record in old_records)),
        "excluded_git_files": git_count,
        "excluded_git_bytes": git_bytes,
        "excluded_git_GiB": gib(git_bytes),
        "new_files_scanned": len(new_records),
        "new_candidate_hash_errors": len(new_errors),
        "old_hash_errors": len(old_errors),
        "categories": {
            category: {
                "files": category_counts[category],
                "bytes": category_bytes[category],
                "GiB": gib(category_bytes[category]),
            }
            for category in sorted(category_counts)
        },
        "largest_unmatched_extensions": unmatched_extensions,
        "csv": str(output_csv),
    }
    json_temp = output_json.with_suffix(output_json.suffix + ".tmp")
    json_temp.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(json_temp, output_json)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-root", type=Path, default=Path(r"D:\Docs"))
    parser.add_argument(
        "--new-root", type=Path, default=Path(r"D:\第二大脑")
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path(
            r"D:\第二大脑\.ecc\audits\docs-merge-20260825\D盘Docs合并清单.csv"
        ),
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path(
            r"D:\第二大脑\.ecc\audits\docs-merge-20260825\D盘Docs合并统计.json"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.old_root.is_dir() or not args.new_root.is_dir():
        print("旧目录或新目录不存在", file=sys.stderr)
        return 2
    summary = write_outputs(
        old_root=args.old_root.resolve(),
        new_root=args.new_root.resolve(),
        output_csv=args.output_csv.resolve(),
        output_json=args.output_json.resolve(),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
