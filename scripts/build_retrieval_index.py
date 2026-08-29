"""Build and query the local Chinese BM25 retrieval index.

The SQLite database is a disposable local artifact under ``.ecc``. Markdown
files remain the source of truth. Corpus selection is an explicit allowlist so
governance files, logs, and unreviewed workspace artifacts cannot silently
become retrieval evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import re
import sqlite3
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_RELATIVE = Path(".ecc/retrieval/knowledge.db")
DEFAULT_DICTIONARY_RELATIVE = Path("schema/retrieval_terms.txt")
INDEX_VERSION = "bm25-jieba-v1"
TARGET_TOKENS = 400
MAX_TOKENS = 480
OVERLAP_TOKENS = 60
PRIMARY_SOURCE_KINDS = frozenset({"article", "course"})

COLLECTIONS = (
    (Path("人智55篇/正文"), "article", "high"),
    (Path("大小课/课程正文"), "course", "high"),
    (Path("炒股实操/课程转写"), "practice_transcript", "low"),
    (Path("炒股实操/整理"), "practice_synthesis", "medium"),
    (Path("资料库/文字层/书籍与讲义"), "book_text_layer", "medium"),
    (Path("资料库/专题整理"), "topic_synthesis", "medium"),
)
EXCLUDED_FILENAMES = {"agent.md", "agents.md"}
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
PAGE_RE = re.compile(r"^Page\s+(\d+)$", re.IGNORECASE)
TOKEN_RE = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]", re.UNICODE)
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?；;\n])")
SEARCH_TOKEN_RE = re.compile(r"[\w\u3400-\u9fff]+", re.UNICODE)
STOPWORDS = {
    "的",
    "了",
    "是",
    "在",
    "和",
    "与",
    "及",
    "或",
    "对",
    "由",
    "将",
    "这",
    "那",
    "一个",
    "一种",
}


class RetrievalIndexError(RuntimeError):
    """Raised when the local index cannot be built or queried safely."""


@dataclass(frozen=True)
class SourceDocument:
    path: Path
    relative_path: Path
    source_kind: str
    default_confidence: str


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    path: Path
    title: str
    section: str
    page: int | None
    ordinal: int
    content: str
    token_count: int


@dataclass(frozen=True)
class IndexStats:
    documents: int
    chunks: int
    characters: int
    database_path: Path


@dataclass(frozen=True)
class SearchResult:
    chunk_id: str
    path: Path
    title: str
    section: str
    page: int | None
    confidence: str
    source_kind: str
    content: str
    score: float


@dataclass(frozen=True)
class _Section:
    name: str
    page: int | None
    text: str


def _load_jieba(dictionary_path: Path | None = None):
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="pkg_resources is deprecated as an API.*",
                category=UserWarning,
            )
            jieba = importlib.import_module("jieba")
    except ImportError as exc:
        raise RetrievalIndexError(
            "jieba is required; run: python -m pip install -r requirements-retrieval.txt"
        ) from exc
    jieba.setLogLevel(30)
    if dictionary_path and dictionary_path.is_file():
        with dictionary_path.open("rb") as handle:
            jieba.load_userdict(handle)
    return jieba


def discover_documents(root: Path) -> list[SourceDocument]:
    """Return only Markdown files from governed searchable collections."""
    resolved_root = root.resolve()
    documents: list[SourceDocument] = []
    for relative_root, source_kind, confidence in COLLECTIONS:
        collection_root = resolved_root / relative_root
        if not collection_root.is_dir():
            continue
        for path in collection_root.rglob("*.md"):
            if path.name.casefold() in EXCLUDED_FILENAMES:
                continue
            documents.append(
                SourceDocument(
                    path=path,
                    relative_path=path.relative_to(resolved_root),
                    source_kind=source_kind,
                    default_confidence=confidence,
                )
            )
    return sorted(documents, key=lambda item: item.relative_path.as_posix())


def estimate_token_count(text: str) -> int:
    """Estimate BERT-like tokens without requiring the future embedding model."""
    count = 0
    for match in TOKEN_RE.finditer(text):
        token = match.group(0)
        if token.isascii() and (token.isalnum() or "_" in token):
            count += max(1, math.ceil(len(token) / 4))
        else:
            count += 1
    return count


def _parse_frontmatter(markdown: str) -> tuple[dict[str, object], str]:
    text = markdown.lstrip("\ufeff")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    loaded = yaml.safe_load(text[4:end]) or {}
    metadata = loaded if isinstance(loaded, dict) else {}
    return metadata, text[end + 5 :]


def _parse_sections(markdown: str, fallback_title: str) -> tuple[dict[str, object], str, list[_Section]]:
    metadata, body = _parse_frontmatter(markdown)
    source_transcript = metadata.get("type") == "source-transcript"
    title = fallback_title
    section = fallback_title
    page: int | None = None
    seen_title = False
    lines: list[str] = []
    sections: list[_Section] = []

    def flush() -> None:
        text = "\n".join(lines).strip()
        if text:
            sections.append(_Section(section, page, text))
        lines.clear()

    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = HEADING_RE.match(line)
        if not match:
            lines.append(line)
            continue
        level = len(match.group(1))
        heading = match.group(2).strip()
        if level == 1 and not seen_title:
            flush()
            title = heading
            section = heading
            page = None
            seen_title = True
            continue
        page_match = PAGE_RE.fullmatch(heading)
        if level == 2 and page_match:
            flush()
            page = int(page_match.group(1))
            section = heading
        elif source_transcript:
            lines.append(line)
        else:
            flush()
            section = heading
    flush()
    return metadata, title, sections


def _tail_for_overlap(text: str, token_limit: int) -> str:
    if token_limit <= 0:
        return ""
    candidate = text[-token_limit:]
    while candidate and estimate_token_count(candidate) > token_limit:
        candidate = candidate[1:]
    return candidate.strip()


def _split_oversized_unit(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    pieces: list[str] = []
    remaining = text.strip()
    while remaining:
        end = min(len(remaining), max_tokens)
        candidate = remaining[:end]
        while end > 1 and estimate_token_count(candidate) > max_tokens:
            end -= 1
            candidate = remaining[:end]
        candidate = candidate.strip()
        if not candidate:
            raise RetrievalIndexError("cannot split an oversized Markdown segment")
        pieces.append(candidate)
        if end >= len(remaining):
            break
        overlap = _tail_for_overlap(candidate, overlap_tokens)
        remaining = (overlap + remaining[end:]).strip()
    return pieces


def _split_section_text(
    text: str,
    target_tokens: int,
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for sentence in SENTENCE_BOUNDARY_RE.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            if estimate_token_count(sentence) > max_tokens:
                units.extend(
                    _split_oversized_unit(sentence, max_tokens, overlap_tokens)
                )
            else:
                units.append(sentence)

    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n{unit}".strip() if current else unit
        if current and estimate_token_count(candidate) > target_tokens:
            chunks.append(current)
            overlap = _tail_for_overlap(current, overlap_tokens)
            candidate = f"{overlap}\n{unit}".strip() if overlap else unit
        if estimate_token_count(candidate) > max_tokens:
            chunks.extend(
                _split_oversized_unit(candidate, max_tokens, overlap_tokens)
            )
            current = ""
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def chunk_markdown(
    relative_path: Path,
    markdown: str,
    target_tokens: int = TARGET_TOKENS,
    max_tokens: int = MAX_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[Chunk]:
    if not 0 <= overlap_tokens < target_tokens <= max_tokens:
        raise RetrievalIndexError("chunk token limits must satisfy 0 <= overlap < target <= max")
    _, title, sections = _parse_sections(markdown, relative_path.stem)
    chunks: list[Chunk] = []
    ordinal = 0
    for section in sections:
        for content in _split_section_text(
            section.text,
            target_tokens,
            max_tokens,
            overlap_tokens,
        ):
            ordinal += 1
            digest_input = "\0".join(
                [
                    relative_path.as_posix(),
                    section.name,
                    str(section.page or ""),
                    str(ordinal),
                    hashlib.sha256(content.encode("utf-8")).hexdigest(),
                ]
            )
            chunks.append(
                Chunk(
                    chunk_id=hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:32],
                    path=relative_path,
                    title=title,
                    section=section.name,
                    page=section.page,
                    ordinal=ordinal,
                    content=content,
                    token_count=estimate_token_count(content),
                )
            )
    return chunks


def _normalized_terms(tokens: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for token in tokens:
        value = token.strip().casefold()
        if not value or value in STOPWORDS or not SEARCH_TOKEN_RE.fullmatch(value):
            continue
        normalized.append(value)
    return normalized


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        PRAGMA temp_store=MEMORY;
        CREATE TABLE index_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE documents (
            document_id TEXT PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            confidence TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            characters INTEGER NOT NULL
        );
        CREATE TABLE chunks (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id TEXT NOT NULL UNIQUE,
            document_id TEXT NOT NULL REFERENCES documents(document_id),
            path TEXT NOT NULL,
            title TEXT NOT NULL,
            section TEXT NOT NULL,
            page INTEGER,
            ordinal INTEGER NOT NULL,
            content TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            token_count INTEGER NOT NULL
        );
        CREATE INDEX chunks_document_id ON chunks(document_id);
        CREATE INDEX chunks_path_page ON chunks(path, page);
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            chunk_id UNINDEXED,
            tokens,
            tokenize='unicode61'
        );
        """
    )


