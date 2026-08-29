from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote


ARTICLE_DIR = Path("人智55篇") / "正文"
COURSE_DIR = Path("大小课") / "课程正文"
PRACTICE_DIR = Path("炒股实操") / "课程转写"
TOOLS_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = TOOLS_ROOT / "schema" / "corpus-lock.json"
LOCAL_DERIVED_MARKDOWN_DIRS = (Path("资料库") / "文字层",)

ARTICLE_LINKS_START = "<!-- COURSE-LINKS-START -->"
ARTICLE_LINKS_END = "<!-- COURSE-LINKS-END -->"
COURSE_TRANSCRIPT_START = "<!-- COURSE-TRANSCRIPT-START -->"
COURSE_TRANSCRIPT_END = "<!-- COURSE-TRANSCRIPT-END -->"

REQUIRED_FILES = (
    Path("AGENTS.md"),
    Path("README.md"),
    Path("log.md"),
    Path("具体政策与权威数据历史台账.md"),
    Path("人智55篇") / "目录.md",
    Path("人智55篇") / "大小课对应表.md",
    Path("大小课") / "目录.md",
    Path("大小课") / "联动检查报告.md",
    Path("炒股实操") / "来源清单.md",
    Path("炒股实操") / "整理" / "AGENT.md",
    Path("炒股实操") / "整理" / "index.md",
    Path("炒股实操") / "整理" / "00-用例与命令速查.md",
    Path("炒股实操") / "整理" / "01-课程逐讲导航.md",
    Path("炒股实操") / "整理" / "02-股票基础术语速查.md",
    Path("炒股实操") / "整理" / "03-开户券商与交易软件操作指南.md",
    Path("炒股实操") / "整理" / "04-交易系统与风险控制框架.md",
    Path("炒股实操") / "整理" / "05-课程主张待核查清单.md",
)

WIKI_LINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")
MARKDOWN_LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\((<[^>]+>|[^)]+)\)")
ARTICLE_FILENAME_PATTERN = re.compile(r"^(\d{2})-.+\.md$")
COURSE_FILENAME_PATTERN = re.compile(r"^(\d{2})-.+\.md$")
PRACTICE_FILENAME_PATTERN = re.compile(r"^(\d{2}).+\.md$")
SECRET_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|token|secret)\s*[:=]\s*[\"']?"
        r"[A-Za-z0-9_-]{20,}"
    ),
)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def error_codes(self) -> set[str]:
        return {issue.code for issue in self.issues}

    def add(self, code: str, path: Path | str, message: str) -> None:
        path_text = path.as_posix() if isinstance(path, Path) else path
        self.issues.append(ValidationIssue(code, path_text, message))

    def format_errors(self) -> str:
        return "\n".join(
            f"ERROR [{issue.code}] {issue.path}: {issue.message}"
            for issue in self.issues
        )


@dataclass(frozen=True)
class WikiResolution:
    status: str
    path: Path | None = None


def strip_fenced_code(content: str) -> str:
    """Remove fenced code blocks before scanning Markdown links."""
    without_backticks = re.sub(
        r"(?ms)^```[^\n]*\n.*?^```[ \t]*$",
        "",
        content,
    )
    return re.sub(
        r"(?ms)^~~~[^\n]*\n.*?^~~~[ \t]*$",
        "",
        without_backticks,
    )


def parse_frontmatter(content: str) -> dict[str, str]:
    """Parse the scalar fields needed by the validator."""
    lines = content.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    try:
        end_index = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration:
        return {}

    fields: dict[str, str] = {}
    for line in lines[1:end_index]:
        if ":" not in line or line.startswith((" ", "\t")):
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"')
    return fields


def extract_wiki_targets(content: str) -> list[str]:
    """Return normalized Obsidian Wiki-link targets outside code fences."""
    targets: list[str] = []
    for match in WIKI_LINK_PATTERN.finditer(strip_fenced_code(content)):
        target = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
        if not target:
            continue
        target = unquote(target).replace("\\", "/")
        if target.lower().endswith(".md"):
            target = target[:-3]
        targets.append(target.strip("/"))
    return targets


