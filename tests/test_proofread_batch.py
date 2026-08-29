from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
import yaml

from scripts import proofread_batch as batch
from scripts import ocr_compare_text_layers as compare_layers
from scripts.ocr import ocr_run
from ocr_pipeline.manifest import BookEntry, Manifest


class ProofreadBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.work = self.root / ".ecc" / "proofreading"
        self.work.mkdir(parents=True)
        self.source = self.root / "资料库/系列/原始资料层/source.pdf"
        self.target = self.root / "资料库/系列/文字层/source.md"
        self.source.parent.mkdir(parents=True)
        self.target.parent.mkdir(parents=True)
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 72), "First page.")
            pdf.new_page().insert_text((72, 72), "Second page.")
            pdf.save(self.source)
        self.target.write_text(
            "---\nsource_sha256: " + batch.file_hash(self.source)
            + "\nsource_file: old.pdf\npages: 2\nconfidence: low\n---\n"
            "# Fixture\n\n## Page 1\n\nFirst wrong.\n\n## Page 2\n\nSecond page.\n",
            encoding="utf-8")
        self.rel = self.target.relative_to(self.root).as_posix()

    def prepare(self):
        batch.scan(self.root, self.work)
        return batch.prepare(self.root, self.work, self.rel, "1")

    def approve(self, review):
        plan = json.loads(review.read_text(encoding="utf-8"))
        entry = plan["pages"][0]
        entry.update(approved=True, reviewer="test fixture", reason="compared original page")
        (self.work / entry["replacement"]).write_text("First page.", encoding="utf-8")
        batch.dump(review, plan)
        return plan

    def test_comparison_distinguishes_no_evidence_from_match(self):
        self.assertEqual(batch.compare("甲\n乙", "甲 乙"), "exact_after_layout_whitespace_only")
        self.assertEqual(batch.compare("甲，", "甲,"), "character_difference_requires_review")
        self.assertEqual(batch.compare("[本页未提取到文本，可能需要 OCR。]", ""), "both_empty_requires_visual_or_ocr")
        self.assertEqual(batch.compare("", "abc"), "text_missing_native_available")
        self.assertEqual(batch.compare("abc", ""), "source_image_requires_visual")
        self.assertEqual(compare_layers.ngram_similarity("甲乙丙丁", "甲乙丙丁"), 1.0)
        self.assertGreater(compare_layers.ngram_similarity("甲乙丙丁", "甲乙丙戊"), 0)
        self.assertEqual(compare_layers.classify("甲 乙", "甲乙", 1), "exact_ignoring_layout_whitespace")
        self.assertEqual(compare_layers.classify("", "甲乙", 0), "text_layer_missing")

    def test_scan_all_pages_resume_and_refresh_changed_file(self):
        first = batch.scan(self.root, self.work)
        self.assertEqual((first["files"], first["pages"], first["scanned"]), (1, 2, 1))
        self.assertFalse(first["visual_proofreading_complete"])
        self.assertEqual(batch.scan(self.root, self.work)["resumed"], 1)
        self.target.write_text(self.target.read_text(encoding="utf-8").replace("First wrong.", "First page."), encoding="utf-8")
        refreshed = batch.scan(self.root, self.work)
        self.assertEqual(refreshed["scanned"], 1)
        self.assertEqual(refreshed["comparison_counts"]["exact_after_layout_whitespace_only"], 2)
        self.target.unlink()
        self.assertEqual(batch.scan(self.root, self.work)["pages"], 0)

    def test_invalid_yaml_identity_recovery_keeps_issue(self):
        text = '---\nsource_file: "D:\\资料库\\old.pdf"\nsource_sha256: ABC123\n---\n## Page 1\n甲'
        meta, _, _, issues = batch.parse(text)
        self.assertEqual(meta["source_sha256"], "ABC123")
        self.assertIn("invalid_yaml", issues)

    def test_missing_original_and_duplicate_markers_are_reported(self):
        self.target.write_text(self.target.read_text(encoding="utf-8").replace("## Page 2", "## Page 1"), encoding="utf-8")
        batch.scan(self.root, self.work)
        docs = json.loads((self.work / "documents.json").read_text(encoding="utf-8"))
        self.assertIn("invalid_page_sequence", docs[0]["issues"])
        self.source.unlink()
        self.assertEqual(batch.scan(self.root, self.work)["error_files"], 1)

    def test_discovery_excludes_backups_and_navigation(self):
        backup = self.root / "资料库/.ecc/backup/文字层/old.md"
        backup.parent.mkdir(parents=True)
        backup.write_bytes(self.target.read_bytes())
        (self.target.parent / "README.md").write_text("navigation", encoding="utf-8")
        self.assertEqual(batch.discover(self.root)[0], [self.target])

    def test_tall_page_tiles_cover_entire_height_with_overlap(self):
        rect = fitz.Rect(0, 0, 812, 12137)
        clips = list(batch.tiles(rect))
        self.assertEqual(clips[0].y0, 0)
        self.assertEqual(clips[-1].y1, rect.y1)
        self.assertTrue(all(b.y0 < a.y1 for a, b in zip(clips, clips[1:])))
        self.assertEqual(batch.page_numbers(None, 8), list(range(1, 9)))
        with self.assertRaises(ValueError):
            batch.page_numbers("0-2", 8)

    def test_apply_dry_run_and_backup_preserve_other_page(self):
        review = self.prepare()
        self.approve(review)
        before, original = self.target.read_bytes(), self.source.read_bytes()
        receipt = batch.apply_review(self.root, self.work, review)
        self.assertFalse(receipt["written"])
        self.assertEqual(self.target.read_bytes(), before)
        receipt = batch.apply_review(self.root, self.work, review, write=True)
        self.assertTrue(receipt["written"])
        self.assertEqual((self.root / receipt["backup"]).read_bytes(), before)
        after = self.target.read_text(encoding="utf-8")
        self.assertEqual(batch.parse(after)[2], {1: "First page.", 2: "Second page."})
        self.assertIn("confidence: low", after)
        self.assertEqual(self.source.read_bytes(), original)

    def test_apply_rejects_unapproved_and_concurrent_changes(self):
        review = self.prepare()
        with self.assertRaisesRegex(ValueError, "approval"):
            batch.apply_review(self.root, self.work, review, True)
        self.approve(review)
        self.target.write_bytes(self.target.read_bytes() + b"\nNew edit")
        with self.assertRaisesRegex(ValueError, "changed"):
            batch.apply_review(self.root, self.work, review, True)

    def test_apply_rejects_changed_original_or_evidence(self):
        review = self.prepare()
        plan = self.approve(review)
        png = self.work / plan["pages"][0]["evidence"][0]["path"]
        png.write_bytes(b"different image")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            batch.apply_review(self.root, self.work, review, True)
        self.source.write_bytes(self.source.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            batch.apply_review(self.root, self.work, review, True)

    def test_apply_rejects_protected_paths_and_injected_page_headings(self):
        for path in ["人智55篇/正文/00.md", "资料库/原始资料层/source.pdf", "../outside.md"]:
            with self.assertRaises(ValueError):
                batch.text_path(self.root, path)
        review = self.prepare()
        plan = self.approve(review)
        replacement = self.work / plan["pages"][0]["replacement"]
        replacement.write_text("## Page 9\nInjected", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "page body"):
            batch.apply_review(self.root, self.work, review, True)
        plan["pages"][0]["replacement"] = "../outside.txt"
        batch.dump(review, plan)
        with self.assertRaisesRegex(ValueError, "escapes"):
            batch.apply_review(self.root, self.work, review, True)

    def test_prepare_all_pages_and_reject_stale_scan(self):
        batch.scan(self.root, self.work)
        self.assertEqual(batch.fallback_pages(self.root, self.work, self.rel, 99), "1-2")
        review = batch.prepare(self.root, self.work, self.rel, None)
        plan = json.loads(review.read_text(encoding="utf-8"))
        self.assertEqual([p["page"] for p in plan["pages"]], [1, 2])
        self.target.write_bytes(self.target.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            batch.prepare(self.root, self.work, self.rel, None)

    def test_manifest_reuses_engine_in_private_hash_bound_job(self):
        batch.scan(self.root, self.work)
        manifest = batch.ocr_manifest(self.root, self.work, [self.rel, self.rel])
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        self.assertEqual(len(data["books"]), 1)
        self.assertEqual(data["books"][0]["source"], str(self.source))
        self.assertEqual(data["books"][0]["mode"], "force")
        self.assertIn(batch.file_hash(self.source), data["books"][0]["id"])
        self.assertTrue(Path(data["output_dir"]).is_relative_to(self.work))

    def test_ocr_source_must_be_original_layer_pdf(self):
        self.assertEqual(batch.original_pdf(self.root, self.source), self.source)
        outside = self.root / "资料库/temporary.pdf"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_bytes(b"pdf")
        with self.assertRaisesRegex(ValueError, "original-material"):
            batch.original_pdf(self.root, outside)
        with self.assertRaisesRegex(ValueError, "original-material"):
            batch.original_pdf(self.root, self.target)

    def test_tile_ocr_keeps_each_candidate_separate(self):
        review = self.prepare()

        def fake_tesseract(command, **kwargs):
            Path(command[2] + ".txt").write_text("candidate", encoding="utf-8")
            return __import__("subprocess").CompletedProcess(command, 0, "", "")

        with patch.object(batch, "tesseract_binary", return_value="tesseract"), \
                patch.object(batch.subprocess, "run", side_effect=fake_tesseract):
            result = batch.tile_ocr(review, self.work)
        self.assertEqual(result, {
            "engine": "Tesseract chi_sim+eng",
            "completed": result["completed"],
            "candidates": 1,
            "failures": 0,
            "note": "Per-tile OCR candidates are not merged and are not approved text.",
        })
        plan = json.loads(review.read_text(encoding="utf-8"))
        self.assertEqual(len(plan["pages"][0]["ocr_candidates"]), 1)
        self.assertFalse(plan["pages"][0]["approved"])

    def test_work_directory_lock_blocks_concurrent_invocation(self):
        with batch.work_lock(self.work):
            with self.assertRaisesRegex(ValueError, "locked"):
                with batch.work_lock(self.work):
                    self.fail("second invocation acquired lock")
        self.assertFalse((self.work / "run.lock").exists())

    def test_book_selection_survives_sample(self):
        books = [BookEntry(id=n, title=n, source=str(self.source), mode="force", pages=2) for n in ("one", "two")]
        manifest = Manifest(books, self.work, self.work / "progress.sqlite")
        selected = ocr_run.select_books(manifest, "two", 1)
        self.assertEqual([(b.id, b.pages) for b in selected], [("two", 1)])
        self.assertEqual(books[1].pages, 2)


if __name__ == "__main__":
    unittest.main()
