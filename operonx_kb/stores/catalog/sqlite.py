"""The SQLite catalog: stdlib ``sqlite3``, WAL, versioned migrations.

A connection per transaction (operonx's own SQLite stores do the same), so the
catalog is safe to call from the worker threads ``bound="cpu"`` ops run in.
Writes take ``BEGIN IMMEDIATE`` so two ingests of one document serialise
instead of interleaving.

Migrations are the numbered ``migrations/sqlite/NNN_*.sql`` files; the applied
version is in ``kb_schema_version`` and the catalog upgrades itself on first
use (the pattern of operonx's ClickHouse store).
"""

from __future__ import annotations

import json
import sqlite3
from array import array
from contextlib import contextmanager
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set

from operonx_kb.errors import CatalogError
from operonx_kb.model.collection import Collection, CollectionSpec
from operonx_kb.model.document import (
    Chunk,
    Document,
    DocumentVersion,
    Element,
    Page,
    Region,
    VersionChunk,
    utcnow,
)
from operonx_kb.stores.catalog.base import Catalog, CommitResult, PurgeResult

__all__ = ["SqliteCatalog", "migrations"]


def migrations() -> List[tuple]:
    """``(version, name, sql)`` of every SQLite migration, in order."""
    folder = resources.files("operonx_kb.stores.catalog.migrations.sqlite")
    out = []
    for entry in folder.iterdir():
        name = entry.name
        if name.endswith(".sql") and name[:3].isdigit():
            out.append((int(name[:3]), name, entry.read_text(encoding="utf-8")))
    return sorted(out)