def read_text(path: Path, report: ValidationReport | None = None) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        if report is not None:
            report.add("read_error", path, str(error))
        return ""


def list_markdown_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.md")
        if not {".git", ".obsidian", ".ecc"}.intersection(path.relative_to(root).parts)
        and not any(
            path.resolve().is_relative_to((root / directory).resolve())
            for directory in LOCAL_DERIVED_MARKDOWN_DIRS
        )
    )


def relative_path(root: Path, path: Path) -> Path:
    return path.resolve().relative_to(root.resolve())


def build_wiki_index(
    root: Path,
    markdown_files: list[Path],
) -> tuple[set[str], dict[str, list[Path]], dict[str, str]]:
    exact_paths: set[str] = set()
    basename_index: dict[str, list[Path]] = {}
    casefold_paths: dict[str, str] = {}

    for path in markdown_files:
        relative = relative_path(root, path).with_suffix("")
        path_key = relative.as_posix()
        exact_paths.add(path_key)
        casefold_paths[path_key.casefold()] = path_key
        basename_index.setdefault(relative.name.casefold(), []).append(relative)

    return exact_paths, basename_index, casefold_paths


def candidate_key(root: Path, candidate: Path) -> str | None:
    try:
        return candidate.resolve().relative_to(root.resolve()).with_suffix("").as_posix()
    except ValueError:
        return None


def resolve_wiki_target(
    root: Path,
    source: Path,
    target: str,
    exact_paths: set[str],
    basename_index: dict[str, list[Path]],
    casefold_paths: dict[str, str],
) -> WikiResolution:
    target_path = Path(*target.split("/"))
    candidates = (root / target_path, source.parent / target_path)

    for candidate in candidates:
        key = candidate_key(root, candidate)
        if key is None:
            continue
        if key in exact_paths:
            return WikiResolution("resolved", Path(key + ".md"))
        actual = casefold_paths.get(key.casefold())
        if actual is not None:
            return WikiResolution("case_mismatch", Path(actual + ".md"))

    if "/" not in target:
        matches = basename_index.get(Path(target).name.casefold(), [])
        if len(matches) == 1:
            return WikiResolution("resolved", matches[0].with_suffix(".md"))
        if len(matches) > 1:
            return WikiResolution("ambiguous")

    return WikiResolution("missing")


def validate_required_files(root: Path, report: ValidationReport) -> None:
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            report.add("required_file_missing", relative, "Required maintenance file is missing")


