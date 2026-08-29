"""YAML manifest loader and book entry model."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pypdf import PdfReader
import yaml


OcrMode = Literal["redo", "skip", "force"]


@dataclass(frozen=True)
class BookEntry:
    id: str
    title: str
    source: str
    mode: OcrMode
    pages: int
    language: str = "chi_sim+eng"
    priority: int = 100         # 数字越小越先跑
    extra_args: list[str] = field(default_factory=list)
    enabled: bool = True

    def output_pdf_for(self, page: int, output_dir: Path) -> Path:
        """单页输出路径。"""
        safe = self._safe(self.title)
        return output_dir / f"{self.id}_{safe}_page{page:03d}.pdf"

    @staticmethod
    def _safe(name: str) -> str:
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, "_")
        return name.strip().replace(" ", "_")

    def validate(self, root: Path) -> list[str]:
        """校验，返回错误信息列表。空列表表示通过。"""
        errors: list[str] = []
        src = Path(self.source)
        if not src.is_absolute():
            src = root / src
        if not src.exists():
            errors.append(f"source not found: {src}")
            return errors
        if self.pages <= 0:
            errors.append(f"pages must be positive: {self.pages}")
        if self.mode not in ("redo", "skip", "force"):
            errors.append(f"invalid mode: {self.mode}")
        if self.pages > 0:
            try:
                actual_pages = len(PdfReader(str(src), strict=False).pages)
            except Exception as exc:
                errors.append(
                    f"source unreadable: {src} ({type(exc).__name__}: {exc})"
                )
            else:
                if actual_pages != self.pages:
                    errors.append(
                        f"page count mismatch: declared={self.pages}, "
                        f"actual={actual_pages}: {src}"
                    )
        return errors


@dataclass(frozen=True)
class Manifest:
    books: list[BookEntry]
    output_dir: Path
    db_path: Path

    def ordered_books(self) -> list[BookEntry]:
        return sorted(
            [b for b in self.books if b.enabled],
            key=lambda b: (b.priority, b.id),
        )


def load_manifest(path: Path, project_root: Path) -> Manifest:
    """从 YAML 文件读取 manifest。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    output_dir = Path(data.get("output_dir", project_root / "ocr-pilot-20260825"))
    db_path = Path(data.get("db_path", output_dir / "ocr_progress.db"))
    output_dir.mkdir(parents=True, exist_ok=True)

    books: list[BookEntry] = []
    for raw in data.get("books", []):
        books.append(
            BookEntry(
                id=raw["id"],
                title=raw["title"],
                source=raw["source"],
                mode=raw.get("mode", "redo"),
                pages=int(raw["pages"]),
                language=raw.get("language", "chi_sim+eng"),
                priority=int(raw.get("priority", 100)),
                extra_args=list(raw.get("extra_args", [])),
                enabled=bool(raw.get("enabled", True)),
            )
        )

    if not books:
        raise ValueError(f"manifest has no books: {path}")

    return Manifest(books=books, output_dir=output_dir, db_path=db_path)