def build_index(
    root: Path,
    db_path: Path,
    dictionary_path: Path | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> IndexStats:
    resolved_root = root.resolve()
    resolved_db = db_path.resolve()
    resolved_dictionary = dictionary_path.resolve() if dictionary_path else None
    jieba = _load_jieba(resolved_dictionary)
    documents = discover_documents(resolved_root)
    resolved_db.parent.mkdir(parents=True, exist_ok=True)
    temp_path = resolved_db.with_name(f".{resolved_db.name}.{os.getpid()}.tmp")
    temp_path.unlink(missing_ok=True)
    document_count = 0
    chunk_count = 0
    character_count = 0

    connection = sqlite3.connect(temp_path)
    try:
        _create_schema(connection)
        for document_number, document in enumerate(documents, 1):
            markdown = document.path.read_text(encoding="utf-8-sig")
            metadata, title, _ = _parse_sections(markdown, document.path.stem)
            confidence = str(
                metadata.get("confidence", document.default_confidence)
            )
            content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
            document_id = hashlib.sha256(
                document.relative_path.as_posix().encode("utf-8")
            ).hexdigest()[:24]
            connection.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    document.relative_path.as_posix(),
                    title,
                    document.source_kind,
                    confidence,
                    content_hash,
                    len(markdown),
                ),
            )
            document_count += 1
            character_count += len(markdown)
            for chunk in chunk_markdown(document.relative_path, markdown):
                cursor = connection.execute(
                    """
                    INSERT INTO chunks (
                        chunk_id, document_id, path, title, section, page,
                        ordinal, content, content_sha256, token_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.chunk_id,
                        document_id,
                        chunk.path.as_posix(),
                        chunk.title,
                        chunk.section,
                        chunk.page,
                        chunk.ordinal,
                        chunk.content,
                        hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
                        chunk.token_count,
                    ),
                )
                searchable_text = f"{chunk.title}\n{chunk.section}\n{chunk.content}"
                terms = _normalized_terms(jieba.cut_for_search(searchable_text))
                connection.execute(
                    "INSERT INTO chunks_fts(rowid, chunk_id, tokens) VALUES (?, ?, ?)",
                    (cursor.lastrowid, chunk.chunk_id, " ".join(terms)),
                )
                chunk_count += 1
            if progress and (
                document_number % 25 == 0 or document_number == len(documents)
            ):
                progress(document_number, len(documents), chunk_count)

        metadata_rows = {
            "index_version": INDEX_VERSION,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "root": str(resolved_root),
            "documents": str(document_count),
            "chunks": str(chunk_count),
            "characters": str(character_count),
            "target_tokens": str(TARGET_TOKENS),
            "max_tokens": str(MAX_TOKENS),
            "overlap_tokens": str(OVERLAP_TOKENS),
        }
        connection.executemany(
            "INSERT INTO index_meta(key, value) VALUES (?, ?)",
            metadata_rows.items(),
        )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RetrievalIndexError(f"SQLite integrity check failed: {integrity}")
    except Exception:
        connection.close()
        temp_path.unlink(missing_ok=True)
        raise
    else:
        connection.close()
        os.replace(temp_path, resolved_db)

    return IndexStats(
        documents=document_count,
        chunks=chunk_count,
        characters=character_count,
        database_path=resolved_db,
    )


def query_index(
    db_path: Path,
    query: str,
    limit: int = 10,
    dictionary_path: Path | None = None,
) -> list[SearchResult]:
    if limit < 1:
        raise RetrievalIndexError("query limit must be positive")
    resolved_db = db_path.resolve()
    if not resolved_db.is_file():
        raise RetrievalIndexError(f"retrieval index not found: {resolved_db}")
    jieba = _load_jieba(dictionary_path.resolve() if dictionary_path else None)
    terms = _normalized_terms(jieba.cut_for_search(query))
    if not terms:
        return []
    match_query = " OR ".join(
        f'"{term.replace(chr(34), chr(34) * 2)}"' for term in dict.fromkeys(terms)
    )
    primary = ", ".join(f"'{kind}'" for kind in sorted(PRIMARY_SOURCE_KINDS))
    uri = resolved_db.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        rows = connection.execute(
            f"""
            SELECT
                c.chunk_id, c.path, c.title, c.section, c.page,
                d.confidence, d.source_kind, c.content,
                -bm25(chunks_fts, 0.0, 1.0) AS score
            FROM chunks_fts
            JOIN chunks AS c ON c.rowid = chunks_fts.rowid
            JOIN documents AS d ON d.document_id = c.document_id
            WHERE chunks_fts MATCH ?
            ORDER BY
                CASE
                    WHEN d.source_kind IN ({primary}) THEN 0
                    ELSE 1
                END,
                bm25(chunks_fts, 0.0, 1.0),
                c.path,
                c.ordinal
            LIMIT ?
            """,
            (match_query, limit),
        ).fetchall()
    finally:
        connection.close()
    return [
        SearchResult(
            chunk_id=str(row[0]),
            path=Path(str(row[1])),
            title=str(row[2]),
            section=str(row[3]),
            page=int(row[4]) if row[4] is not None else None,
            confidence=str(row[5]),
            source_kind=str(row[6]),
            content=str(row[7]),
            score=float(row[8]),
        )
        for row in rows
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Atomically rebuild the local index")
    build.add_argument("--root", type=Path, default=PROJECT_ROOT)
    build.add_argument("--db", type=Path)
    build.add_argument("--dictionary", type=Path)

    query = subparsers.add_parser("query", help="Run a local Chinese BM25 query")
    query.add_argument("query")
    query.add_argument("--root", type=Path, default=PROJECT_ROOT)
    query.add_argument("--db", type=Path)
    query.add_argument("--dictionary", type=Path)
    query.add_argument("--limit", type=int, default=10)
    query.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    db_path = (args.db or (root / DEFAULT_DB_RELATIVE)).resolve()
    dictionary = (args.dictionary or (root / DEFAULT_DICTIONARY_RELATIVE)).resolve()
    if args.command == "build":
        stats = build_index(
            root,
            db_path,
            dictionary,
            progress=lambda done, total, chunks: print(
                f"progress documents={done}/{total} chunks={chunks}",
                flush=True,
            ),
        )
        print(
            f"indexed documents={stats.documents} chunks={stats.chunks} "
            f"characters={stats.characters} db={stats.database_path}"
        )
        return 0

    results = query_index(db_path, args.query, args.limit, dictionary)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        **result.__dict__,
                        "path": result.path.as_posix(),
                    }
                    for result in results
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    for rank, result in enumerate(results, 1):
        location = result.path.as_posix()
        if result.page is not None:
            location += f"#page-{result.page}"
        excerpt = " ".join(result.content.split())[:240]
        print(
            f"{rank}. {result.title} | {location} | "
            f"confidence={result.confidence} score={result.score:.4f}"
        )
        print(f"   {excerpt}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RetrievalIndexError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