def validate_numbered_corpus(
    root: Path,
    report: ValidationReport,
) -> tuple[list[Path], list[Path], list[Path]]:
    article_root = root / ARTICLE_DIR
    course_root = root / COURSE_DIR
    article_files = sorted(article_root.glob("*.md")) if article_root.is_dir() else []
    course_files = sorted(course_root.glob("*.md")) if course_root.is_dir() else []
    practice_root = root / PRACTICE_DIR
    practice_files = sorted(practice_root.glob("*.md")) if practice_root.is_dir() else []

    report.stats["articles"] = len(article_files)
    report.stats["courses"] = len(course_files)
    report.stats["practice_transcripts"] = len(practice_files)

    article_numbers: list[int] = []
    for path in article_files:
        match = ARTICLE_FILENAME_PATTERN.match(path.name)
        if match is None:
            report.add("article_filename", relative_path(root, path), "Invalid article filename")
            continue
        number = int(match.group(1))
        article_numbers.append(number)
        content = read_text(path, report)
        frontmatter = parse_frontmatter(content)
        if frontmatter.get("type") not in {"source-reconstruction", "source-synthesis"}:
            report.add("article_type", relative_path(root, path), "Unsupported article type")
        if not frontmatter.get("status"):
            report.add("article_status", relative_path(root, path), "Missing status")
        try:
            frontmatter_number = int(frontmatter.get("article_no", ""))
        except ValueError:
            frontmatter_number = -1
        if frontmatter_number != number:
            report.add(
                "article_frontmatter_number",
                relative_path(root, path),
                f"Filename number {number:02d} does not match article_no",
            )
        validate_marker_pair(
            root,
            path,
            content,
            ARTICLE_LINKS_START,
            ARTICLE_LINKS_END,
            report,
        )

    if article_numbers != list(range(55)):
        report.add(
            "article_numbering",
            ARTICLE_DIR,
            "Expected exactly 00-54 with no gaps or duplicates",
        )

    course_numbers: list[int] = []
    for path in course_files:
        match = COURSE_FILENAME_PATTERN.match(path.name)
        if match is None:
            report.add("course_filename", relative_path(root, path), "Invalid course filename")
            continue
        number = int(match.group(1))
        course_numbers.append(number)
        content = read_text(path, report)
        frontmatter = parse_frontmatter(content)
        if frontmatter.get("type") != "course-transcript":
            report.add("course_type", relative_path(root, path), "Unsupported course type")
        if not frontmatter.get("status"):
            report.add("course_status", relative_path(root, path), "Missing status")
        try:
            frontmatter_number = int(frontmatter.get("course_no", ""))
        except ValueError:
            frontmatter_number = -1
        if frontmatter_number != number:
            report.add(
                "course_frontmatter_number",
                relative_path(root, path),
                f"Filename number {number:02d} does not match course_no",
            )
        if "related_articles" not in frontmatter:
            report.add(
                "course_related_articles",
                relative_path(root, path),
                "Missing related_articles",
            )
        validate_marker_pair(
            root,
            path,
            content,
            COURSE_TRANSCRIPT_START,
            COURSE_TRANSCRIPT_END,
            report,
        )

    if course_numbers != list(range(1, 41)):
        report.add(
            "course_numbering",
            COURSE_DIR,
            "Expected exactly 01-40 with no gaps or duplicates",
        )

    practice_numbers: list[int] = []
    for path in practice_files:
        match = PRACTICE_FILENAME_PATTERN.match(path.name)
        if match is None:
            report.add("practice_filename", relative_path(root, path), "Invalid practice filename")
            continue
        practice_numbers.append(int(match.group(1)))
        frontmatter = parse_frontmatter(read_text(path, report))
        if frontmatter.get("type") != "source":
            report.add(
                "practice_type",
                relative_path(root, path),
                "Practice transcript must remain a source document",
            )
        if frontmatter.get("status") != "review":
            report.add(
                "practice_status",
                relative_path(root, path),
                "Automatic transcript must remain marked for review",
            )

    if practice_numbers != list(range(34)):
        report.add(
            "practice_numbering",
            PRACTICE_DIR,
            "Expected exactly 00-33 with no gaps or duplicates",
        )

    return article_files, course_files, practice_files


def validate_marker_pair(
    root: Path,
    path: Path,
    content: str,
    start_marker: str,
    end_marker: str,
    report: ValidationReport,
) -> None:
    start_count = content.count(start_marker)
    end_count = content.count(end_marker)
    if start_count != 1 or end_count != 1:
        report.add(
            "marker_count",
            relative_path(root, path),
            f"Expected one {start_marker} and one {end_marker}",
        )
        return
    if content.index(start_marker) >= content.index(end_marker):
        report.add(
            "marker_order",
            relative_path(root, path),
            "Start marker must appear before end marker",
        )


