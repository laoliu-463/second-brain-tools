from __future__ import annotations

from pathlib import Path
import unittest

from scripts.validate_vault import (
    strip_fenced_code,
    validate_vault,
    write_corpus_lock,
)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def article_text(number: int, course_link: str = "") -> str:
    return f"""---
type: source-reconstruction
status: verified-boundary
article_no: {number}
---

# {number:02d}｜Article

## 正文

Protected article text {number}.

<!-- COURSE-LINKS-START -->
{course_link}
<!-- COURSE-LINKS-END -->
"""


def course_text(number: int, article_link: str = "") -> str:
    return f"""---
type: course-transcript
status: source-derived
course_no: {number:02d}
related_articles: []
---

# {number:02d}｜Course

{article_link}

<!-- COURSE-TRANSCRIPT-START -->
Protected course text {number}.
<!-- COURSE-TRANSCRIPT-END -->
"""


def practice_text(number: int) -> str:
    return f"""---
type: source
status: review
---

# {number:02d}｜Practice

Automatic transcript {number}.
"""


def build_valid_vault(root: Path) -> None:
    write_text(root / "AGENTS.md", "# Rules\n")
    write_text(root / "log.md", "# Log\n")
    write_text(root / "具体政策与权威数据历史台账.md", "# Ledger\n")
    write_text(root / "README.md", "[Article index](./人智55篇/目录.md)\n")
    write_text(root / "人智55篇" / "目录.md", "# Articles\n")
    write_text(root / "人智55篇" / "大小课对应表.md", "# Relations\n")
    write_text(root / "大小课" / "目录.md", "# Courses\n")
    write_text(root / "大小课" / "联动检查报告.md", "# Report\n")

    for number in range(55):
        link = ""
        if number == 0:
            link = "[[大小课/课程正文/01-course|Course 01]]"
        write_text(
            root / "人智55篇" / "正文" / f"{number:02d}-article.md",
            article_text(number, link),
        )

    for number in range(1, 41):
        link = ""
        if number == 1:
            link = "[[人智55篇/正文/00-article|Article 00]]"
        write_text(
            root / "大小课" / "课程正文" / f"{number:02d}-course.md",
            course_text(number, link),
        )

    for number in range(34):
        write_text(
            root / "炒股实操" / "课程转写" / f"{number:02d}-practice.md",
            practice_text(number),
        )

    write_text(root / "炒股实操" / "来源清单.md", "# Sources\n")
    for name in (
        "AGENT.md",
        "index.md",
        "00-用例与命令速查.md",
        "01-课程逐讲导航.md",
        "02-股票基础术语速查.md",
        "03-开户券商与交易软件操作指南.md",
        "04-交易系统与风险控制框架.md",
        "05-课程主张待核查清单.md",
    ):
        write_text(root / "炒股实操" / "整理" / name, f"# {name}\n")

    write_corpus_lock(root)


class VaultValidatorTests(unittest.TestCase):
    def test_valid_vault_passes(self) -> None:
        with self.subTest("valid fixture"):
            from tempfile import TemporaryDirectory

            with TemporaryDirectory() as directory:
                root = Path(directory)
                build_valid_vault(root)

                report = validate_vault(root)

                self.assertTrue(report.ok, report.format_errors())
                self.assertEqual(report.stats["articles"], 55)
                self.assertEqual(report.stats["courses"], 40)
                self.assertEqual(report.stats["practice_transcripts"], 34)

    def test_broken_wiki_link_fails(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            build_valid_vault(root)
            write_text(root / "README.md", "[[missing-page]]\n")

            report = validate_vault(root)

            self.assertIn("broken_wiki_link", report.error_codes)

    def test_log_historical_wiki_links_are_exempt(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            build_valid_vault(root)
            # log.md 里指向已删除历史文件（如 2026-08-26 合并掉的旧索引）的链接：
            # 按 AGENTS.md 协议「历史来源路径不参与断链检查」，应被豁免，不报 broken_wiki_link。
            write_text(
                root / "log.md",
                "# Log\n\n## [2026-08-26] 合并 | 索引合并\n"
                "指向已删除旧索引：[[资料库/文字化索引]] [[资料库/原始资料分类]]\n",
            )

            report = validate_vault(root)

            self.assertTrue(report.ok, report.format_errors())
            self.assertNotIn("broken_wiki_link", report.error_codes)

    def test_local_derived_markdown_is_not_validated_as_vault_content(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            build_valid_vault(root)
            write_text(
                root / "资料库" / "文字层" / "book.md",
                "---\ntype: source-transcript\n---\n\n[[code example]]\n",
            )
            for private in (root / ".ecc" / "audit" / "before.md",
                            root / "资料库" / ".ecc" / "backup" / "book.md"):
                write_text(private, "[[private snapshot, not a published link]]\n")

            report = validate_vault(root)

            self.assertTrue(report.ok, report.format_errors())

    def test_one_way_cross_relation_fails(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            build_valid_vault(root)
            course = root / "大小课" / "课程正文" / "01-course.md"
            write_text(course, course_text(1))

            report = validate_vault(root)

            self.assertIn("missing_course_backlink", report.error_codes)

    def test_protected_article_change_fails(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            build_valid_vault(root)
            article = root / "人智55篇" / "正文" / "00-article.md"
            changed = article.read_text(encoding="utf-8").replace(
                "Protected article text 0.",
                "Changed protected article text.",
            )
            write_text(article, changed)

            report = validate_vault(root)

            self.assertIn("corpus_lock_mismatch", report.error_codes)

    def test_numbering_gap_fails(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            build_valid_vault(root)
            (root / "人智55篇" / "正文" / "54-article.md").unlink()

            report = validate_vault(root)

            self.assertIn("article_numbering", report.error_codes)

    def test_practice_numbering_gap_fails(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            build_valid_vault(root)
            (root / "炒股实操" / "课程转写" / "33-practice.md").unlink()

            report = validate_vault(root)

            self.assertIn("practice_numbering", report.error_codes)

    def test_wiki_link_inside_code_fence_is_ignored(self) -> None:
        content = "before\n```markdown\n[[placeholder]]\n```\nafter\n"

        self.assertNotIn("[[placeholder]]", strip_fenced_code(content))

    def test_historical_non_markdown_link_is_ignored(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            build_valid_vault(root)
            write_text(
                root / "大小课" / "目录.md",
                "[Deleted source](legacy-source.docx)\n",
            )

            report = validate_vault(root)

            self.assertTrue(report.ok, report.format_errors())

    def test_tracked_secret_pattern_fails_without_echoing_secret(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            build_valid_vault(root)
            secret = "ghp_" + "A" * 40
            write_text(root / "README.md", f"credential={secret}\n")

            report = validate_vault(root)

            self.assertIn("secret_pattern", report.error_codes)
            self.assertNotIn(secret, report.format_errors())


if __name__ == "__main__":
    unittest.main()
