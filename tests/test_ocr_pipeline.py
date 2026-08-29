from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config" / "ocr"))

from ocr_pipeline.manifest import BookEntry
from ocr_pipeline.ocr import OcrResult, OcrRunner
from ocr_pipeline.state import MemoryMonitor, PipelineState
from ocr_pipeline.worker import PageWorker


def write_pdf(path: Path, pages: int) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as handle:
        writer.write(handle)


def book(source: Path, pages: int = 1) -> BookEntry:
    return BookEntry(
        id="fixture",
        title="Fixture",
        source=str(source),
        mode="redo",
        pages=pages,
    )


class OcrPipelineTests(unittest.TestCase):
    def test_process_page_sends_only_requested_page_to_ocrmypdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            output = root / "output.pdf"
            write_pdf(source, pages=3)
            observed: dict[str, int | str] = {}

            def fake_ocrmypdf(command, **kwargs):
                observed["input_pages"] = len(
                    PdfReader(command[-2], strict=False).pages
                )
                pages_index = command.index("--pages")
                observed["requested_page"] = command[pages_index + 1]
                write_pdf(Path(command[-1]), pages=1)
                return subprocess.CompletedProcess(command, 0, "", "")

            runner = OcrRunner(ocrmypdf_bin=str(root / "ocrmypdf.exe"))
            with patch(
                "ocr_pipeline.ocr.subprocess.run", side_effect=fake_ocrmypdf
            ):
                result = runner.process_page(
                    book(source, pages=3), page=2, output_path=output
                )

            self.assertTrue(result.ok)
            self.assertEqual(
                (observed["input_pages"], observed["requested_page"]),
                (1, "1"),
            )

    def test_manifest_rejects_declared_page_count_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            write_pdf(source, pages=3)

            errors = book(source, pages=2).validate(Path(directory))

            self.assertTrue(any("page count mismatch" in error for error in errors))

    def test_processing_pages_are_recovered_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = PipelineState(Path(directory) / "progress.db")
            state.ensure_book_pages("fixture", "Fixture", pages=1)
            self.assertEqual(state.claim_next_page("fixture"), (1, 0))

            recovered = state.recover_processing("fixture")

            self.assertEqual(recovered, 1)
            self.assertEqual(state.claim_next_page("fixture"), (1, 0))

    def test_reset_book_requeues_every_page_and_clears_skips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = PipelineState(Path(directory) / "progress.db")
            state.ensure_book_pages("fixture", "Fixture", pages=2)
            state.claim_next_page("fixture")
            state.mark_failed_terminal("fixture", 1, "old failure")
            state.claim_next_page("fixture")
            state.mark_done("fixture", 2, "old-output.pdf")

            reset = state.reset_book("fixture")

            self.assertEqual(reset, {"pages": 2, "skipped": 1})
            self.assertEqual(state.book_summary("fixture"), {"pending": 2})
            self.assertEqual(state.claim_next_page("fixture"), (1, 0))

    def test_legacy_progress_is_imported_by_book_title(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "progress.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "CREATE TABLE progress ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "book_name TEXT NOT NULL, page INTEGER NOT NULL,"
                    "status TEXT NOT NULL, output_path TEXT,"
                    "error_message TEXT, timestamp TEXT,"
                    "retry_count INTEGER DEFAULT 0,"
                    "UNIQUE(book_name, page))"
                )
                conn.execute(
                    "INSERT INTO progress "
                    "(book_name, page, status, error_message, retry_count) "
                    "VALUES (?, ?, 'failed', ?, 0)",
                    ("Fixture", 1, "legacy failure"),
                )
                conn.commit()
            finally:
                conn.close()

            state = PipelineState(db_path)
            state.ensure_book_pages("fixture", "Fixture", pages=2)

            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    "SELECT page, status, attempts FROM progress "
                    "WHERE book_id=? ORDER BY page",
                    ("fixture",),
                ).fetchall()
            finally:
                conn.close()

            self.assertEqual(rows, [(1, "pending", 0), (2, "pending", 0)])

    def test_nonretryable_failure_is_skipped_with_one_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "progress.db"
            failed_artifact = Path(directory) / "partial.pdf"
            failed_artifact.write_bytes(b"invalid pdf")
            state = PipelineState(db_path)
            state.ensure_book_pages("fixture", "Fixture", pages=1)
            state.claim_next_page("fixture")
            worker = PageWorker(
                state=state,
                runner=object(),
                monitor=MemoryMonitor(max_workers=1),
                max_attempts=3,
            )

            worker._handle_result(
                book(Path(directory) / "source.pdf"),
                page=1,
                attempts_so_far=0,
                result=OcrResult(
                    returncode=4,
                    stdout="",
                    stderr="xref 200: this image could not be processed",
                    output_path=failed_artifact,
                ),
            )

            conn = sqlite3.connect(db_path)
            try:
                progress = conn.execute(
                    "SELECT status, attempts FROM progress WHERE book_id=? AND page=?",
                    ("fixture", 1),
                ).fetchone()
                skipped = conn.execute(
                    "SELECT attempts, last_error FROM skipped WHERE book_id=? AND page=?",
                    ("fixture", 1),
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(progress, ("skipped", 1))
            self.assertEqual(skipped[0], 1)
            self.assertIn("xref 200", skipped[1])
            self.assertFalse(failed_artifact.exists())
            self.assertTrue(Path(str(failed_artifact) + ".failed").exists())
            self.assertIn("failed_artifact=", skipped[1])

    def test_worker_exception_is_recorded_instead_of_leaking_processing(self) -> None:
        class RaisingRunner:
            def process_page(self, *args, **kwargs):
                raise RuntimeError("simulated worker crash")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "progress.db"
            state = PipelineState(db_path)
            worker = PageWorker(
                state=state,
                runner=RaisingRunner(),
                monitor=MemoryMonitor(max_workers=1),
                max_attempts=1,
                retry_backoff_s=0,
                memory_poll_interval_s=0.001,
            )

            worker.run_book(book(root / "source.pdf"), root / "output")

            conn = sqlite3.connect(db_path)
            try:
                progress = conn.execute(
                    "SELECT status, attempts, error_message FROM progress "
                    "WHERE book_id=? AND page=?",
                    ("fixture", 1),
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(progress[:2], ("skipped", 1))
            self.assertIn("RuntimeError", progress[2])
            self.assertIn("simulated worker crash", progress[2])

    def test_last_inflight_page_is_retried_before_worker_returns(self) -> None:
        import time

        class RetryRunner:
            calls = 0

            def process_page(self, book, page, output_path):
                self.calls += 1
                time.sleep(0.02)
                if self.calls == 1:
                    return OcrResult(-1, "", "transient", output_path)
                write_pdf(output_path, 1)
                return OcrResult(0, "", "", output_path)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_pdf(root / "source.pdf", pages=1)
            state = PipelineState(root / "progress.db")
            runner = RetryRunner()
            worker = PageWorker(state, runner, MemoryMonitor(max_workers=2),
                                max_attempts=2, retry_backoff_s=0,
                                memory_poll_interval_s=0.001)
            worker.run_book(book(root / "source.pdf"), root / "output")
            self.assertEqual(state.book_summary("fixture"), {"done": 1})
            self.assertEqual(runner.calls, 2)

    def test_timeout_with_text_output_returns_a_failed_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            write_pdf(source, pages=1)
            runner = OcrRunner(ocrmypdf_bin=str(root / "ocrmypdf.exe"))
            timeout = subprocess.TimeoutExpired(
                cmd=["ocrmypdf"],
                timeout=1,
                output="partial stdout",
                stderr="partial stderr",
            )

            with patch("ocr_pipeline.ocr.subprocess.run", side_effect=timeout):
                result = runner.process_page(
                    book(source),
                    page=1,
                    output_path=root / "output.pdf",
                    timeout_s=1,
                )

            self.assertEqual(result.returncode, -1)
            self.assertTrue(result.timed_out)
            self.assertEqual(result.stdout, "partial stdout")
            self.assertIn("partial stderr", result.stderr)
            self.assertIn("TIMEOUT after 1s", result.stderr)


if __name__ == "__main__":
    unittest.main()
