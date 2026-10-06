"""Lexical index ops: the lexical half of ingest, delete, GC and rebuild (PLAN R1).

They mirror the dense path's order of writes. A chunk's key is recorded in the
catalog ledger **before** its entry is written, and an entry is deleted
**before** its ledger row is forgotten, so the ledger always covers the index
and GC can always find what to remove. Keys are the dense index's keys
(:func:`~operonx_kb.model.ids.vector_id`).

Each op takes the collection's lexical spec as a dict (it is traced) and does
nothing when it is ``None``: a collection without a lexical index runs the
same graphs.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from operonx import op

from operonx_kb.errors import CatalogError
from operonx_kb.model.collection import LexicalIndexSpec
from operonx_kb.model.ids import vector_id
from operonx_kb.ops._resources import catalog_of, full_key, lexical_of
from operonx_kb.text.analyze import Analyzer

__all__ = [
    "write_lexical",
    "delete_lexical",
    "delete_document_lexical",
    "collect_lexical",
    "finish_lexical_rebuild",
]


def _target(lexical: Dict[str, Any]) -> Tuple[LexicalIndexSpec, str, str]:
    """``(spec, ledger store key, ledger collection)`` of a lexical spec dump."""
    spec = LexicalIndexSpec.model_validate(lexical)
    return spec, full_key(spec.index, "kb_lexical"), spec.collection or ""


@op(bound="cpu", exclude={"trace": ["todo", "payloads"]}, show_keys="written")
def write_lexical(
    todo: list, payloads: dict, collection: str, catalog: str, lexical: Optional[dict] = None
) -> dict:
    """Write chunks to the lexical index: ledger rows first, then analyzed text and payload.

    Args:
        todo: Chunk dumps to write (their ``embed_text`` is what is indexed: the
            heading path and the text).
        payloads: ``document_id -> payload`` (:func:`~operonx_kb.model.filter.index_payload`).
        collection: The KB collection id.
    """
    if lexical is None or not todo:
        return {"written": 0}
    spec, store, table = _target(lexical)
    entries = [(c["id"], vector_id(c["id"]), c["document_id"]) for c in todo]
    catalog_of(catalog).record_index_entries(store, table, collection, entries)
    analyzer = Analyzer(spec.analyzer)
    written = lexical_of(spec.index).upsert(
        [key for _, key, _ in entries],
        [analyzer.tokens(c["embed_text"]) for c in todo],
        [payloads[c["document_id"]] for c in todo],
        spec.collection,
    )
    return {"written": written}


def _delete(spec: LexicalIndexSpec, store: str, table: str, catalog: str, chunk_keys: dict) -> int:
    deleted = lexical_of(spec.index).delete(list(chunk_keys.values()), spec.collection)
    catalog_of(catalog).forget_index_entries(store, table, list(chunk_keys))
    return deleted


@op(bound="cpu", show_keys="deleted")
def delete_lexical(chunk_ids: list, catalog: str, lexical: Optional[dict] = None) -> dict:
    """Delete the entries of chunks a new version dropped, then their ledger rows."""
    if lexical is None or not chunk_ids:
        return {"deleted": 0}
    spec, store, table = _target(lexical)
    return {"deleted": _delete(spec, store, table, catalog, {c: vector_id(c) for c in chunk_ids})}


@op(bound="cpu", show_keys="deleted")
def delete_document_lexical(document_id: str, catalog: str, lexical: Optional[dict] = None) -> dict:
    """Delete every entry the ledger records for a document, then those rows."""
    if lexical is None:
        return {"deleted": 0}
    spec, store, table = _target(lexical)
    entries = catalog_of(catalog).index_entries(store, table, document_id=document_id)
    return {"deleted": _delete(spec, store, table, catalog, entries)}


@op(bound="cpu", show_keys="deleted")
def collect_lexical(
    collection: str, catalog: str, lexical: Optional[dict] = None, everything: bool = False
) -> dict:
    """Delete the collection's entries no active version holds (with ``everything``, all:
    dropping a whole lexical generation)."""
    if lexical is None:
        return {"stale": 0, "deleted": 0}
    spec, store, table = _target(lexical)
    cat = catalog_of(catalog)
    entries = cat.index_entries(store, table, collection_id=collection)
    live = set() if everything else cat.active_chunk_ids(collection_id=collection)
    stale = {c: k for c, k in entries.items() if c not in live}
    return {"stale": len(stale), "deleted": _delete(spec, store, table, catalog, stale)}


@op(bound="cpu", show_keys="report")
def finish_lexical_rebuild(
    collection: str, lexical: dict, switch: bool, catalog: str, chunks: int = 0, upserted: int = 0
) -> dict:
    """With ``switch``, make the rebuilt lexical index the collection's (the alias flip)."""
    cat = catalog_of(catalog)
    coll = cat.get_collection(collection)
    if coll is None:
        raise CatalogError(f"no collection {collection!r}")
    previous = coll.spec.lexical
    target = LexicalIndexSpec.model_validate(lexical)
    if switch:
        cat.put_collection(
            coll.model_copy(update={"spec": coll.spec.model_copy(update={"lexical": target})})
        )
    return {
        "report": {
            "chunks": chunks,
            "upserted": upserted,
            "index": full_key(target.index, "kb_lexical"),
            "collection": target.collection or "",
            "switched": switch,
            "previous": previous.model_dump(mode="json") if previous else None,
        }
    }
