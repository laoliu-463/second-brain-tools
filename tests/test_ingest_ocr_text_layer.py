from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

import scripts.ingest_ocr_text_layer as ingest
from scripts.ingest_ocr_text_layer import (
    BookDocument,
    BookSpec,
    IngestError,
    PreparedBook,
    build_book_document,
    choose_hybrid_text,
    clean_ocr_text,
    discover_page_files,
    parse_markdown_pages,
    update_index_text,
    update_manifest_bytes,
    validate_progress_db,
    write_files_atomically,
)


def write_single_page_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as handle:
        writer.write(handle)


def fixture_spec() -> BookSpec:
    return BookSpec(
        book_id="fixture",
        title="Fixture Book",
        ocr_prefix="fixture_book_",
        expected_pages=2,
        source_relative=Path("资料库/原始资料/fixture.pdf"),
        output_relative=Path("资料库/文字层/fixture.md"),
        source_sha256="A" * 64,
        strategy="ocr",
        status="extracted_needs_review",
        confidence="medium",
        quality_note="公式和图表需要对照原始页。",
        watermark="see more please visit: https://example.com",
    )


class IngestOcrTextLayerTests(unittest.TestCase):
    def test_clean_ocr_text_removes_exact_watermark_and_blank_runs(self) -> None:
        text = (
            "正文第一行\n"
            "see more please visit: https://example.com\n\n\n"
            "正文第二行  \n"
        )

        cleaned = clean_ocr_text(
            text,
            watermark="see more please visit: https://example.com",
        )

        self.assertEqual(cleaned, "正文第一行\n\n正文第二行")

    def test_choose_hybrid_text_only_replaces_short_native_page(self) -> None:
        native = "原生文本" * 20
        ocr = "OCR 文本" * 30

        kept, used_ocr = choose_hybrid_text(native, ocr, threshold=50)
        replaced, replaced_with_ocr = choose_hybrid_text(
            "短文本", ocr, threshold=50
        )

        self.assertEqual(kept, native)
        self.assertFalse(used_ocr)
        self.assertEqual(replaced, ocr)
        self.assertTrue(replaced_with_ocr)

    def test_discover_page_files_rejects_a_missing_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_single_page_pdf(root / "fixture_book_page001.pdf")
            write_single_page_pdf(root / "fixture_book_page003.pdf")

            with self.assertRaisesRegex(IngestError, "missing pages: 2"):
                discover_page_files(
                    root,
                    prefix="fixture_book_",
                    expected_pages=3,
                )

    def test_parse_markdown_pages_treats_old_placeholder_as_empty(self) -> None:
        markdown = (
            "# Fixture\n\n"
            "## Page 1\n\n"
            "[本页未提取到文本，可能需要 OCR。]\n\n"
            "## Page 2\n\n"
            "有效原生文本\n"
        )

        pages = parse_markdown_pages(markdown)

        self.assertEqual(pages, {1: "", 2: "有效原生文本"})

    def test_build_book_document_records_review_pages_and_source_link(self) -> None:
        spec = fixture_spec()
        document = build_book_document(
            spec,
            ocr_pages={1: "足够长的正文" * 10, 2: "短页"},
            native_pages={},
            historical_source_file=r"D:\Docs\fixture.pdf",
            ocr_dir=Path(r"D:\ocr-output"),
            extraction_date="2026-08-25",
        )

        self.assertEqual(document.text_pages, 2)
        self.assertEqual(document.review_pages, (2,))
        self.assertIn("status: extracted_needs_review", document.content)
        self.assertIn("review_pages: [2]", document.content)
        self.assertIn("资料库/原始资料/fixture.pdf", document.content)
        self.assertIn("本页 OCR 文本较少", document.content)

    def test_build_book_document_applies_reviewed_page_classifications(self) -> None:
        spec = replace(
            fixture_spec(),
            expected_pages=3,
            manual_text_overrides={1: "人工核验后的标题"},
            verified_blank_pages=(2,),
            verified_short_pages=(3,),
            visual_review_date="2026-08-25",
        )

        document = build_book_document(
            spec,
            ocr_pages={1: "错误标题", 2: "背页透印噪声", 3: "索引"},
            native_pages={},
            historical_source_file=r"D:\Docs\fixture.pdf",
            ocr_dir=Path(r"D:\ocr-output"),
            extraction_date="2026-08-26",
        )

        self.assertEqual(document.review_pages, ())
        self.assertEqual(document.manual_override_pages, (1,))
        self.assertEqual(document.verified_blank_pages, (2,))
        self.assertEqual(document.verified_short_pages, (3,))
        self.assertEqual(document.text_pages, 2)
        self.assertEqual(document.text_chars, len("人工核验后的标题索引"))
        self.assertIn("manual_override_pages: [1]", document.content)
        self.assertIn("verified_blank_pages: [2]", document.content)
        self.assertIn("verified_short_pages: [3]", document.content)
        self.assertIn("visual_reviewed: 2026-08-25", document.content)
        self.assertIn("人工核验后的标题", document.content)
        self.assertNotIn("错误标题", document.content)
        self.assertNotIn("背页透印噪声", document.content)
        self.assertIn("经原始 PDF 视觉核验", document.content)
        self.assertNotIn("本页 OCR 文本较少", document.content)
        self.assertEqual(parse_markdown_pages(document.content)[2], "")

    def test_manual_override_is_not_reported_as_hybrid_ocr_fallback(self) -> None:
        spec = replace(
            fixture_spec(),
            strategy="hybrid",
            manual_text_overrides={1: "人工核验标题"},
        )

        document = build_book_document(
            spec,
            ocr_pages={1: "很长的错误 OCR" * 20, 2: "另一页 OCR" * 20},
            native_pages={1: "", 2: "足够长的原生文本" * 20},
            historical_source_file=r"D:\Docs\fixture.pdf",
            ocr_dir=Path(r"D:\ocr-output"),
            extraction_date="2026-08-26",
        )

        self.assertEqual(document.ocr_fallback_pages, ())
        self.assertEqual(document.review_pages, ())
        self.assertIn("人工核验标题", document.content)
        self.assertNotIn("很长的错误 OCR", document.content)

    def test_review_classifications_reject_invalid_page_and_empty_override(self) -> None:
        cases = (
            ({"verified_blank_pages": (3,)}, "out of range"),
            ({"manual_text_overrides": {1: "   "}}, "overrides are empty"),
        )
        for changes, message in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(IngestError, message):
                    build_book_document(
                        replace(fixture_spec(), **changes),
                        ocr_pages={1: "OCR", 2: "正文" * 30},
                        native_pages={},
                        historical_source_file=r"D:\Docs\fixture.pdf",
                        ocr_dir=Path(r"D:\ocr-output"),
                        extraction_date="2026-08-26",
                    )

    def test_build_book_document_rejects_overlapping_review_classifications(self) -> None:
        spec = replace(
            fixture_spec(),
            manual_text_overrides={1: "人工标题"},
            verified_blank_pages=(1,),
        )

        with self.assertRaisesRegex(IngestError, "overlap"):
            build_book_document(
                spec,
                ocr_pages={1: "OCR", 2: "正文" * 30},
                native_pages={},
                historical_source_file=r"D:\Docs\fixture.pdf",
                ocr_dir=Path(r"D:\ocr-output"),
                extraction_date="2026-08-26",
            )

    def test_build_book_document_rejects_an_empty_verified_short_page(self) -> None:
        spec = replace(fixture_spec(), verified_short_pages=(1,))

        with self.assertRaisesRegex(IngestError, "verified short page 1 is empty"):
            build_book_document(
                spec,
                ocr_pages={1: "", 2: "正文" * 30},
                native_pages={},
                historical_source_file=r"D:\Docs\fixture.pdf",
                ocr_dir=Path(r"D:\ocr-output"),
                extraction_date="2026-08-26",
            )

    def test_update_manifest_changes_only_matching_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.csv"
            path.write_text(
                "Hash,Source,Output,status,confidence,source_format,pages,text_pages,text_chars,Errors\n"
                f"{'A' * 64},source.pdf,output.md,needs_ocr,low,pdf,2,0,0,old\n"
                f"{'B' * 64},other.pdf,other.md,extracted,high,pdf,1,1,100,\n",
                encoding="utf-8-sig",
            )
            document = BookDocument(
                content="",
                text_pages=2,
                text_chars=123,
                review_pages=(2,),
                ocr_fallback_pages=(),
                manual_override_pages=(),
                verified_blank_pages=(),
                verified_short_pages=(),
            )

            payload, counts = update_manifest_bytes(
                path,
                {"A" * 64: (fixture_spec(), document)},
            )
            decoded = payload.decode("utf-8-sig").splitlines()
            rows = list(csv.DictReader(decoded))

            self.assertEqual(rows[0]["status"], "extracted_needs_review")
            self.assertEqual(rows[0]["confidence"], "medium")
            self.assertEqual(rows[0]["text_chars"], "123")
            self.assertEqual(rows[0]["Errors"], "")
            self.assertEqual(rows[1]["status"], "extracted")
            self.assertEqual(counts["extracted_needs_review"], 1)

    def test_update_manifest_preserves_an_existing_utf8_file_without_bom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.csv"
            path.write_text(
                "Hash,Source,Output,status,confidence,source_format,pages,text_pages,text_chars,Errors\r\n"
                f"{'A' * 64},source.pdf,output.md,needs_ocr,low,pdf,2,0,0,old\r\n",
                encoding="utf-8",
            )
            document = BookDocument(
                content="",
                text_pages=2,
                text_chars=123,
                review_pages=(),
                ocr_fallback_pages=(),
            )

            payload, _ = update_manifest_bytes(
                path,
                {"A" * 64: (fixture_spec(), document)},
            )

            self.assertFalse(payload.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"\r\n", payload)

    def test_update_manifest_returns_original_bytes_when_target_row_is_current(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.csv"
            original = (
                "Hash,Source,Output,status,confidence,source_format,pages,text_pages,text_chars,Errors\n"
                f"{'A' * 64},source.pdf,output.md,extracted_needs_review,medium,pdf,2,2,123,\n"
            ).encode("utf-8")
            path.write_bytes(original)
            document = BookDocument(
                content="",
                text_pages=2,
                text_chars=123,
                review_pages=(),
                ocr_fallback_pages=(),
            )

            payload, _ = update_manifest_bytes(
                path,
                {"A" * 64: (fixture_spec(), document)},
            )

            self.assertEqual(payload, original)

    def test_update_index_recomputes_summary_and_queue_counts(self) -> None:
        original = "\n".join(
            [
                "- 可直接检索：205 份，状态为 `extracted`。",
                "- 需要复核：2 份，状态为 `extracted_needs_review`。",
                "- 需要 OCR：89 份，状态为 `needs_ocr`。",
                "- 中文 OCR 试验使用本地 Tesseract.js 处理单页，识别噪声较大，因此暂停批量 OCR，等待更可靠的中文 OCR 引擎或人工复核流程。",
                "| 中文 OCR | 89 | old |",
                "| 文本质量复核 | 2 | old |",
            ]
        )

        updated = update_index_text(
            original,
            {
                "extracted": 203,
                "extracted_needs_review": 6,
                "needs_ocr": 87,
            },
        )

        self.assertIn("可直接检索：203 份", updated)
        self.assertIn("需要复核：6 份", updated)
        self.assertIn("| 中文 OCR | 87 | old |", updated)
        self.assertIn("| 文本质量复核 | 6 | old |", updated)
        self.assertIn("5 本重点书籍的 OCR 接入", updated)

        rerun = update_index_text(
            updated,
            {
                "extracted": 203,
                "extracted_needs_review": 6,
                "needs_ocr": 87,
            },
        )
        self.assertEqual(rerun, updated)

    def test_validate_progress_db_rejects_incomplete_book(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "progress.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "CREATE TABLE progress (book_id TEXT, status TEXT)"
                )
                connection.execute(
                    "INSERT INTO progress VALUES ('fixture', 'done')"
                )
                connection.commit()
            finally:
                connection.close()
            prepared = PreparedBook(
                spec=fixture_spec(),
                source_path=root / "source.pdf",
                output_path=root / "output.md",
                historical_source_file="source.pdf",
                native_pages={},
            )

            with self.assertRaisesRegex(IngestError, "not complete"):
                validate_progress_db(db_path, [prepared])

    def test_write_files_atomically_updates_all_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_bytes(b"old")

            write_files_atomically({first: b"new", second: b"created"})

            self.assertEqual(first.read_bytes(), b"new")
            self.assertEqual(second.read_bytes(), b"created")
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_load_target_books_prepares_all_governed_sources(self) -> None:
        class FakeBook:
            def __init__(self, book_id: str, source: Path) -> None:
                self.id = book_id
                self.title = f"Title {book_id}"
                self.source = str(source)
                self.pages = 2

            def output_pdf_for(self, page: int, output_dir: Path) -> Path:
                return output_dir / f"{self.id}_page{page:03d}.pdf"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "ocr-output"
            books = []
            for book_id in ingest.TARGET_BOOK_IDS:
                source = (
                    root
                    / "资料库"
                    / "原始资料"
                    / "书籍与讲义"
                    / f"{book_id}.pdf"
                )
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"source")
                output = ingest._output_relative_for_source(
                    source.relative_to(root)
                )
                output_path = root / output
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    "---\n"
                    f"source_sha256: {'A' * 64}\n"
                    "pages: 2\n"
                    "source_file: historical.pdf\n"
                    "---\n\n"
                    "## Page 1\n\n原生文字\n\n"
                    "## Page 2\n\n原生文字\n",
                    encoding="utf-8",
                )
                books.append(FakeBook(book_id, source))

            manifest = type(
                "FakeManifest",
                (),
                {
                    "books": books,
                    "output_dir": output_dir,
                    "db_path": root / "progress.db",
                },
            )()
            with (
                patch.object(ingest, "load_manifest", return_value=manifest),
                patch.object(ingest, "sha256_file", return_value="A" * 64),
            ):
                prepared, actual_output_dir, db_path = ingest.load_target_books(
                    root,
                    root / "books.yaml",
                )

            self.assertEqual([book.spec.book_id for book in prepared], list(ingest.TARGET_BOOK_IDS))
            self.assertEqual(prepared[0].spec.title, f"Title {ingest.TARGET_BOOK_IDS[0]}")
            self.assertEqual(actual_output_dir, output_dir.resolve())
            self.assertEqual(db_path, (root / "progress.db").resolve())
            mankiw = next(
                book
                for book in prepared
                if book.spec.book_id == "econ_mankiw_宏观经济学第十版"
            )
            self.assertEqual(mankiw.native_pages, {1: "原生文字", 2: "原生文字"})

    def test_main_supports_dry_run_and_apply_with_atomic_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_path = root / "资料库" / "文字层" / "fixture.md"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("old", encoding="utf-8")
            manifest_path = root / "资料库" / "文字化清单.csv"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_bytes(b"old manifest")
            index_path = root / "资料库" / "文字化索引.md"
            index_path.write_text("old index", encoding="utf-8")

            prepared = PreparedBook(
                spec=fixture_spec(),
                source_path=root / "source.pdf",
                output_path=output_path,
                historical_source_file="source.pdf",
                native_pages={},
            )
            document = BookDocument(
                content="new document\n",
                text_pages=2,
                text_chars=12,
                review_pages=(),
                ocr_fallback_pages=(),
            )
            documents = {"A" * 64: (prepared.spec, document)}
            common_patches = (
                patch.object(
                    ingest,
                    "load_target_books",
                    return_value=([prepared], root / "ocr", root / "progress.db"),
                ),
                patch.object(ingest, "validate_progress_db"),
                patch.object(ingest, "compile_documents", return_value=documents),
                patch.object(
                    ingest,
                    "update_manifest_bytes",
                    return_value=(b"new manifest", Counter({"extracted": 1})),
                ),
                patch.object(ingest, "update_index_text", return_value="new index\n"),
            )

            with common_patches[0], common_patches[1], common_patches[2], common_patches[3], common_patches[4]:
                result = ingest.main(
                    [
                        "--vault-root",
                        str(root),
                        "--manifest",
                        str(root / "books.yaml"),
                        "--date",
                        "2026-08-26",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "old")

            with (
                patch.object(
                    ingest,
                    "load_target_books",
                    return_value=([prepared], root / "ocr", root / "progress.db"),
                ),
                patch.object(ingest, "validate_progress_db"),
                patch.object(ingest, "compile_documents", return_value=documents),
                patch.object(
                    ingest,
                    "update_manifest_bytes",
                    return_value=(b"new manifest", Counter({"extracted": 1})),
                ),
                patch.object(ingest, "update_index_text", return_value="new index\n"),
            ):
                result = ingest.main(
                    [
                        "--vault-root",
                        str(root),
                        "--manifest",
                        str(root / "books.yaml"),
                        "--date",
                        "2026-08-26",
                        "--apply",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "new document\n")
            self.assertEqual(manifest_path.read_bytes(), b"new manifest")
            self.assertEqual(index_path.read_text(encoding="utf-8"), "new index\n")


if __name__ == "__main__":
    unittest.main()
