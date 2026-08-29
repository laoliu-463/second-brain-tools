"""单页 ocrmypdf 子进程封装。"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from .manifest import BookEntry


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OcrResult:
    returncode: int
    stdout: str
    stderr: str
    output_path: Path
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


# Windows：防止 ocrmypdf.exe（console 子进程）在父进程无 console 的情况下
# AllocConsole 弹窗。capture_output=True 把 stdout/stderr 拿到内存即可。
_CREATE_NO_WINDOW = 0x08000000


def _timeout_stream_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


class OcrRunner:
    """对单页调用 ocrmypdf。

    设计要点：
    - 严格一次只跑一页（--pages N），让失败隔离到页
    - 把 TESSDATA_PREFIX / Path 注入环境变量，避免在 PowerShell 启动
    - 输出文件按 页号 占位，便于乱序完成后顺序合并
    """

    DEFAULT_TIMEOUT_S = 300
    OCRMYPDF_BIN_CANDIDATES = [
        r"C:\Users\caojianing\AppData\Local\Programs\Python\Python312\Scripts\ocrmypdf.exe",
        shutil.which("ocrmypdf") or "",
    ]
    TESSDATA_PATHS = [
        r"C:\Program Files\Tesseract-OCR\tessdata",
    ]

    def __init__(self, ocrmypdf_bin: str | None = None) -> None:
        self.bin = ocrmypdf_bin or self._find_ocrmypdf()

    @classmethod
    def _find_ocrmypdf(cls) -> str:
        for candidate in cls.OCRMYPDF_BIN_CANDIDATES:
            if candidate and Path(candidate).exists():
                return candidate
        raise FileNotFoundError(
            "ocrmypdf not found; tried: " + ", ".join(cls.OCRMYPDF_BIN_CANDIDATES)
        )

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        for td in self.TESSDATA_PATHS:
            if Path(td).exists():
                env["TESSDATA_PREFIX"] = td
                break
        # tesseract 也可能不在默认路径
        tess = r"C:\Program Files\Tesseract-OCR"
        if Path(tess).exists():
            env["Path"] = tess + os.pathsep + env.get("Path", "")
        return env

    def process_page(
        self,
        book: BookEntry,
        page: int,
        output_path: Path,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> OcrResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="ocr-source-page-") as temp_dir:
            single_page_input = Path(temp_dir) / f"page-{page:04d}.pdf"
            self._extract_page(book.source, page, single_page_input)
            cmd = [
                self.bin,
                "-l", book.language,
                "--pages", "1",
                "--mode", book.mode,
                "-j", "1",                    # ocrmypdf 内部单线程
                "--output-type", "pdf",
                *book.extra_args,
                str(single_page_input),
                str(output_path),
            ]
            logger.debug("OCR cmd: %s", " ".join(cmd))
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                    env=self._env(),
                    creationflags=_CREATE_NO_WINDOW,    # 不弹 AllocConsole 框
                )
                return OcrResult(
                    returncode=proc.returncode,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    output_path=output_path,
                )
            except subprocess.TimeoutExpired as exc:
                return OcrResult(
                    returncode=-1,
                    stdout=_timeout_stream_text(exc.stdout),
                    stderr=_timeout_stream_text(exc.stderr)
                    + f"\n[TIMEOUT after {timeout_s}s]",
                    output_path=output_path,
                    timed_out=True,
                )
            except FileNotFoundError as exc:
                return OcrResult(
                    returncode=-1,
                    stdout="",
                    stderr=f"[FileNotFoundError] {exc}",
                    output_path=output_path,
                )

    @staticmethod
    def _extract_page(source: str, page: int, destination: Path) -> None:
        reader = PdfReader(source, strict=False)
        if page < 1 or page > len(reader.pages):
            raise ValueError(
                f"page {page} outside source range 1-{len(reader.pages)}"
            )
        writer = PdfWriter()
        writer.add_page(reader.pages[page - 1])
        with destination.open("wb") as handle:
            writer.write(handle)
