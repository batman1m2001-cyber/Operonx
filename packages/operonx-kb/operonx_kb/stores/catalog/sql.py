"""The catalog over SQL: one implementation, two dialects (SQLite, Postgres).

Both dialects run the same statements, written with ``?`` placeholders and
portable SQL (``INSERT … ON CONFLICT``, no ``rowid``); a dialect supplies the
connection, the transaction and its locking, the placeholder style and its
migration files. JSON-valued columns are text, times ISO-8601 text, vectors
float32 bytes, so a row reads the same from either database.

Migrations are the numbered ``migrations/<dialect>/NNN_*.sql`` files; the
applied version is in ``kb_schema_version`` and a catalog upgrades itself on
first use (the pattern of operonx's ClickHouse store).
"""

from __future__ import annotations

import json
from abc import abstractmethod
from array import array
from contextlib import contextmanager
from datetime import datetime
from importlib import resources
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

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
from operonx_kb.model.tree import TreeNode
from operonx_kb.stores.catalog.base import (
    ActiveChunk,
    CachedAnswer,
    Catalog,
    CommitResult,
    PurgeResult,
)

__all__ = ["SqlCatalog", "Tx", "migrations"]

_BATCH = 500


def migrations(dialect: str) -> List[Tuple[int, str, List[str]]]:
    """``(version, file name, statements)`` of a dialect's migrations, in order."""
    folder = resources.files(f"operonx_kb.stores.catalog.migrations.{dialect}")
    out = []
    for entry in folder.iterdir():
        name = entry.name
        if name.endswith(".sql") and name[:3].isdigit():
            text = "\n".join(
                line
                for line in entry.read_text(encoding="utf-8").splitlines()
                if not line.strip().startswith("--")
            )
            statements = [s.strip() for s in text.split(";") if s.strip()]
            out.append((int(name[:3]), name, statements))
    return sorted(out)


