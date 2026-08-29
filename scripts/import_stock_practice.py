from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil


TRANSCRIPT_COUNT = 34
ORIGINAL_MEDIA_LINK = re.compile(
    r"\[\[(raw/炒股基础实操/[^\]|]+)(?:\|[^\]]+)?\]\]"
)
PATH_REPLACEMENTS = (
    ("知识库/10-投资与交易/炒股基础实操/", "炒股实操/整理/"),
    ("raw/sources/炒股基础实操/课程转写/", "炒股实操/课程转写/"),
    ("raw/sources/炒股基础实操/来源清单", "炒股实操/来源清单"),
)


def normalize_links(content: str) -> str:
    for old, new in PATH_REPLACEMENTS:
        content = content.replace(old, new)
    content = ORIGINAL_MEDIA_LINK.sub(
        lambda match: f"`server-backup://炒股基础实操-20260729/{match.group(1)}`",
        content,
    )
    content = content.replace(
        "[[raw/炒股基础实操.zip|炒股基础实操.zip]]",
        "`server-backup://炒股基础实操-20260729/raw/炒股基础实操.zip`",
    )
    content = content.replace("`[[知识库/...]]`", "`知识库页面路径`")
    content = content.replace("`[[raw/...]]`", "`原始资料路径`")
    return content


def import_stock_practice(source_root: Path, vault_root: Path) -> Path:
    transcript_source = source_root / "source" / "课程转写"
    source_registry = source_root / "source" / "来源清单.md"
    synthesis_source = source_root / "knowledge"
    target_root = vault_root / "炒股实操"

    transcript_paths = sorted(transcript_source.glob("*.md"))
    if len(transcript_paths) != TRANSCRIPT_COUNT:
        raise ValueError(
            f"expected {TRANSCRIPT_COUNT} transcripts, got {len(transcript_paths)}"
        )
    required_synthesis = {
        "AGENT.md",
        "index.md",
        "00-用例与命令速查.md",
        "01-课程逐讲导航.md",
        "02-股票基础术语速查.md",
        "03-开户券商与交易软件操作指南.md",
        "04-交易系统与风险控制框架.md",
        "05-课程主张待核查清单.md",
    }
    actual_synthesis = {path.name for path in synthesis_source.glob("*.md")}
    if actual_synthesis != required_synthesis:
        raise ValueError(
            "synthesis allowlist mismatch: "
            f"missing={sorted(required_synthesis - actual_synthesis)}, "
            f"extra={sorted(actual_synthesis - required_synthesis)}"
        )
    if not source_registry.is_file():
        raise ValueError("source registry is missing")
    if target_root.exists():
        raise FileExistsError(f"target already exists: {target_root}")

    (target_root / "课程转写").mkdir(parents=True)
    (target_root / "整理").mkdir()
    files = [source_registry, *transcript_paths]
    files.extend(sorted(synthesis_source.glob("*.md")))
    for source_path in files:
        if source_path == source_registry:
            relative_target = Path("来源清单.md")
        elif source_path.parent == transcript_source:
            relative_target = Path("课程转写") / source_path.name
        else:
            relative_target = Path("整理") / source_path.name
        target_path = target_root / relative_target
        target_path.write_text(
            normalize_links(source_path.read_text(encoding="utf-8")),
            encoding="utf-8",
        )
        shutil.copystat(source_path, target_path)
    return target_root


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import the allowlisted text layer of the private stock-practice archive."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    imported = import_stock_practice(args.source.resolve(), args.root.resolve())
    print(f"Imported stock-practice text to {imported}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
