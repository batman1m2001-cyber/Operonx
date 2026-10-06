"""Delete, GC and rebuild graphs (wiring only; the logic is in :mod:`operonx_kb.ops.maintenance`
and :mod:`operonx_kb.ops.lexical`).

::

    delete:          tombstone_document ─► VectorDeleteOp ─► delete_document_lexical ─► finish_delete
    gc:              find_stale_entries ─► VectorDeleteOp ─► forget_index_writes ─► collect_lexical
                     ─► collect_blobs ─► gc_report
    rebuild:         plan_rebuild ─► embed_chunks (cache) ─► stage_index_writes ─► VectorUpsertOp
                     ─► finish_rebuild
    drop:            find_stale_entries(everything) ─► VectorDeleteOp ─► forget_index_writes
    rebuild_lexical: plan_rebuild ─► write_lexical ─► finish_lexical_rebuild
    drop_lexical:    collect_lexical(everything)

The lexical ops do nothing when the collection has no lexical index. Every graph is
defined once, at module level (operonx guide 05): delete and GC read the collection's
current indexes with ``index_settings``; rebuild and drop take the index they act on
(a new generation, or an old one) as inputs.
"""

from __future__ import annotations

from operonx import END, START, graph
from operonx.providers.ops import VectorDeleteOp, VectorUpsertOp

from operonx_kb.ops import embed_chunks, forget_index_writes, stage_index_writes
from operonx_kb.ops.lexical import (
    collect_lexical,
    delete_document_lexical,
    finish_lexical_rebuild,
    write_lexical,
)
from operonx_kb.ops.maintenance import (
    collect_blobs,
    find_stale_entries,
    finish_delete,
    finish_rebuild,
    gc_report,
    plan_rebuild,
    tombstone_document,
)
from operonx_kb.ops.settings import index_settings

__all__ = [
    "delete_document",
    "collect_garbage",
    "rebuild_index",
    "drop_index",
    "rebuild_lexical",
    "drop_lexical",
]


@graph
def delete_document(collection, key, purge, catalog, blobs):
    """Delete one document from the catalog and the collection's indexes."""
    s = index_settings(collection=collection, catalog=catalog)
    tomb = tombstone_document(collection=collection, key=key, store=s["store"],
                              vcollection=s["vcollection"], catalog=catalog)  # fmt: skip
    delete = VectorDeleteOp.of(resource=s["store"], ids=tomb["vector_ids"],
                               collection=s["vector_collection"])  # fmt: skip
    lex = delete_document_lexical(document_id=tomb["document_id"], catalog=catalog,
                                  lexical=s["lexical"])  # fmt: skip
    finish = finish_delete(
        collection=collection,
        key=key,
        document_id=tomb["document_id"],
        chunk_ids=tomb["chunk_ids"],
        purge=purge,
        store=s["store"],
        vcollection=s["vcollection"],
        catalog=catalog,
        blobs=blobs,
        deleted=delete["deleted"],
        lexical=s["lexical"],
        lexical_deleted=lex["deleted"],
    )
    START >> s >> tomb >> delete >> lex >> finish >> END


@graph
def collect_garbage(collection, blobs_enabled, grace_seconds, catalog, blobs):
    """Garbage-collect one collection's indexes (and, with ``blobs_enabled``, blobs)."""
    s = index_settings(collection=collection, catalog=catalog)
    stale = find_stale_entries(collection=collection, store=s["store"],
                               vcollection=s["vcollection"], catalog=catalog)  # fmt: skip
    delete = VectorDeleteOp.of(resource=s["store"], ids=stale["vector_ids"],
                               collection=s["vector_collection"])  # fmt: skip
    forget = forget_index_writes(removed=stale["chunk_ids"], store=s["store"],
                                 collection=s["vcollection"], catalog=catalog,
                                 deleted=delete["deleted"])  # fmt: skip
    lex = collect_lexical(collection=collection, catalog=catalog, lexical=s["lexical"])
    sweep = collect_blobs(enabled=blobs_enabled, grace_seconds=grace_seconds, catalog=catalog,
                          blobs=blobs)  # fmt: skip
    summary = gc_report(stale=stale["count"], deleted=delete["deleted"],
                        forgotten=forget["forgotten"], blobs_deleted=sweep["blobs_deleted"],
                        lexical_deleted=lex["deleted"])  # fmt: skip
    START >> s >> stale >> delete >> forget >> lex >> sweep >> summary >> END


@graph
def rebuild_index(collection, switch, embedder, store, vector_collection, vcollection,
                  batch_size, passage_template, catalog):  # fmt: skip
    """Rebuild a collection's dense index into ``store``/``vector_collection`` from the
    catalog alone: no parsing, and embeddings come from the cache when the embedder is
    unchanged. ``switch`` makes it the collection's index."""
    plan = plan_rebuild(collection=collection, catalog=catalog)
    embed = embed_chunks(chunks=plan["chunks"], embedder=embedder, catalog=catalog,
                         batch_size=batch_size, template=passage_template)  # fmt: skip
    stage = stage_index_writes(
        todo=plan["chunks"],
        vectors=embed["vectors"],
        store=store,
        vcollection=vcollection,
        collection=collection,
        catalog=catalog,
        payloads=plan["payloads"],
    )
    upsert = VectorUpsertOp.of(resource=store, ids=stage["ids"], vectors=stage["vectors"],
                               metadata=stage["metadata"], collection=vector_collection)  # fmt: skip
    finish = finish_rebuild(collection=collection, store=store, vcollection=vcollection,
                            switch=switch, catalog=catalog, chunks=plan["count"],
                            upserted=upsert["upserted"])  # fmt: skip
    START >> plan >> embed >> stage >> upsert >> finish >> END


@graph
def drop_index(collection, store, vector_collection, vcollection, catalog):
    """Delete every vector the ledger records for a collection in ``store`` (an old
    index generation)."""
    entries = find_stale_entries(collection=collection, store=store, vcollection=vcollection,
                                 catalog=catalog, everything=True)  # fmt: skip
    delete = VectorDeleteOp.of(resource=store, ids=entries["vector_ids"],
                               collection=vector_collection)  # fmt: skip
    forget = forget_index_writes(removed=entries["chunk_ids"], store=store,
                                 collection=vcollection, catalog=catalog,
                                 deleted=delete["deleted"])  # fmt: skip
    START >> entries >> delete >> forget >> END
    delete >> END  # its count is part of the result


@graph
def rebuild_lexical(collection, switch, lexical, catalog):
    """Rebuild a collection's lexical index into ``lexical`` (another table, or another
    analyzer) from the catalog alone."""
    plan = plan_rebuild(collection=collection, catalog=catalog)
    write = write_lexical(todo=plan["chunks"], payloads=plan["payloads"], collection=collection,
                          catalog=catalog, lexical=lexical)  # fmt: skip
    finish = finish_lexical_rebuild(collection=collection, lexical=lexical, switch=switch,
                                    catalog=catalog, chunks=plan["count"],
                                    upserted=write["written"])  # fmt: skip
    START >> plan >> write >> finish >> END


@graph
def drop_lexical(collection, lexical, catalog):
    """Delete every entry the ledger records for a collection in ``lexical`` (an old
    generation)."""
    drop = collect_lexical(collection=collection, catalog=catalog, lexical=lexical,
                           everything=True)  # fmt: skip
    START >> drop >> END