def validate_links_and_relations(
    root: Path,
    article_files: list[Path],
    course_files: list[Path],
    report: ValidationReport,
) -> None:
    markdown_files = list_markdown_files(root)
    exact_paths, basename_index, casefold_paths = build_wiki_index(
        root,
        markdown_files,
    )
    article_set = {relative_path(root, path) for path in article_files}
    course_set = {relative_path(root, path) for path in course_files}
    article_to_course: set[tuple[Path, Path]] = set()
    course_to_article: set[tuple[Path, Path]] = set()
    wiki_link_count = 0

    for source in markdown_files:
        content = read_text(source, report)
        source_relative = relative_path(root, source)
        for target in extract_wiki_targets(content):
            wiki_link_count += 1
            resolution = resolve_wiki_target(
                root,
                source,
                target,
                exact_paths,
                basename_index,
                casefold_paths,
            )
            if resolution.status == "missing":
                # log.md 是只追加的维护日志，按 AGENTS.md 协议
                # 「历史来源路径不参与断链检查」：指向已删除历史文件的
                # 旧链接（如 2026-08-26 合并掉的旧索引）不报错。
                if source_relative == Path("log.md"):
                    continue
                report.add(
                    "broken_wiki_link",
                    source_relative,
                    f"Missing Wiki target: {target}",
                )
                continue
            if resolution.status == "ambiguous":
                report.add(
                    "ambiguous_wiki_link",
                    source_relative,
                    f"Ambiguous Wiki target: {target}",
                )
                continue
            if resolution.status == "case_mismatch":
                report.add(
                    "wiki_link_case",
                    source_relative,
                    f"Wiki target has incorrect case: {target}",
                )
            if resolution.path is None:
                continue
            if source_relative in article_set and resolution.path in course_set:
                article_to_course.add((source_relative, resolution.path))
            if source_relative in course_set and resolution.path in article_set:
                course_to_article.add((resolution.path, source_relative))

        if source_relative in article_set or source_relative in course_set:
            continue
        validate_markdown_links(
            root,
            source,
            strip_fenced_code(content),
            report,
        )

    for article, course in sorted(article_to_course - course_to_article):
        report.add(
            "missing_course_backlink",
            course,
            f"Missing backlink to {article.as_posix()}",
        )
    for article, course in sorted(course_to_article - article_to_course):
        report.add(
            "missing_article_backlink",
            article,
            f"Missing backlink to {course.as_posix()}",
        )

    report.stats["wiki_links"] = wiki_link_count
    report.stats["cross_relations"] = len(article_to_course & course_to_article)


def validate_markdown_links(
    root: Path,
    source: Path,
    content: str,
    report: ValidationReport,
) -> None:
    for match in MARKDOWN_LINK_PATTERN.finditer(content):
        target = match.group(1).strip().strip("<>")
        target = target.split("#", 1)[0].strip()
        if not target or re.match(r"^(?:https?://|mailto:)", target):
            continue
        target = unquote(target)
        if Path(target).suffix.lower() != ".md":
            continue
        candidate = (source.parent / Path(*target.replace("\\", "/").split("/"))).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            report.add(
                "markdown_link_escape",
                relative_path(root, source),
                f"Link escapes the vault: {target}",
            )
            continue
        if not candidate.exists():
            report.add(
                "broken_markdown_link",
                relative_path(root, source),
                f"Missing Markdown target: {target}",
            )


def protected_content(relative: Path, content: str) -> str:
    if relative.is_relative_to(ARTICLE_DIR):
        return content.split(ARTICLE_LINKS_START, 1)[0]
    if relative.is_relative_to(COURSE_DIR):
        start = content.find(COURSE_TRANSCRIPT_START)
        end = content.find(COURSE_TRANSCRIPT_END)
        if start == -1 or end == -1 or start >= end:
            return content
        start += len(COURSE_TRANSCRIPT_START)
        return content[start:end]
    if relative.is_relative_to(PRACTICE_DIR):
        return content
    raise ValueError(f"Not a corpus file: {relative.as_posix()}")


def compute_corpus_hashes(root: Path) -> dict[str, str]:
    corpus_files = sorted((root / ARTICLE_DIR).glob("*.md")) + sorted(
        (root / COURSE_DIR).glob("*.md")
    ) + sorted((root / PRACTICE_DIR).glob("*.md"))
    hashes: dict[str, str] = {}
    for path in corpus_files:
        relative = relative_path(root, path)
        content = path.read_text(encoding="utf-8")
        protected = protected_content(relative, content)
        hashes[relative.as_posix()] = hashlib.sha256(
            protected.encode("utf-8")
        ).hexdigest()
    return hashes


