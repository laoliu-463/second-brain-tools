from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts.build_retrieval_index import (
    build_index,
    chunk_markdown,
    discover_documents,
    estimate_token_count,
    main,
    query_index,
)


class RetrievalIndexTests(unittest.TestCase):
    def test_discovery_uses_explicit_governed_collections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            included = (
                Path("人智55篇/正文/00-a.md"),
                Path("大小课/课程正文/01-b.md"),
                Path("炒股实操/课程转写/00-c.md"),
                Path("炒股实操/整理/主题.md"),
                Path("资料库/文字层/书籍与讲义/book.md"),
                Path("资料库/专题整理/topic.md"),
            )
            excluded = (
                Path("炒股实操/整理/AGENT.md"),
                Path("资料库/文字化索引.md"),
                Path("log.md"),
            )
            for relative in included + excluded:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"# {relative.stem}\n\n正文", encoding="utf-8")

            documents = discover_documents(root)

            actual = {document.relative_path for document in documents}
            self.assertEqual(actual, set(included))
            self.assertNotIn(Path("炒股实操/整理/AGENT.md"), actual)

    def test_chunking_preserves_page_boundaries_and_respects_token_limit(self) -> None:
        markdown = (
            "---\ntype: source-transcript\nconfidence: medium\n---\n\n"
            "# 测试书\n\n"
            "## Page 1\n\n"
            "# OCR 误识别的井号不是书名\n"
            + "剩余价值是分析资本关系的重要概念。" * 100
            + "\n\n## Page 2\n\n第二页短文。\n"
        )

        chunks = chunk_markdown(Path("资料库/文字层/书籍与讲义/book.md"), markdown)

        self.assertGreater(len(chunks), 2)
        self.assertEqual({chunk.page for chunk in chunks}, {1, 2})
        self.assertTrue(all(estimate_token_count(chunk.content) <= 480 for chunk in chunks))
        self.assertTrue(all("confidence:" not in chunk.content for chunk in chunks))
        self.assertTrue(all(chunk.title == "测试书" for chunk in chunks))
        self.assertTrue(any("# OCR 误识别的井号不是书名" in chunk.content for chunk in chunks))

    def test_build_and_query_returns_ranked_source_with_stable_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "人智55篇" / "正文" / "00-价值.md"
            second = root / "资料库" / "专题整理" / "技术.md"
            for path in (first, second):
                path.parent.mkdir(parents=True, exist_ok=True)
            first.write_text(
                "# 剩余价值\n\n剩余价值来自雇佣劳动创造的新价值。",
                encoding="utf-8",
            )
            second.write_text(
                "# 技术说明\n\n这里讨论数据库索引和检索。",
                encoding="utf-8",
            )
            dictionary = root / "retrieval_terms.txt"
            dictionary.write_text("剩余价值 1000 nz\n", encoding="utf-8")
            db_path = root / ".ecc" / "retrieval" / "knowledge.db"

            stats = build_index(root, db_path, dictionary)
            results = query_index(db_path, "剩余价值", limit=5, dictionary_path=dictionary)

            self.assertEqual(stats.documents, 2)
            self.assertGreaterEqual(stats.chunks, 2)
            self.assertTrue(db_path.is_file())
            self.assertGreaterEqual(len(results), 1)
            self.assertEqual(results[0].path, Path("人智55篇/正文/00-价值.md"))
            self.assertEqual(results[0].title, "剩余价值")
            self.assertIn("剩余价值", results[0].content)
            self.assertTrue(results[0].chunk_id)

    def test_query_ranks_articles_and_courses_before_other_collections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            article = root / "人智55篇" / "正文" / "00-需求.md"
            course = root / "大小课" / "课程正文" / "01-需求.md"
            topic = root / "资料库" / "专题整理" / "需求专题.md"
            for path in (article, course, topic):
                path.parent.mkdir(parents=True, exist_ok=True)
            article.write_text("# 需求\n\n先看真实需求再谈流量。", encoding="utf-8")
            course.write_text("# 需求课\n\n先看真实需求再谈流量。", encoding="utf-8")
            topic.write_text(
                "# 需求专题\n\n" + ("先看真实需求再谈流量。\n" * 20),
                encoding="utf-8",
            )
            dictionary = root / "retrieval_terms.txt"
            dictionary.write_text("需求 1000 nz\n", encoding="utf-8")
            db_path = root / ".ecc" / "retrieval" / "knowledge.db"

            build_index(root, db_path, dictionary)
            results = query_index(db_path, "需求", limit=3, dictionary_path=dictionary)

            self.assertGreaterEqual(len(results), 2)
            self.assertEqual(
                {result.source_kind for result in results[:2]},
                {"article", "course"},
            )
            self.assertNotEqual(results[0].source_kind, "topic_synthesis")

    def test_rebuild_removes_documents_that_left_the_governed_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "资料库" / "专题整理" / "临时主题.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# 临时主题\n\n独有检索词阿尔法。", encoding="utf-8")
            dictionary = root / "retrieval_terms.txt"
            dictionary.write_text("独有检索词 1000 nz\n", encoding="utf-8")
            db_path = root / ".ecc" / "retrieval" / "knowledge.db"
            build_index(root, db_path, dictionary)
            self.assertTrue(query_index(db_path, "独有检索词", dictionary_path=dictionary))

            path.unlink()
            stats = build_index(root, db_path, dictionary)

            self.assertEqual(stats.documents, 0)
            self.assertEqual(stats.chunks, 0)
            self.assertEqual(
                query_index(db_path, "独有检索词", dictionary_path=dictionary),
                [],
            )

    def test_cli_builds_and_returns_json_query_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "资料库" / "专题整理" / "货币专题.md"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("# 货币专题\n\n货币政策影响通货膨胀。", encoding="utf-8")
            dictionary = root / "terms.txt"
            dictionary.write_text("货币政策 1000 nz\n", encoding="utf-8")
            db_path = root / "index.db"

            build_output = io.StringIO()
            with redirect_stdout(build_output):
                build_result = main(
                    [
                        "build",
                        "--root",
                        str(root),
                        "--db",
                        str(db_path),
                        "--dictionary",
                        str(dictionary),
                    ]
                )
            query_output = io.StringIO()
            with redirect_stdout(query_output):
                query_result = main(
                    [
                        "query",
                        "货币政策",
                        "--root",
                        str(root),
                        "--db",
                        str(db_path),
                        "--dictionary",
                        str(dictionary),
                        "--limit",
                        "3",
                        "--json",
                    ]
                )

            self.assertEqual(build_result, 0)
            self.assertIn("indexed documents=1", build_output.getvalue())
            self.assertEqual(query_result, 0)
            payload = json.loads(query_output.getvalue())
            self.assertEqual(payload[0]["title"], "货币专题")
            self.assertEqual(payload[0]["path"], "资料库/专题整理/货币专题.md")


if __name__ == "__main__":
    unittest.main()