def _dt(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _path_key(path: str) -> Tuple[int, ...]:
    return tuple(int(p) for p in path.split("."))


class Tx:
    """One transaction: rows come back as dicts, statements use ``?`` placeholders."""

    @abstractmethod
    def rows(self, sql: str, args: Sequence[Any] = ()) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def run(self, sql: str, args: Sequence[Any] = ()) -> int:
        """Execute; return the number of rows changed."""

    @abstractmethod
    def many(self, sql: str, rows: Sequence[Sequence[Any]]) -> None: ...

    def one(self, sql: str, args: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        found = self.rows(sql, args)
        return found[0] if found else None


class SqlCatalog(Catalog):
    """The catalog's logic; subclasses provide :meth:`_transaction` and :attr:`dialect`."""

    dialect: str = ""

    @abstractmethod
    def _transaction(self, write: bool) -> Any:
        """A context manager yielding a :class:`Tx`."""

    def _lock_document(self, tx: Tx, document_id: str) -> None:
        """Serialise writers of one document (SQLite's write transactions already are)."""

    def _lock_migrations(self, tx: Tx) -> None:
        """Serialise catalogs migrating one database at once."""

    @contextmanager
    def _tx(self, write: bool = False) -> Iterator[Tx]:
        with self._transaction(write) as tx:
            yield tx

    # migrations -------------------------------------------------------------------

    def schema_version(self) -> int:
        with self._tx(write=True) as c:
            c.run("CREATE TABLE IF NOT EXISTS kb_schema_version (version INTEGER NOT NULL)")
            row = c.one("SELECT MAX(version) AS v FROM kb_schema_version")
        return int(row["v"] or 0)

    def migrate(self) -> int:
        """Apply pending migrations; return the schema version."""
        current = self.schema_version()
        for version, _, statements in migrations(self.dialect):
            if version <= current:
                continue
            with self._tx(write=True) as c:
                self._lock_migrations(c)
                applied = c.one("SELECT MAX(version) AS v FROM kb_schema_version")["v"] or 0
                if applied >= version:  # another process applied it meanwhile
                    continue
                for statement in statements:
                    c.run(statement)
                c.run("INSERT INTO kb_schema_version (version) VALUES (?)", (version,))
            current = version
        return current

    # collections ----------------------------------------------------------------------

    def put_collection(self, collection: Collection) -> None:
        with self._tx(write=True) as c:
            c.run(
                "INSERT INTO kb_collections (id, spec, tags, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET spec = excluded.spec, tags = excluded.tags",
                (
                    collection.id,
                    collection.spec.model_dump_json(),
                    _j(collection.tags),
                    _dt(utcnow()),
                ),
            )

    @staticmethod
    def _collection(row: Dict[str, Any]) -> Collection:
        return Collection(
            id=row["id"],
            spec=CollectionSpec.model_validate_json(row["spec"]),
            tags=json.loads(row["tags"]),
        )

    def get_collection(self, collection_id: str) -> Optional[Collection]:
        with self._tx() as c:
            row = c.one("SELECT * FROM kb_collections WHERE id = ?", (collection_id,))
        return self._collection(row) if row else None

    def list_collections(self) -> List[Collection]:
        with self._tx() as c:
            return [self._collection(r) for r in c.rows("SELECT * FROM kb_collections ORDER BY id")]

    # documents and versions -----------------------------------------------------------

    @staticmethod
    def _document(row: Dict[str, Any]) -> Document:
        return Document(
            id=row["id"],
            collection_id=row["collection_id"],
            key=row["key"],
            title=row["title"],
            mime=row["mime"],
            tags=json.loads(row["tags"]),
            acl=json.loads(row["acl"]),
            metadata=json.loads(row["metadata"]),
            active_version_id=row["active_version_id"],
            deleted_at=datetime.fromisoformat(row["deleted_at"]) if row["deleted_at"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_document(self, document_id: str) -> Optional[Document]:
        with self._tx() as c:
            row = c.one("SELECT * FROM kb_documents WHERE id = ?", (document_id,))
        return self._document(row) if row else None

    def list_documents(self, collection_id: str, include_deleted: bool = False) -> List[Document]:
        sql = "SELECT * FROM kb_documents WHERE collection_id = ?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        with self._tx() as c:
            return [self._document(r) for r in c.rows(sql + " ORDER BY key", (collection_id,))]

    @staticmethod
    def _version(row: Dict[str, Any]) -> DocumentVersion:
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
            row = c.one("SELECT * FROM kb_versions WHERE id = ?", (version_id,))
        return self._version(row) if row else None

    def list_versions(self, document_id: str) -> List[DocumentVersion]:
        with self._tx() as c:
            rows = c.rows(
                "SELECT * FROM kb_versions WHERE document_id = ? ORDER BY ordinal", (document_id,)
            )
        return [self._version(r) for r in rows]

    def elements(self, version_id: str, canonical: str) -> List[Element]:
        with self._tx() as c:
            rows = c.rows("SELECT * FROM kb_elements WHERE version_id = ?", (version_id,))
        out = []
        # Pre-order is the order of the int-tuple paths: the tree's own order.
        for r in sorted(rows, key=lambda r: _path_key(r["path"])):
            span = (r["span_start"], r["span_end"]) if r["span_start"] is not None else None
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
                    text=canonical[span[0] : span[1]] if span is not None else (r["text"] or ""),
                    span=span,
                    regions=[Region.model_validate(x) for x in json.loads(r["regions"])],
                    attrs=json.loads(r["attrs"]),
                    confidence=r["confidence"],
                )
            )
        return out

    def pages(self, version_id: str) -> List[Page]:
        with self._tx() as c:
            rows = c.rows(
                "SELECT * FROM kb_pages WHERE version_id = ? ORDER BY page_no", (version_id,)
            )
        return [
            Page(
                version_id=version_id,
                page_no=r["page_no"],
                width=r["width"],
                height=r["height"],
                unit=r["unit"],
                image_sha=r["image_sha"],
                text_layer=bool(r["text_layer"]),
            )  # fmt: skip
            for r in rows
        ]

    def version_chunks(self, version_id: str) -> List[VersionChunk]:
        with self._tx() as c:
            rows = c.rows(
                "SELECT * FROM kb_version_chunks WHERE version_id = ? ORDER BY ordinal",
                (version_id,),
            )
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
            for start in range(0, len(ids), _BATCH):
                part = ids[start : start + _BATCH]
                for r in c.rows(
                    f"SELECT * FROM kb_chunks WHERE id IN ({','.join('?' * len(part))})", part
                ):
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

    def active_chunk_ids(
        self, collection_id: Optional[str] = None, document_id: Optional[str] = None
    ) -> Set[str]:
        sql = (
            "SELECT vc.chunk_id AS chunk_id FROM kb_version_chunks vc "
            "JOIN kb_documents d ON d.active_version_id = vc.version_id WHERE d.deleted_at IS NULL"
        )
        args: List[str] = []
        if collection_id is not None:
            sql += " AND d.collection_id = ?"
            args.append(collection_id)
        if document_id is not None:
            sql += " AND d.id = ?"
            args.append(document_id)
        with self._tx() as c:
            return {r["chunk_id"] for r in c.rows(sql, args)}

    def active_chunks(self, collection_id: str, chunk_ids: Sequence[str]) -> Dict[str, ActiveChunk]:
        ids = list(dict.fromkeys(chunk_ids))
        out: Dict[str, ActiveChunk] = {}
        with self._tx() as c:
            for start in range(0, len(ids), _BATCH):
                part = ids[start : start + _BATCH]
                rows = c.rows(
                    "SELECT vc.version_id AS vc_version, vc.ordinal AS vc_ordinal, vc.spans AS vc_spans, "
                    "vc.element_ids AS vc_elements, vc.pages AS vc_pages, ch.id AS ch_id, "
                    "ch.content_sha AS ch_content_sha, ch.kind AS ch_kind, ch.heading_path AS ch_heading_path, "
                    "ch.token_count AS ch_token_count, ch.text AS ch_text, ch.embed_text AS ch_embed_text, "
                    "ch.embed_text_sha AS ch_embed_text_sha, d.* "
                    "FROM kb_version_chunks vc "
                    "JOIN kb_documents d ON d.active_version_id = vc.version_id "
                    "JOIN kb_chunks ch ON ch.id = vc.chunk_id "
                    "WHERE d.collection_id = ? AND d.deleted_at IS NULL "
                    f"AND vc.chunk_id IN ({','.join('?' * len(part))})",
                    [collection_id, *part],
                )
                for r in rows:
                    out[r["ch_id"]] = ActiveChunk(
                        chunk=Chunk(
                            id=r["ch_id"], document_id=r["id"], content_sha=r["ch_content_sha"],
                            kind=r["ch_kind"], heading_path=json.loads(r["ch_heading_path"]),
                            token_count=r["ch_token_count"], text=r["ch_text"],
                            embed_text=r["ch_embed_text"], embed_text_sha=r["ch_embed_text_sha"],
                        ),
                        occurrence=VersionChunk(
                            version_id=r["vc_version"], chunk_id=r["ch_id"], ordinal=r["vc_ordinal"],
                            spans=[tuple(s) for s in json.loads(r["vc_spans"])],
                            element_ids=json.loads(r["vc_elements"]), pages=json.loads(r["vc_pages"]),
                        ),
                        document=self._document(r),
                    )  # fmt: skip
        return out

    # writes -------------------------------------------------------------------------------

    def commit_version(
        self,
        document: Document,
        version: DocumentVersion,
        elements: Sequence[Element],
        pages: Sequence[Page],
        chunks: Sequence[Chunk],
        occurrences: Sequence[VersionChunk],
        nodes: Sequence[TreeNode] = (),
        mentions: Sequence[Tuple[str, str, float]] = (),
    ) -> CommitResult:
        with self._tx(write=True) as c:
            self._lock_document(c, document.id)
            if (
                c.one("SELECT id FROM kb_collections WHERE id = ?", (document.collection_id,))
                is None
            ):
                raise CatalogError(
                    f"collection {document.collection_id!r} does not exist; create it first "
                    "(KnowledgeBase.create_collection)"
                )
            row = c.one("SELECT * FROM kb_documents WHERE id = ?", (document.id,))
            previous = row["active_version_id"] if row and row["deleted_at"] is None else None
            if previous == version.id:
                return CommitResult(committed=False, previous_version_id=previous)
            existing = c.one(
                "SELECT document_id, status FROM kb_versions WHERE id = ?", (version.id,)
            )
            if existing is not None and existing["document_id"] != document.id:
                raise CatalogError(
                    "version id already belongs to another document; ids are content derived, so this is a bug",
                    {
                        "version": version.id,
                        "document": document.id,
                        "owner": existing["document_id"],
                    },
                )
            if row is None:
                c.run(
                    "INSERT INTO kb_documents (id, collection_id, key, title, mime, tags, acl, metadata, "
                    "active_version_id, deleted_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
                    (document.id, document.collection_id, document.key, document.title, document.mime,
                     _j(document.tags), _j(document.acl), _j(document.metadata), _dt(document.created_at)),
                )  # fmt: skip
            else:
                c.run(
                    "UPDATE kb_documents SET title = ?, mime = ?, tags = ?, acl = ?, metadata = ?, "
                    "deleted_at = NULL WHERE id = ?",
                    (document.title, document.mime, _j(document.tags), _j(document.acl),
                     _j(document.metadata), document.id),
                )  # fmt: skip
            if existing is None:
                ordinal = c.one(
                    "SELECT COALESCE(MAX(ordinal), 0) + 1 AS n FROM kb_versions WHERE document_id = ?",
                    (document.id,),
                )["n"]
                c.run(
                    "INSERT INTO kb_versions (id, document_id, ordinal, raw_sha, text_sha, pipeline_fp, status, stats, "
                    "error, created_at) VALUES (?, ?, ?, ?, ?, ?, 'committed', ?, NULL, ?)",
                    (version.id, document.id, ordinal, version.raw_sha, version.text_sha, version.pipeline_fp,
                     _j(version.stats), _dt(version.created_at)),
                )  # fmt: skip
                c.many(
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
                c.many(
                    "INSERT INTO kb_pages (version_id, page_no, width, height, unit, image_sha, text_layer) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            version.id,
                            p.page_no,
                            p.width,
                            p.height,
                            p.unit,
                            p.image_sha,
                            int(p.text_layer),
                        )
                        for p in pages
                    ],
                )
                c.many(
                    "INSERT INTO kb_chunks (id, document_id, content_sha, kind, heading_path, token_count, "
                    "text, embed_text, embed_text_sha) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
                    [(ch.id, ch.document_id, ch.content_sha, ch.kind, _j(ch.heading_path), ch.token_count, ch.text,
                      ch.embed_text, ch.embed_text_sha) for ch in chunks],
                )  # fmt: skip
                c.many(
                    "INSERT INTO kb_version_chunks (version_id, chunk_id, ordinal, spans, element_ids, pages) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [(version.id, o.chunk_id, o.ordinal, _j([list(s) for s in o.spans]), _j(o.element_ids), _j(o.pages))
                     for o in occurrences],
                )  # fmt: skip
                c.many(
                    "INSERT INTO kb_tree_nodes (version_id, id, parent_id, path, ordinal, depth, title, span_start, "
                    "span_end, pages, source, summary, summary_sha) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(version.id, n.id, n.parent_id, n.path, n.ordinal, n.depth, n.title, n.span[0], n.span[1],
                      _j(n.pages), n.source, n.summary, n.summary_sha) for n in nodes],
                )  # fmt: skip
                c.many(
                    "INSERT INTO kb_graph_mentions (version_id, chunk_id, concept, weight) VALUES (?, ?, ?, ?)",
                    [(version.id, chunk_id, concept, float(weight)) for chunk_id, concept, weight in mentions],
                )  # fmt: skip
            else:
                c.run("UPDATE kb_versions SET status = 'committed' WHERE id = ?", (version.id,))
            if previous is not None:
                c.run("UPDATE kb_versions SET status = 'superseded' WHERE id = ?", (previous,))
            c.run(
                "UPDATE kb_documents SET active_version_id = ? WHERE id = ?",
                (version.id, document.id),
            )
            old = (
                {
                    r["chunk_id"]
                    for r in c.rows(
                        "SELECT chunk_id FROM kb_version_chunks WHERE version_id = ?", (previous,)
                    )
                }
                if previous
                else set()
            )
            new = {o.chunk_id for o in occurrences}
        return CommitResult(
            committed=True,
            previous_version_id=previous,
            removed_chunk_ids=sorted(old - new),
            added_chunk_ids=sorted(new - old),
        )

    def update_document(
        self, document_id: str, *, tags: Sequence[str], acl: Sequence[str], metadata: dict
    ) -> bool:
        with self._tx(write=True) as c:
            self._lock_document(c, document_id)
            row = c.one("SELECT tags, acl, metadata FROM kb_documents WHERE id = ?", (document_id,))
            if row is None:
                raise CatalogError(f"no document {document_id!r}")
            new = (_j(list(tags)), _j(list(acl)), _j(dict(metadata)))
            if (json.loads(row["tags"]), json.loads(row["acl"]), json.loads(row["metadata"])) == (
                list(tags),
                list(acl),
                dict(metadata),
            ):
                return False
            c.run(
                "UPDATE kb_documents SET tags = ?, acl = ?, metadata = ? WHERE id = ?",
                (*new, document_id),
            )
        return True

    def tombstone(self, document_id: str) -> List[str]:
        with self._tx(write=True) as c:
            self._lock_document(c, document_id)
            row = c.one("SELECT active_version_id FROM kb_documents WHERE id = ?", (document_id,))
            if row is None:
                raise CatalogError(f"no document {document_id!r}")
            active = row["active_version_id"]
            c.run(
                "UPDATE kb_documents SET active_version_id = NULL, deleted_at = ? WHERE id = ?",
                (_dt(utcnow()), document_id),
            )
            if active is None:
                return []
            c.run("UPDATE kb_versions SET status = 'superseded' WHERE id = ?", (active,))
            rows = c.rows("SELECT chunk_id FROM kb_version_chunks WHERE version_id = ?", (active,))
        return sorted({r["chunk_id"] for r in rows})

    def purge(self, document_id: str) -> PurgeResult:
        with self._tx(write=True) as c:
            self._lock_document(c, document_id)
            vrows = c.rows(
                "SELECT id, raw_sha, text_sha FROM kb_versions WHERE document_id = ?",
                (document_id,),
            )
            versions = [r["id"] for r in vrows]
            shas = {sha for r in vrows for sha in (r["raw_sha"], r["text_sha"])}
            chunk_ids = [
                r["id"]
                for r in c.rows("SELECT id FROM kb_chunks WHERE document_id = ?", (document_id,))
            ]
            for table in (
                "kb_version_chunks",
                "kb_elements",
                "kb_pages",
                "kb_tree_nodes",
                "kb_graph_mentions",
            ):
                c.many(f"DELETE FROM {table} WHERE version_id = ?", [(v,) for v in versions])
            c.run("DELETE FROM kb_chunks WHERE document_id = ?", (document_id,))
            c.run("DELETE FROM kb_versions WHERE document_id = ?", (document_id,))
            c.run("DELETE FROM kb_documents WHERE id = ?", (document_id,))
            still = self._blob_refs(c)
        return PurgeResult(
            chunk_ids=sorted(chunk_ids), version_ids=versions, orphan_blobs=sorted(shas - still)
        )

    @staticmethod
    def _blob_refs(c: Tx) -> Set[str]:
        return {
            sha
            for r in c.rows("SELECT raw_sha, text_sha FROM kb_versions")
            for sha in (r["raw_sha"], r["text_sha"])
        }

    def blob_refs(self) -> Set[str]:
        with self._tx() as c:
            return self._blob_refs(c)

    # the derived-index ledger ---------------------------------------------------------

    def record_index_entries(
        self,
        store: str,
        collection: str,
        collection_id: str,
        entries: Sequence[Tuple[str, int, str]],
    ) -> None:
        if not entries:
            return
        with self._tx(write=True) as c:
            vids = [vid for _, vid, _ in entries]
            mine = {vid: cid for cid, vid, _ in entries}
            for start in range(0, len(vids), _BATCH):
                part = vids[start : start + _BATCH]
                for row in c.rows(
                    "SELECT chunk_id, vector_id FROM kb_index_entries WHERE store = ? AND collection = ? "
                    f"AND vector_id IN ({','.join('?' * len(part))})",
                    [store, collection, *part],
                ):
                    if row["chunk_id"] != mine[row["vector_id"]]:
                        raise CatalogError(
                            "two chunks map to the same vector key; writing would overwrite the other chunk's vector",
                            {"store": store, "vector_id": row["vector_id"], "chunk": mine[row["vector_id"]],
                             "holder": row["chunk_id"]},
                        )  # fmt: skip
            c.many(
                "INSERT INTO kb_index_entries (store, collection, chunk_id, vector_id, document_id, collection_id) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (store, collection, chunk_id) DO NOTHING",
                [(store, collection, cid, vid, doc, collection_id) for cid, vid, doc in entries],
            )

    def forget_index_entries(self, store: str, collection: str, chunk_ids: Sequence[str]) -> int:
        ids = list(dict.fromkeys(chunk_ids))
        removed = 0
        with self._tx(write=True) as c:
            for start in range(0, len(ids), _BATCH):
                part = ids[start : start + _BATCH]
                removed += c.run(
                    "DELETE FROM kb_index_entries WHERE store = ? AND collection = ? "
                    f"AND chunk_id IN ({','.join('?' * len(part))})",
                    [store, collection, *part],
                )
        return removed

    def index_entries(
        self,
        store: str,
        collection: str,
        *,
        collection_id: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> Dict[str, int]:
        sql = "SELECT chunk_id, vector_id FROM kb_index_entries WHERE store = ? AND collection = ?"
        args: List[Any] = [store, collection]
        if collection_id is not None:
            sql += " AND collection_id = ?"
            args.append(collection_id)
        if document_id is not None:
            sql += " AND document_id = ?"
            args.append(document_id)
        with self._tx() as c:
            return {r["chunk_id"]: int(r["vector_id"]) for r in c.rows(sql, args)}

    def chunks_for_keys(
        self, store: str, collection: str, collection_id: str, keys: Sequence[int]
    ) -> Dict[int, str]:
        wanted = list(dict.fromkeys(int(k) for k in keys))
        out: Dict[int, str] = {}
        with self._tx() as c:
            for start in range(0, len(wanted), _BATCH):
                part = wanted[start : start + _BATCH]
                for r in c.rows(
                    "SELECT chunk_id, vector_id FROM kb_index_entries WHERE store = ? AND collection = ? "
                    f"AND collection_id = ? AND vector_id IN ({','.join('?' * len(part))})",
                    [store, collection, collection_id, *part],
                ):
                    out[int(r["vector_id"])] = r["chunk_id"]
        return out

    # caches and log ---------------------------------------------------------------------

    def get_embeddings(self, embedder_fp: str, text_shas: Iterable[str]) -> Dict[str, List[float]]:
        shas = list(dict.fromkeys(text_shas))
        out: Dict[str, List[float]] = {}
        with self._tx() as c:
            for start in range(0, len(shas), _BATCH):
                part = shas[start : start + _BATCH]
                for r in c.rows(
                    "SELECT text_sha, vector FROM kb_embedding_cache WHERE embedder_fp = ? "
                    f"AND text_sha IN ({','.join('?' * len(part))})",
                    [embedder_fp, *part],
                ):
                    out[r["text_sha"]] = array("f", bytes(r["vector"])).tolist()
        return out

    def put_embeddings(self, embedder_fp: str, vectors: Dict[str, List[float]]) -> None:
        with self._tx(write=True) as c:
            c.many(
                "INSERT INTO kb_embedding_cache (embedder_fp, text_sha, dim, vector) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (embedder_fp, text_sha) DO UPDATE SET dim = excluded.dim, vector = excluded.vector",
                [(embedder_fp, sha, len(v), array("f", v).tobytes()) for sha, v in vectors.items()],
            )

    def get_enrichments(
        self, enricher_fp: str, input_shas: Iterable[str]
    ) -> Dict[str, CachedAnswer]:
        shas = list(dict.fromkeys(input_shas))
        out: Dict[str, CachedAnswer] = {}
        with self._tx() as c:
            for start in range(0, len(shas), _BATCH):
                part = shas[start : start + _BATCH]
                for r in c.rows(
                    "SELECT * FROM kb_enrichment_cache WHERE enricher_fp = ? "
                    f"AND input_sha IN ({','.join('?' * len(part))})",
                    [enricher_fp, *part],
                ):
                    out[r["input_sha"]] = CachedAnswer(
                        value=json.loads(r["value"]), model=r["model"],
                        prompt_tokens=r["prompt_tokens"], completion_tokens=r["completion_tokens"],
                        cached_tokens=r["cached_tokens"], cost_usd=r["cost_usd"],
                    )  # fmt: skip
        return out

    def put_enrichments(
        self, enricher_fp: str, kind: str, answers: Dict[str, CachedAnswer]
    ) -> None:
        with self._tx(write=True) as c:
            c.many(
                "INSERT INTO kb_enrichment_cache (enricher_fp, input_sha, kind, value, model, prompt_tokens, "
                "completion_tokens, cached_tokens, cost_usd, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (enricher_fp, input_sha) DO NOTHING",
                [(enricher_fp, sha, kind, _j(a.value), a.model, a.prompt_tokens, a.completion_tokens,
                  a.cached_tokens, a.cost_usd, _dt(utcnow())) for sha, a in answers.items()],
            )  # fmt: skip

    def tree_nodes(self, version_id: str) -> List[TreeNode]:
        with self._tx() as c:
            rows = c.rows("SELECT * FROM kb_tree_nodes WHERE version_id = ?", (version_id,))
        nodes = [
            TreeNode(
                id=r["id"],
                version_id=r["version_id"],
                path=r["path"],
                parent_id=r["parent_id"],
                ordinal=r["ordinal"],
                depth=r["depth"],
                title=r["title"],
                span=(r["span_start"], r["span_end"]),
                pages=json.loads(r["pages"]),
                source=r["source"],
                summary=r["summary"],
                summary_sha=r["summary_sha"],
            )  # fmt: skip
            for r in rows
        ]
        return sorted(nodes, key=lambda n: _path_key(n.path))

    def active_version_ids(self, collection_id: str) -> List[str]:
        with self._tx() as c:
            rows = c.rows(
                "SELECT active_version_id FROM kb_documents WHERE collection_id = ? "
                "AND deleted_at IS NULL AND active_version_id IS NOT NULL",
                (collection_id,),
            )
        return sorted(r["active_version_id"] for r in rows)

    def graph_mentions(self, collection_id: str) -> List[Tuple[str, str, str, float]]:
        with self._tx() as c:
            rows = c.rows(
                "SELECT m.chunk_id AS chunk_id, d.id AS document_id, m.concept AS concept, "
                "m.weight AS weight "
                "FROM kb_graph_mentions m JOIN kb_documents d ON d.active_version_id = m.version_id "
                "WHERE d.collection_id = ? AND d.deleted_at IS NULL ORDER BY m.chunk_id, m.concept",
                (collection_id,),
            )
        return [(r["chunk_id"], r["document_id"], r["concept"], float(r["weight"])) for r in rows]

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
            c.run(
                "INSERT INTO kb_ingest_log (collection_id, key, document_id, version_id, action, stats, error, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    collection_id,
                    key,
                    document_id,
                    version_id,
                    action,
                    _j(stats or {}),
                    error,
                    _dt(utcnow()),
                ),
            )

    def ingest_log(self, collection_id: str, key: Optional[str] = None) -> List[dict]:
        sql = "SELECT * FROM kb_ingest_log WHERE collection_id = ?"
        args: List[Any] = [collection_id]
        if key is not None:
            sql += " AND key = ?"
            args.append(key)
        with self._tx() as c:
            rows = c.rows(sql + " ORDER BY id", args)
        return [dict(r) | {"stats": json.loads(r["stats"])} for r in rows]