def _dt(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class SqliteCatalog(Catalog):
    """A catalog in one SQLite file.

    Args:
        path: The database file; its folder is created.
    """

    def __init__(self, path: Any):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def _tx(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    def schema_version(self) -> int:
        with self._tx() as c:
            c.execute("CREATE TABLE IF NOT EXISTS kb_schema_version (version INTEGER NOT NULL)")
            row = c.execute("SELECT MAX(version) AS v FROM kb_schema_version").fetchone()
        return int(row["v"] or 0)

    def migrate(self) -> int:
        """Apply pending migrations; return the schema version."""
        current = self.schema_version()
        for version, name, sql in migrations():
            if version <= current:
                continue
            with self._tx(write=True) as c:
                for statement in sql.split(";"):
                    if statement.strip():
                        c.execute(statement)
                c.execute("INSERT INTO kb_schema_version (version) VALUES (?)", (version,))
            current = version
        return current

    # collections -----------------------------------------------------------------

    def put_collection(self, collection: Collection) -> None:
        with self._tx(write=True) as c:
            c.execute(
                "INSERT INTO kb_collections (id, spec, tags, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET spec = excluded.spec, tags = excluded.tags",
                (collection.id, collection.spec.model_dump_json(), _j(collection.tags), _dt(utcnow())),
            )

    @staticmethod
    def _collection(row: sqlite3.Row) -> Collection:
        return Collection(id=row["id"], spec=CollectionSpec.model_validate_json(row["spec"]), tags=json.loads(row["tags"]))

    def get_collection(self, collection_id: str) -> Optional[Collection]:
        with self._tx() as c:
            row = c.execute("SELECT * FROM kb_collections WHERE id = ?", (collection_id,)).fetchone()
        return self._collection(row) if row else None

    def list_collections(self) -> List[Collection]:
        with self._tx() as c:
            rows = c.execute("SELECT * FROM kb_collections ORDER BY id").fetchall()
        return [self._collection(r) for r in rows]

    # documents and versions ----------------------------------------------------

    @staticmethod
    def _document(row: sqlite3.Row) -> Document:
        return Document(
            id=row["id"],
            collection_id=row["collection_id"],
            key=row["key"],
            title=row["title"],
            mime=row["mime"],
            tags=json.loads(row["tags"]),
            metadata=json.loads(row["metadata"]),
            active_version_id=row["active_version_id"],
            deleted_at=datetime.fromisoformat(row["deleted_at"]) if row["deleted_at"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_document(self, document_id: str) -> Optional[Document]:
        with self._tx() as c:
            row = c.execute("SELECT * FROM kb_documents WHERE id = ?", (document_id,)).fetchone()
        return self._document(row) if row else None

    def list_documents(self, collection_id: str, include_deleted: bool = False) -> List[Document]:
        sql = "SELECT * FROM kb_documents WHERE collection_id = ?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        with self._tx() as c:
            rows = c.execute(sql + " ORDER BY key", (collection_id,)).fetchall()
        return [self._document(r) for r in rows]

    @staticmethod
    def _version(row: sqlite3.Row) -> DocumentVersion:
        return DocumentVersion(
            id=row["id"],
            document_id=row["document_id"],
            ordinal=row["ordinal"],
            raw_sha=row["raw_sha"],
            text_sha=row["text_sha"],
            pipeline_fp=row["pipeline_fp"],
            status=row["status"],
            stats=json.loads(row["stats"]),
            error=row["error"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_version(self, version_id: str) -> Optional[DocumentVersion]:
        with self._tx() as c:
            row = c.execute("SELECT * FROM kb_versions WHERE id = ?", (version_id,)).fetchone()
        return self._version(row) if row else None

    def list_versions(self, document_id: str) -> List[DocumentVersion]:
        with self._tx() as c:
            rows = c.execute("SELECT * FROM kb_versions WHERE document_id = ? ORDER BY ordinal", (document_id,)).fetchall()
        return [self._version(r) for r in rows]

    def elements(self, version_id: str, canonical: str) -> List[Element]:
        with self._tx() as c:
            rows = c.execute(
                "SELECT * FROM kb_elements WHERE version_id = ? ORDER BY rowid", (version_id,)
            ).fetchall()
        out = []
        for r in rows:
            span = (r["span_start"], r["span_end"]) if r["span_start"] is not None else None
            text = canonical[span[0] : span[1]] if span is not None else (r["text"] or "")
            out.append(
                Element(
                    id=r["id"],
                    content_sha=r["content_sha"],
                    version_id=version_id,
                    parent_id=r["parent_id"],
                    path=r["path"],
                    ordinal=r["ordinal"],
                    depth=r["depth"],
                    kind=r["kind"],
                    layer=r["layer"],
                    level=r["level"],
                    text=text,
                    span=span,
                    regions=[Region.model_validate(x) for x in json.loads(r["regions"])],
                    attrs=json.loads(r["attrs"]),
                    confidence=r["confidence"],
                )
            )
        return out

    def pages(self, version_id: str) -> List[Page]:
        with self._tx() as c:
            rows = c.execute("SELECT * FROM kb_pages WHERE version_id = ? ORDER BY page_no", (version_id,)).fetchall()
        return [
            Page(version_id=version_id, page_no=r["page_no"], width=r["width"], height=r["height"], unit=r["unit"],
                 image_sha=r["image_sha"], text_layer=bool(r["text_layer"]))  # fmt: skip
            for r in rows
        ]

    def version_chunks(self, version_id: str) -> List[VersionChunk]:
        with self._tx() as c:
            rows = c.execute(
                "SELECT * FROM kb_version_chunks WHERE version_id = ? ORDER BY ordinal", (version_id,)
            ).fetchall()
        return [
            VersionChunk(
                version_id=version_id,
                chunk_id=r["chunk_id"],
                ordinal=r["ordinal"],
                spans=[tuple(s) for s in json.loads(r["spans"])],
                element_ids=json.loads(r["element_ids"]),
                pages=json.loads(r["pages"]),
            )
            for r in rows
        ]

    def get_chunks(self, chunk_ids: Sequence[str]) -> Dict[str, Chunk]:
        out: Dict[str, Chunk] = {}
        ids = list(dict.fromkeys(chunk_ids))
        with self._tx() as c:
            for start in range(0, len(ids), 500):
                part = ids[start : start + 500]
                marks = ",".join("?" * len(part))
                for r in c.execute(f"SELECT * FROM kb_chunks WHERE id IN ({marks})", part):
                    out[r["id"]] = Chunk(
                        id=r["id"],
                        document_id=r["document_id"],
                        content_sha=r["content_sha"],
                        kind=r["kind"],
                        heading_path=json.loads(r["heading_path"]),
                        token_count=r["token_count"],
                        text=r["text"],
                        embed_text=r["embed_text"],
                        embed_text_sha=r["embed_text_sha"],
                    )
        return out

    def active_chunk_ids(self, collection_id: Optional[str] = None, document_id: Optional[str] = None) -> Set[str]:
        sql = (
            "SELECT vc.chunk_id FROM kb_version_chunks vc JOIN kb_documents d ON d.active_version_id = vc.version_id "
            "WHERE d.deleted_at IS NULL"
        )
        args: List[str] = []
        if collection_id is not None:
            sql += " AND d.collection_id = ?"
            args.append(collection_id)
        if document_id is not None:
            sql += " AND d.id = ?"
            args.append(document_id)
        with self._tx() as c:
            return {r[0] for r in c.execute(sql, args)}

    # writes -----------------------------------------------------------------------

    def commit_version(
        self,
        document: Document,
        version: DocumentVersion,
        elements: Sequence[Element],
        pages: Sequence[Page],
        chunks: Sequence[Chunk],
        occurrences: Sequence[VersionChunk],
    ) -> CommitResult:
        with self._tx(write=True) as c:
            if c.execute("SELECT 1 FROM kb_collections WHERE id = ?", (document.collection_id,)).fetchone() is None:
                raise CatalogError(
                    f"collection {document.collection_id!r} does not exist; create it first "
                    "(KnowledgeBase.create_collection)"
                )
            row = c.execute("SELECT * FROM kb_documents WHERE id = ?", (document.id,)).fetchone()
            previous = row["active_version_id"] if row and row["deleted_at"] is None else None
            if previous == version.id:
                return CommitResult(committed=False, previous_version_id=previous)
            existing = c.execute("SELECT document_id, status FROM kb_versions WHERE id = ?", (version.id,)).fetchone()
            if existing is not None and existing["document_id"] != document.id:
                raise CatalogError(
                    "version id already belongs to another document; ids are content derived, so this is a bug",
                    {"version": version.id, "document": document.id, "owner": existing["document_id"]},
                )
            if row is None:
                c.execute(
                    "INSERT INTO kb_documents (id, collection_id, key, title, mime, tags, metadata, active_version_id, "
                    "deleted_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
                    (document.id, document.collection_id, document.key, document.title, document.mime,
                     _j(document.tags), _j(document.metadata), _dt(document.created_at)),
                )  # fmt: skip
            else:
                c.execute(
                    "UPDATE kb_documents SET title = ?, mime = ?, tags = ?, metadata = ?, deleted_at = NULL WHERE id = ?",
                    (document.title, document.mime, _j(document.tags), _j(document.metadata), document.id),
                )
            if existing is None:
                ordinal = c.execute(
                    "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM kb_versions WHERE document_id = ?", (document.id,)
                ).fetchone()[0]
                c.execute(
                    "INSERT INTO kb_versions (id, document_id, ordinal, raw_sha, text_sha, pipeline_fp, status, stats, "
                    "error, created_at) VALUES (?, ?, ?, ?, ?, ?, 'committed', ?, NULL, ?)",
                    (version.id, document.id, ordinal, version.raw_sha, version.text_sha, version.pipeline_fp,
                     _j(version.stats), _dt(version.created_at)),
                )  # fmt: skip
                c.executemany(
                    "INSERT INTO kb_elements (version_id, id, parent_id, path, ordinal, depth, kind, layer, level, "
                    "span_start, span_end, text, regions, attrs, content_sha, confidence) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (version.id, e.id, e.parent_id, e.path, e.ordinal, e.depth, e.kind, e.layer, e.level,
                         e.span[0] if e.span else None, e.span[1] if e.span else None,
                         e.text if e.span is None else None,
                         _j([r.model_dump(mode="json") for r in e.regions]), _j(e.attrs), e.content_sha, e.confidence)
                        for e in elements
                    ],
                )  # fmt: skip
                c.executemany(
                    "INSERT INTO kb_pages (version_id, page_no, width, height, unit, image_sha, text_layer) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(version.id, p.page_no, p.width, p.height, p.unit, p.image_sha, int(p.text_layer)) for p in pages],
                )
                c.executemany(
                    "INSERT OR IGNORE INTO kb_chunks (id, document_id, content_sha, kind, heading_path, token_count, "
                    "text, embed_text, embed_text_sha) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(ch.id, ch.document_id, ch.content_sha, ch.kind, _j(ch.heading_path), ch.token_count, ch.text,
                      ch.embed_text, ch.embed_text_sha) for ch in chunks],
                )  # fmt: skip
                c.executemany(
                    "INSERT INTO kb_version_chunks (version_id, chunk_id, ordinal, spans, element_ids, pages) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [(version.id, o.chunk_id, o.ordinal, _j([list(s) for s in o.spans]), _j(o.element_ids), _j(o.pages))
                     for o in occurrences],
                )  # fmt: skip
            else:
                c.execute("UPDATE kb_versions SET status = 'committed' WHERE id = ?", (version.id,))
            if previous is not None:
                c.execute("UPDATE kb_versions SET status = 'superseded' WHERE id = ?", (previous,))
            c.execute("UPDATE kb_documents SET active_version_id = ? WHERE id = ?", (version.id, document.id))
            old = {r[0] for r in c.execute("SELECT chunk_id FROM kb_version_chunks WHERE version_id = ?", (previous,))} if previous else set()
            new = {o.chunk_id for o in occurrences}
        return CommitResult(
            committed=True,
            previous_version_id=previous,
            removed_chunk_ids=sorted(old - new),
            added_chunk_ids=sorted(new - old),
        )

    def tombstone(self, document_id: str) -> List[str]:
        with self._tx(write=True) as c:
            row = c.execute("SELECT active_version_id FROM kb_documents WHERE id = ?", (document_id,)).fetchone()
            if row is None:
                raise CatalogError(f"no document {document_id!r}")
            active = row["active_version_id"]
            c.execute(
                "UPDATE kb_documents SET active_version_id = NULL, deleted_at = ? WHERE id = ?",
                (_dt(utcnow()), document_id),
            )
            if active is None:
                return []
            c.execute("UPDATE kb_versions SET status = 'superseded' WHERE id = ?", (active,))
            return sorted({r[0] for r in c.execute("SELECT chunk_id FROM kb_version_chunks WHERE version_id = ?", (active,))})

    def purge(self, document_id: str) -> PurgeResult:
        with self._tx(write=True) as c:
            versions = [r["id"] for r in c.execute("SELECT id FROM kb_versions WHERE document_id = ?", (document_id,))]
            shas = {
                sha
                for r in c.execute("SELECT raw_sha, text_sha FROM kb_versions WHERE document_id = ?", (document_id,))
                for sha in (r["raw_sha"], r["text_sha"])
            }
            chunk_ids = [r["id"] for r in c.execute("SELECT id FROM kb_chunks WHERE document_id = ?", (document_id,))]
            for table in ("kb_version_chunks", "kb_elements", "kb_pages"):
                c.executemany(f"DELETE FROM {table} WHERE version_id = ?", [(v,) for v in versions])
            c.execute("DELETE FROM kb_chunks WHERE document_id = ?", (document_id,))
            c.execute("DELETE FROM kb_versions WHERE document_id = ?", (document_id,))
            c.execute("DELETE FROM kb_documents WHERE id = ?", (document_id,))
            still = {
                sha
                for r in c.execute("SELECT raw_sha, text_sha FROM kb_versions")
                for sha in (r["raw_sha"], r["text_sha"])
            }
        return PurgeResult(chunk_ids=sorted(chunk_ids), version_ids=versions, orphan_blobs=sorted(shas - still))

    def blob_refs(self) -> Set[str]:
        with self._tx() as c:
            return {sha for r in c.execute("SELECT raw_sha, text_sha FROM kb_versions") for sha in (r[0], r[1])}

    # caches and log -------------------------------------------------------------------

    def get_embeddings(self, embedder_fp: str, text_shas: Iterable[str]) -> Dict[str, List[float]]:
        shas = list(dict.fromkeys(text_shas))
        out: Dict[str, List[float]] = {}
        with self._tx() as c:
            for start in range(0, len(shas), 500):
                part = shas[start : start + 500]
                marks = ",".join("?" * len(part))
                for r in c.execute(
                    f"SELECT text_sha, vector FROM kb_embedding_cache WHERE embedder_fp = ? AND text_sha IN ({marks})",
                    [embedder_fp, *part],
                ):
                    out[r["text_sha"]] = array("f", r["vector"]).tolist()
        return out

    def put_embeddings(self, embedder_fp: str, vectors: Dict[str, List[float]]) -> None:
        with self._tx(write=True) as c:
            c.executemany(
                "INSERT OR REPLACE INTO kb_embedding_cache (embedder_fp, text_sha, dim, vector) VALUES (?, ?, ?, ?)",
                [(embedder_fp, sha, len(v), array("f", v).tobytes()) for sha, v in vectors.items()],
            )

    def log_ingest(
        self,
        collection_id: str,
        key: str,
        action: str,
        *,
        document_id: Optional[str] = None,
        version_id: Optional[str] = None,
        stats: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._tx(write=True) as c:
            c.execute(
                "INSERT INTO kb_ingest_log (collection_id, key, document_id, version_id, action, stats, error, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (collection_id, key, document_id, version_id, action, _j(stats or {}), error, _dt(utcnow())),
            )

    def ingest_log(self, collection_id: str, key: Optional[str] = None) -> List[dict]:
        sql = "SELECT * FROM kb_ingest_log WHERE collection_id = ?"
        args: List[Any] = [collection_id]
        if key is not None:
            sql += " AND key = ?"
            args.append(key)
        with self._tx() as c:
            rows = c.execute(sql + " ORDER BY id", args).fetchall()
        return [dict(r) | {"stats": json.loads(r["stats"])} for r in rows]
