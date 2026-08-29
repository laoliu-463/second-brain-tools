"""Run the five requested OCR books sequentially without opening consoles."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OCR_RUN = PROJECT_ROOT / "scripts" / "ocr" / "ocr_run.py"
MANIFEST = PROJECT_ROOT / "config" / "ocr" / "books.yaml"
BOOK_IDS = (
    "econ_micro_\u7ecf\u6d4e\u5b66\u539f\u7406\u5fae\u89c2",
    "econ_macro_\u7ecf\u6d4e\u5b66\u539f\u7406\u5b8f\u89c2",
    "econ_mankiw_\u5b8f\u89c2\u7ecf\u6d4e\u5b66\u7b2c\u5341\u7248",
    "econ_political_\u9a6c\u653f\u7ecf\u6d4e\u5b66\u6982\u8bba",
    "history_\u53e4\u672c\u7af9\u4e66\u7eaa\u5e74\u8bd1\u6ce8",
)


def main() -> int:
    for book_id in BOOK_IDS:
        result = subprocess.run(
            [
                sys.executable,
                "-u",
                str(OCR_RUN),
                "--manifest",
                str(MANIFEST),
                "--max-workers",
                "6",
                "--max-attempts",
                "3",
                "--log-level",
                "INFO",
                "--book",
                book_id,
            ],
            cwd=PROJECT_ROOT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
