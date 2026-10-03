"""The ingest ops: the logic of :func:`operonx_kb.graphs.ingest.build_ingest_graph`.

Every op takes resource keys, not objects, and hands small JSON-safe values
on. The heavy ones (the parsed document, the tree, the chunk lists) are kept
out of traces with ``@op(exclude={"trace": [...]})`` and summarised by a
``stats`` output that ``show_keys`` names.

Order of writes (track5 §11.2): raw bytes go to the blob store when planned;
vector keys are recorded in the catalog ledger, then upserted
(``VectorUpsertOp``) before the catalog commit; the commit flips the active
version in one transaction; vectors of dropped chunks are deleted after
(``VectorDeleteOp``) and only then leave the ledger.
A crash anywhere leaves the catalog consistent, and a retry is idempotent
(upserts by chunk id, commit keyed by version id).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Optional

from operonx import op
from operonx.core.loggings import LOGGER

from operonx_kb.chunking import materialize
from operonx_kb.errors import DocumentParseError, SpanInvariantError
from operonx_kb.model.collection import CollectionSpec
from operonx_kb.model.document import Chunk, Document, DocumentVersion, VersionChunk
from operonx_kb.model.ids import document_id as make_document_id
from operonx_kb.model.ids import sha256_bytes, sha256_text
from operonx_kb.model.ids import vector_id as make_vector_id
from operonx_kb.model.ids import version_id as make_version_id
from operonx_kb.ops._resources import blobs_of, catalog_of, full_key
from operonx_kb.parsing.base import ParsedDoc
from operonx_kb.parsing.router import sniff_mime
from operonx_kb.pipeline import Pipeline, tree_from_dict, tree_to_dict
from operonx_kb.structure.build import build_version
from operonx_kb.text.spans import check_chunks, check_elements

__all__ = [
    "plan_ingest",
    "parse_document",
    "build_tree",
    "chunk_version",
    "stage_index_writes",
    "commit_version",
    "removed_vector_ids",
    "forget_index_writes",
    "skipped",
    "report",
]


def _read(item: Dict[str, Any]) -> bytes:
    if item.get("data") is not None:
        data = item["data"]
        return data.encode("utf-8") if isinstance(data, str) else bytes(data)
    path = item.get("path")
    if not path:
        raise DocumentParseError("an ingest item needs 'path' or 'data'", {"keys": sorted(item)})
    return Path(path).read_bytes()


@op(bound="cpu", exclude={"trace": ["item"]}, show_keys="action")
def plan_ingest(item: dict, collection: str, catalog: str, blobs: str) -> dict:
    """Hash the source, store its bytes, and decide: ``skip``, ``new`` or ``update``.

    ``skip`` when the document's active version has the same bytes and the same
    pipeline (same version id): nothing is parsed or embedded.

    Item keys: ``path`` or ``data``; optional ``key`` (default: the path),
    ``name``, ``mime``, ``title``, ``tags``, ``metadata``.
    """
    cat = catalog_of(catalog)
    coll = cat.get_collection(collection)
    if coll is None:
        raise KeyError(
            f"collection {collection!r} does not exist; create it with KnowledgeBase.create_collection"
        )
    data = _read(item)
    # A file path names the file; DirSource's "name" is only the stem.
    name = Path(item["path"]).name if item.get("path") else item.get("name")
    key = str(item.get("key") or item.get("path") or "")
    if not key:
        raise ValueError("an ingest item given as 'data' needs a 'key'")
    raw_sha = sha256_bytes(data)
    blobs_of(blobs).put(data)
    pipeline = Pipeline(coll.spec)
    mime = item.get("mime") or sniff_mime(data, name) or "application/octet-stream"
    parser = pipeline.parser_for(data, name=name, mime=mime)
    pipeline_fp = pipeline.fingerprint(parser)
    doc_id = make_document_id(collection, key)
    ver_id = make_version_id(doc_id, raw_sha, pipeline_fp)
    existing = cat.get_document(doc_id)
    active = (
        existing.active_version_id if existing is not None and existing.deleted_at is None else None
    )
    action = "skip" if active == ver_id else ("update" if active else "new")
    plan = {
        "collection_id": collection,
        "key": key,
        "name": name,
        "mime": mime,
        "document_id": doc_id,
        "version_id": ver_id,
        "previous_version_id": active,
        "raw_sha": raw_sha,
        "parser": parser.name,
        "pipeline_fp": pipeline_fp,
        "title": item.get("title"),
        "tags": list(item.get("tags") or []),
        "metadata": dict(item.get("metadata") or {}),
        "spec": coll.spec.model_dump(mode="json"),
    }
    return {"plan": plan, "action": action}


@op(bound="cpu", exclude={"trace": ["parsed"]}, show_keys="stats")
def parse_document(plan: dict, blobs: str) -> dict:
    """Read the raw bytes back from the blob store and parse them."""
    data = blobs_of(blobs).get(plan["raw_sha"])
    if data is None:
        raise DocumentParseError(
            "raw bytes vanished from the blob store between plan and parse",
            {"sha": plan["raw_sha"]},
        )
    pipeline = Pipeline(CollectionSpec.model_validate(plan["spec"]))
    started = time.perf_counter()
    parsed = pipeline.parser_named(plan["parser"]).parse(data, name=plan.get("name"))
    stats = {
        "parser": parsed.parser,
        "blocks": len(parsed.blocks),
        "pages": len(parsed.pages),
        "seconds": round(time.perf_counter() - started, 4),
        **parsed.stats,
    }
    return {"parsed": parsed.model_dump(mode="json"), "stats": stats}


@op(bound="cpu", exclude={"trace": ["tree"]}, show_keys="stats")
def build_tree(parsed: dict, plan: dict) -> dict:
    """Element tree, canonical text and spans; the span invariant is checked here."""
    tree = build_version(ParsedDoc.model_validate(parsed), plan["version_id"])
    stats = {
        "elements": len(tree.elements),
        "chars": len(tree.canonical),
        "text_sha": tree.text_sha,
    }
    return {"tree": tree_to_dict(tree), "stats": stats}


@op(bound="cpu", exclude={"trace": ["chunks", "occurrences", "todo"]}, show_keys="stats")
def chunk_version(tree: dict, plan: dict, catalog: str) -> dict:
    """Chunk the version and diff it against the active one.

    ``todo`` holds the chunks the active version does not have: only they are
    embedded and written to the index. Unchanged chunks keep their ids
    (content-addressed), so they are reused as they are.
    """
    vt = tree_from_dict(tree)
    pipeline = Pipeline(CollectionSpec.model_validate(plan["spec"]))
    drafts = pipeline.chunker.draft(vt)
    chunks, occurrences = materialize(
        vt,
        drafts,
        document_id=plan["document_id"],
        version_id=plan["version_id"],
        chunker=pipeline.chunker,
    )
    check_chunks(vt.canonical, occurrences, {c.id: c.content_sha for c in chunks})
    active = (
        catalog_of(catalog).active_chunk_ids(document_id=plan["document_id"])
        if plan["previous_version_id"]
        else set()
    )
    ids = {c.id for c in chunks}
    todo = [c for c in {c.id: c for c in chunks}.values() if c.id not in active]
    stats = {
        "chunks": len(chunks),
        "new": len(todo),
        "reused": len(ids & active),
        "removed": len(active - ids),
    }
    return {
        "chunks": [c.model_dump(mode="json") for c in chunks],
        "occurrences": [o.model_dump(mode="json") for o in occurrences],
        "todo": [c.model_dump(mode="json") for c in todo],
        "stats": stats,
    }


@op(bound="cpu", exclude={"trace": ["vectors", "todo", "ids"]}, show_keys="staged")
def stage_index_writes(
    plan: dict, todo: list, vectors: dict, store: str, collection: str, catalog: str
) -> dict:
    """Record the new chunks' vector keys in the catalog ledger, then hand them to the upsert.

    The ledger row comes first, so the ledger always covers the index: a crash
    after this op and before the upsert leaves a row whose vector is missing,
    which GC deletes harmlessly (deleting an absent id is not an error).
    """
    entries = [(c["id"], make_vector_id(c["id"]), plan["document_id"]) for c in todo]
    catalog_of(catalog).record_index_entries(
        full_key(store, "vector_store"), collection, plan["collection_id"], entries
    )
    return {
        "ids": [vid for _, vid, _ in entries],
        "vectors": [vectors[c["id"]] for c in todo],
        "staged": len(entries),
    }


@op(bound="cpu", exclude={"trace": ["tree", "chunks", "occurrences"]}, show_keys="version")
def commit_version(
    plan: dict,
    tree: dict,
    chunks: list,
    occurrences: list,
    catalog: str,
    blobs: str,
    written: int,
) -> dict:
    """Store the canonical text, re-check the invariant, and flip the active version.

    It runs after the vector upsert; ``written`` is the upsert's count (0 when
    the version brought no new chunk: an empty batch is a no-op upstream).
    """
    vt = tree_from_dict(tree)
    chunk_models = [Chunk.model_validate(c) for c in chunks]
    occ_models = [
        VersionChunk.model_validate({**o, "spans": [tuple(s) for s in o["spans"]]})
        for o in occurrences
    ]
    check_elements(vt.canonical, vt.elements)
    check_chunks(vt.canonical, occ_models, {c.id: c.content_sha for c in chunk_models})
    text_sha = blobs_of(blobs).put(vt.canonical.encode("utf-8"))
    if text_sha != sha256_text(vt.canonical):
        raise SpanInvariantError(
            "blob store returned a different sha for the canonical text", {"sha": text_sha}
        )
    document = Document(
        id=plan["document_id"],
        collection_id=plan["collection_id"],
        key=plan["key"],
        title=plan.get("title") or vt.title,
        mime=plan["mime"],
        tags=plan.get("tags") or [],
        metadata=plan.get("metadata") or {},
    )
    version = DocumentVersion(
        id=plan["version_id"],
        document_id=plan["document_id"],
        ordinal=0,
        raw_sha=plan["raw_sha"],
        text_sha=text_sha,
        pipeline_fp=plan["pipeline_fp"],
        status="committed",
        stats={
            "chunks": len(occ_models),
            "elements": len(vt.elements),
            "pages": len(vt.pages),
            "indexed": written,
        },
    )
    cat = catalog_of(catalog)
    result = cat.commit_version(document, version, vt.elements, vt.pages, chunk_models, occ_models)
    cat.log_ingest(
        plan["collection_id"],
        plan["key"],
        "update" if plan["previous_version_id"] else "new",
        document_id=plan["document_id"],
        version_id=plan["version_id"],
        stats=version.stats,
    )
    LOGGER.info(
        "[kb] committed %s (%s) as %s", plan["key"], plan["collection_id"], plan["version_id"]
    )
    return {
        "version": plan["version_id"],
        "committed": result.committed,
        "removed": result.removed_chunk_ids,
        "stats": {**version.stats, "removed": len(result.removed_chunk_ids)},
    }


@op(bound="cpu", show_keys="ids")
def removed_vector_ids(removed: list) -> dict:
    """The vector keys of the chunks the new version dropped, for ``VectorDeleteOp``."""
    return {"ids": [make_vector_id(cid) for cid in removed]}


@op(bound="cpu", show_keys="forgotten")
def forget_index_writes(
    removed: list, store: str, collection: str, catalog: str, deleted: Optional[int] = None
) -> dict:
    """Drop the ledger rows of vectors just deleted (after the delete, so the ledger still covers the index)."""
    forgotten = catalog_of(catalog).forget_index_entries(
        full_key(store, "vector_store"), collection, removed
    )
    return {"forgotten": forgotten}


@op(bound="cpu", show_keys="result")
def skipped(plan: dict, catalog: str) -> dict:
    """The skip arm: record it and report."""
    catalog_of(catalog).log_ingest(
        plan["collection_id"],
        plan["key"],
        "skip",
        document_id=plan["document_id"],
        version_id=plan["version_id"],
    )
    return {
        "result": {
            "key": plan["key"],
            "action": "skip",
            "document_id": plan["document_id"],
            "version_id": plan["version_id"],
        }
    }


@op(show_keys="result")
def report(
    plan: Optional[dict] = None,
    committed: Optional[dict] = None,
    chunking: Optional[dict] = None,
    embedding: Optional[dict] = None,
    deleted: int = 0,
    skip: Optional[dict] = None,
) -> dict:
    """Where the two arms merge: one result per item."""
    if skip is not None:
        return {"result": skip}
    result: Dict[str, Any] = {
        "key": plan["key"] if plan else None,
        "action": "update" if plan and plan["previous_version_id"] else "new",
        "document_id": plan["document_id"] if plan else None,
        "version_id": plan["version_id"] if plan else None,
        "stats": {
            "chunking": chunking or {},
            "embedding": embedding or {},
            "commit": committed or {},
            "gc_deleted": deleted,
        },
    }
    return {"result": result}