def write_corpus_lock(root: Path) -> Path:
    lock_path = LOCK_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 2,
        "algorithm": "sha256",
        "scope": {
            "articles": f"content before {ARTICLE_LINKS_START}",
            "courses": (
                f"content between {COURSE_TRANSCRIPT_START} "
                f"and {COURSE_TRANSCRIPT_END}"
            ),
            "practice": "full automatic transcript files under 炒股实操/课程转写",
        },
        "files": compute_corpus_hashes(root),
    }
    lock_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return lock_path


def validate_corpus_lock(root: Path, report: ValidationReport) -> None:
    lock_path = LOCK_PATH
    if not lock_path.is_file():
        report.add("corpus_lock_missing", LOCK_PATH, "Corpus lock file is missing")
        return
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        report.add("corpus_lock_invalid", LOCK_PATH, str(error))
        return

    expected = payload.get("files")
    if not isinstance(expected, dict):
        report.add("corpus_lock_invalid", LOCK_PATH, "Missing files mapping")
        return

    actual = compute_corpus_hashes(root)
    report.stats["locked_files"] = len(actual)
    expected_paths = set(expected)
    actual_paths = set(actual)
    if expected_paths != actual_paths:
        report.add(
            "corpus_lock_file_set",
            LOCK_PATH,
            "Locked corpus file set does not match the current 55+40+34 corpus",
        )

    for path in sorted(expected_paths & actual_paths):
        if expected[path] != actual[path]:
            report.add(
                "corpus_lock_mismatch",
                Path(path),
                "Protected text changed; explicit authorization and lock refresh required",
            )


def tracked_files(root: Path) -> list[Path]:
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            cwd=root,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return sorted(
            path
            for path in root.rglob("*")
            if path.is_file()
            and ".git" not in path.parts
            and ".obsidian" not in path.parts
            and not path.name.startswith(".env")
        )

    paths = result.stdout.decode("utf-8").split("\0")
    return [root / Path(path) for path in paths if path]


def validate_sensitive_files(root: Path, report: ValidationReport) -> None:
    files = tracked_files(root)
    report.stats["tracked_files"] = len(files)
    for path in files:
        relative = relative_path(root, path)
        if path.name in {".env", ".github.env"} or (
            path.name.startswith(".env.") and path.name != ".env.example"
        ):
            report.add(
                "sensitive_file_tracked",
                relative,
                "Sensitive environment file is tracked",
            )
            continue
        try:
            if path.stat().st_size > 5_000_000:
                continue
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            report.add(
                "secret_pattern",
                relative,
                "Possible credential pattern found; secret value suppressed",
            )


def validate_vault(root: Path) -> ValidationReport:
    root = root.resolve()
    report = ValidationReport()
    validate_required_files(root, report)
    article_files, course_files, _practice_files = validate_numbered_corpus(root, report)
    validate_links_and_relations(root, article_files, course_files, report)
    validate_corpus_lock(root, report)
    validate_sensitive_files(root, report)
    return report


def print_report(report: ValidationReport) -> None:
    summary = ", ".join(
        f"{key}={value}" for key, value in sorted(report.stats.items())
    )
    if report.ok:
        print(f"PASS: second-brain validation succeeded ({summary})")
        return
    print(report.format_errors(), file=sys.stderr)
    print(f"FAIL: second-brain validation failed ({summary})", file=sys.stderr)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the canonical 55+40 corpus and 34 practice transcripts.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Vault root directory (default: current directory)",
    )
    parser.add_argument(
        "--write-lock",
        action="store_true",
        help="Refresh protected corpus hashes after an authorized text change",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    if args.write_lock:
        lock_path = write_corpus_lock(root)
        print(f"WROTE: {lock_path.as_posix()}")
    report = validate_vault(root)
    print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
