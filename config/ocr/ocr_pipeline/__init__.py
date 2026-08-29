"""OCR pipeline package: manifest-driven, memory-throttled, fault-tolerant.

Modules:
- state: SQLite progress + skipped tables, psutil memory checks
- ocr: single-page ocrmypdf subprocess wrapper
- worker: ThreadPoolExecutor with dynamic memory-aware concurrency
- manifest: YAML loader and validator
"""

from .state import PipelineState, MemoryMonitor
from .ocr import OcrRunner, OcrResult
from .worker import PageWorker, sha256_file
from .manifest import Manifest, BookEntry, load_manifest

__all__ = [
    "PipelineState",
    "MemoryMonitor",
    "OcrRunner",
    "OcrResult",
    "PageWorker",
    "sha256_file",
    "Manifest",
    "BookEntry",
    "load_manifest",
]
