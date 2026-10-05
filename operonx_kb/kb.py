"""The library API: :class:`KnowledgeBase` (track5 §14.1).

A thin layer over the resources and the graphs — ``add``, ``delete`` and ``gc``
run the same operonx graphs a Job or a service runs, so a script's ingest is
traced exactly like a served one.

    import operonx, operonx_kb
    operonx.bootstrap(resources="resources.yaml")
    kb = operonx_kb.KnowledgeBase()
    kb.create_collection("handbook", CollectionSpec(dense=DenseIndexSpec(embedder="bge-m3", store="kb")))
    result = await kb.add("handbook", "raw/policy.pdf", key="policy")
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from operonx import Operon

from operonx_kb.errors import CatalogError, KBError
from operonx_kb.graphs.answer import answer_graph
from operonx_kb.graphs.ingest import build_ingest_graph
from operonx_kb.graphs.maintenance import (
    build_delete_graph,
    build_drop_index_graph,
    build_gc_graph,
    build_lexical_drop_graph,
    build_lexical_rebuild_graph,
    build_rebuild_graph,
)
from operonx_kb.graphs.retrieve import (
    dense_retriever,
    graph_retriever,
    hybrid_retriever,
    lexical_retriever,
    reranked,
    search_graph,
    tree_retriever,
)
from operonx_kb.maintenance import VerifyReport, verify
from operonx_kb.model.collection import (
    Collection,
    CollectionSpec,
    DenseIndexSpec,
    LexicalIndexSpec,
)
from operonx_kb.model.document import Document
from operonx_kb.model.filter import KBFilter
from operonx_kb.model.ids import document_id
from operonx_kb.ops._resources import blobs_of, catalog_of, full_key

__all__ = ["KnowledgeBase", "IngestError", "QueryError", "MODES", "DEFAULT_MODE"]

#: Retrieval modes of :meth:`KnowledgeBase.search`. ``tree`` needs the collection's
#: ``tree`` spec (PLAN E7), ``graph`` its ``graph`` spec (PLAN G4); both are seeded
#: by the collection's default mode.
MODES = ("dense", "lexical", "hybrid", "tree", "graph")
#: The mode a search uses unless told otherwise, for a collection that has both a
#: dense and a lexical index. Hybrid beat dense on Recall@10 on all three K2 eval
#: sets (the PLAN gate asks for two of three; the table is in ``docs/bench/k2.md``).
#: A collection with only one index uses that one (:meth:`KnowledgeBase.default_mode`).
DEFAULT_MODE = "hybrid"


class IngestError(KBError):
    """A KB graph run failed; the message holds the failing op and its error."""


class QueryError(KBError):
    """A search or an answer run failed; the message holds the failing op and its error."""


_EXCEPTION_LINE = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt)\b.*")


def _exception_text(traceback: str) -> str:
    """The exception a traceback ends with, all of its message lines joined.

    A KB error's message runs over several lines (the message, then one context
    fact per line); the last line alone would be a context fact without the error.
    """
    lines = [line for line in traceback.strip().splitlines() if line.strip()]
    for i in range(len(lines) - 1, -1, -1):
        if _EXCEPTION_LINE.match(lines[i]):
            return " ".join(line.strip() for line in lines[i:])
    return lines[-1].strip() if lines else traceback


class KnowledgeBase:
    """A catalog, a blob store and the collections in them.

    Args:
        catalog: ``kb_catalog`` resource key.
        blobs: ``kb_blob`` resource key.
        trace: Passed to ``Operon(trace=...)`` for every run.
    """

    def __init__(
        self, catalog: str = "kb_catalog:main", blobs: str = "kb_blob:main", trace: Any = None
    ):
        self.catalog_key = catalog
        self.blobs_key = blobs
        self.trace = trace
        self.catalog = catalog_of(catalog)
        self.blobs = blobs_of(blobs)
        self._engines: Dict[Tuple[str, str, str], Operon] = {}

    # collections -----------------------------------------------------------------

    def create_collection(
        self, collection_id: str, spec: Optional[CollectionSpec] = None
    ) -> Collection:
        """Create a collection, or update its spec."""
        collection = Collection(id=collection_id, spec=spec or CollectionSpec())
        self.catalog.put_collection(collection)
        self._engines = {k: v for k, v in self._engines.items() if k[1] != collection_id}
        return collection

    def collection(self, collection_id: str) -> Collection:
        found = self.catalog.get_collection(collection_id)
        if found is None:
            raise CatalogError(
                f"no collection {collection_id!r}; create it with create_collection()"
            )
        return found

    def _dense(self, collection_id: str) -> DenseIndexSpec:
        dense = self.collection(collection_id).spec.dense
        if dense is None:
            raise CatalogError(
                f"collection {collection_id!r} has no dense index; set CollectionSpec(dense=...)"
            )
        return dense

    def _engine(
        self, kind: str, collection_id: str, factory: Callable[[], Any], params: Sequence[str],
        config: str,
    ) -> Operon:  # fmt: skip
        key = (kind, collection_id, config)
        if key not in self._engines:
            self._engines[key] = Operon(
                factory(), params={**{p: None for p in params}, "name": kind}, trace=self.trace
            )
        return self._engines[key]

    async def _run_graph(
        self,
        kind: str,
        collection_id: str,
        factory: Callable[[], Any],
        config: str,
        inputs: Dict[str, Any],
        output: Optional[str],
        error: type = IngestError,
        required: Sequence[str] = (),
        trace_id: Optional[str] = None,
    ) -> Any:
        """Run a graph; return ``out[output]``, or every output when ``output`` is ``None``.
        ``trace_id`` is the run's id (default: a new one).

        Raises:
            error: An op failed, or an expected output is missing.
        """
        engine = self._engine(kind, collection_id, factory, list(inputs), config)
        out = await engine.run(inputs=inputs, trace_id=trace_id)
        expected = [*required, *([output] if output is not None else [])]
        if "$errors" in out or any(name not in out for name in expected):
            errors = out.get("$errors") or {kind: {"message": "the run produced no result"}}
            # Records arrive in the order the ops failed: the first is the cause, the
            # others are ops downstream of it that ran without its outputs.
            op, record = next(iter(errors.items()))
            text = record.get("message", "") if isinstance(record, dict) else str(record)
            raise error(
                f"{kind} in {collection_id!r} failed in {op}: {_exception_text(text)}",
                {"ops": sorted(errors)},
            )
        return out[output] if output is not None else out

    async def _run(
        self,
        kind: str,
        collection_id: str,
        build,
        inputs: Dict[str, Any],
        output: Optional[str],
        dense: Optional[DenseIndexSpec] = None,
    ) -> Any:
        """Run a graph built from the collection's dense and lexical index specs."""
        dense = dense or self._dense(collection_id)
        lexical = self.collection(collection_id).spec.lexical
        config = dense.model_dump_json() + (lexical.model_dump_json() if lexical else "")

        def factory():
            return build(dense, lexical=lexical, catalog=self.catalog_key, blobs=self.blobs_key)

        return await self._run_graph(kind, collection_id, factory, config, inputs, output)

    # ingest ------------------------------------------------------------------------

    async def add(
        self,
        collection_id: str,
        path: Optional[str] = None,
        *,
        data: Optional[bytes] = None,
        key: Optional[str] = None,
        name: Optional[str] = None,
        mime: Optional[str] = None,
        title: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
        acl: Optional[Sequence[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Ingest one document; return the run's result (``action``: new, update or skip).

        Args:
            acl: Principals allowed to read the document (``KBFilter.acl_any``).
            metadata: Free-form; the fields the collection declares ``filterable``
                are copied into its index entries and must have the declared type.

        Raises:
            IngestError: An op failed. The failure is also recorded in the ingest log.
        """
        item: Dict[str, Any] = {"path": str(path) if path is not None else None, "data": data, "key": key, "name": name,
                                "mime": mime, "title": title, "tags": list(tags or []), "acl": list(acl or []),
                                "metadata": metadata or {}}  # fmt: skip
        doc_key = key or (str(path) if path is not None else "")
        dense, spec = self._dense(collection_id), self.collection(collection_id).spec

        def factory():
            return build_ingest_graph(dense, lexical=spec.lexical, contextual=spec.contextual,
                                      tree=spec.tree, catalog=self.catalog_key, blobs=self.blobs_key)  # fmt: skip

        try:
            return await self._run_graph("ingest_document", collection_id, factory, spec.model_dump_json(),
                                         {"item": item, "collection": collection_id}, "result")  # fmt: skip
        except IngestError as exc:
            self.catalog.log_ingest(
                collection_id, doc_key, "failed",
                document_id=document_id(collection_id, doc_key) if doc_key else None, error=str(exc),
            )  # fmt: skip
            raise

    # reads -------------------------------------------------------------------------------

    def documents(self, collection_id: str, include_deleted: bool = False) -> List[Document]:
        return self.catalog.list_documents(collection_id, include_deleted=include_deleted)

    def document(self, collection_id: str, key: str) -> Optional[Document]:
        return self.catalog.get_document(document_id(collection_id, key))

    def canonical_text(self, version_id: str) -> str:
        version = self.catalog.get_version(version_id)
        if version is None:
            raise CatalogError(f"no version {version_id!r}")
        data = self.blobs.get(version.text_sha)
        if data is None:
            raise CatalogError(
                f"canonical text of {version_id} is missing from the blob store",
                {"sha": version.text_sha},
            )
        return data.decode("utf-8")

    # retrieval ------------------------------------------------------------------------------

    def default_mode(self, collection_id: str) -> str:
        """:data:`DEFAULT_MODE` when the collection has both indexes, else the one it has."""
        spec = self.collection(collection_id).spec
        if spec.dense is not None and spec.lexical is not None:
            return DEFAULT_MODE
        return "dense" if spec.dense is not None else "lexical"

    def retriever(self, collection_id: str, mode: Optional[str] = None):
        """The collection's retriever graph for ``mode`` (``dense``, ``lexical``, ``hybrid``,
        ``tree``, ``graph``; default :meth:`default_mode`). An explicit mode the collection cannot
        serve raises.

        Raises:
            QueryError: Unknown mode, or the collection lacks the index it needs.
        """
        spec = self.collection(collection_id).spec
        mode = mode or self.default_mode(collection_id)
        if mode not in MODES:
            raise QueryError(f"unknown retrieval mode {mode!r}; use one of {list(MODES)}")
        if mode == "tree":
            if spec.tree is None:
                raise QueryError(
                    f"collection {collection_id!r} has no tree index for mode 'tree'; set "
                    "CollectionSpec(tree=TreeSpec(llm=...)) and re-add its documents"
                )
            seed = self.retriever(collection_id, self.default_mode(collection_id))
            return tree_retriever(seed, spec.tree, catalog=self.catalog_key)
        if mode == "graph":
            if spec.graph is None:
                raise QueryError(
                    f"collection {collection_id!r} has no concept graph for mode 'graph'; set "
                    "CollectionSpec(graph=GraphSpec()) and re-add its documents"
                )
            seed = self.retriever(collection_id, self.default_mode(collection_id))
            return graph_retriever(seed, spec.graph, catalog=self.catalog_key)
        if mode in ("dense", "hybrid") and spec.dense is None:
            raise QueryError(f"collection {collection_id!r} has no dense index for mode {mode!r}")
        if mode in ("lexical", "hybrid") and spec.lexical is None:
            raise QueryError(
                f"collection {collection_id!r} has no lexical index for mode {mode!r}; set "
                "CollectionSpec(lexical=LexicalIndexSpec(...)) and run rebuild_lexical()"
            )
        if mode == "dense":
            return dense_retriever(spec.dense, catalog=self.catalog_key)
        if mode == "lexical":
            return lexical_retriever(spec.lexical, catalog=self.catalog_key)
        return hybrid_retriever(
            dense_retriever(spec.dense, catalog=self.catalog_key),
            lexical_retriever(spec.lexical, catalog=self.catalog_key),
        )

    def search_graph(
        self,
        collection_id: str,
        mode: Optional[str] = None,
        reranker: Optional[str] = None,
        rerank_depth: int = 30,
    ):
        """The search graph: the retriever, the hydration gate, and with ``reranker``
        (a ``reranking:`` resource name) a rerank of ``rerank_depth`` hits."""
        search = search_graph(self.retriever(collection_id, mode), catalog=self.catalog_key)
        return reranked(search, reranker, depth=rerank_depth) if reranker else search

    async def search(
        self,
        collection_id: str,
        query: str,
        *,
        filter: Optional[Any] = None,
        k: int = 10,
        mode: Optional[str] = None,
        reranker: Optional[str] = None,
        rerank_depth: int = 30,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search a collection; return ``{"hits", "stats"}`` (hits hydrated, best first).

        Args:
            filter: A :class:`~operonx_kb.model.filter.KBFilter` or its dict.
            mode: ``dense``, ``lexical``, ``hybrid``, ``tree`` or ``graph`` (default
                :meth:`default_mode`).
            reranker: A ``reranking:`` resource name to rerank with.
            trace_id: The run's trace id (default: a new one), for a caller that links
                to the run's trace.

        Raises:
            QueryError: An op failed (a filter on an undeclared field, a missing index…).
        """
        mode = mode or self.default_mode(collection_id)
        flt = KBFilter.of(filter).model_dump(mode="json", exclude_defaults=True) or None
        spec = self.collection(collection_id).spec
        config = f"{mode}|{reranker}|{rerank_depth}|{spec.model_dump_json()}"
        out = await self._run_graph(
            "search", collection_id,
            lambda: self.search_graph(collection_id, mode, reranker, rerank_depth), config,
            {"query": query, "collection": collection_id, "filter": flt, "k": k}, None,
            error=QueryError, required=("hits",), trace_id=trace_id,
        )  # fmt: skip
        return {"hits": out["hits"], "stats": out.get("stats", {})}

    def answer_graph(
        self,
        collection_id: str,
        llm: str,
        *,
        mode: Optional[str] = None,
        reranker: Optional[str] = None,
        rerank_depth: int = 30,
        budget_tokens: int = 1500,
        neighbours: int = 1,
    ):
        """The answer graph: this collection's search, the context, ``llm`` and citation checks."""
        return answer_graph(
            self.search_graph(collection_id, mode, reranker, rerank_depth),
            llm,
            budget_tokens=budget_tokens,
            neighbours=neighbours,
            catalog=self.catalog_key,
            blobs=self.blobs_key,
        )

    async def ask(
        self,
        collection_id: str,
        question: str,
        llm: str,
        *,
        filter: Optional[Any] = None,
        k: int = 8,
        mode: Optional[str] = None,
        reranker: Optional[str] = None,
        rerank_depth: int = 30,
        budget_tokens: int = 1500,
        neighbours: int = 1,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Answer a question from the collection with verified citations (track5 §10).

        ``trace_id`` names the run (default: a new one), as in :meth:`search`.

        Returns:
            ``{"text", "citations", "dropped", "unsupported_sentences", "sources",
            "stats", "usage"}``. Every citation's quote was found in its source and
            carries its canonical span, pages and boxes; the rest are in ``dropped``.

        Raises:
            QueryError: An op failed, or the model's reply did not parse.
        """
        mode = mode or self.default_mode(collection_id)
        flt = KBFilter.of(filter).model_dump(mode="json", exclude_defaults=True) or None
        spec = self.collection(collection_id).spec
        config = f"{llm}|{mode}|{reranker}|{rerank_depth}|{budget_tokens}|{neighbours}|{spec.model_dump_json()}"
        return await self._run_graph(
            "answer", collection_id,
            lambda: self.answer_graph(collection_id, llm, mode=mode, reranker=reranker,
                                      rerank_depth=rerank_depth, budget_tokens=budget_tokens,
                                      neighbours=neighbours),
            config, {"query": question, "collection": collection_id, "filter": flt, "k": k},
            "answer", error=QueryError, trace_id=trace_id,
        )  # fmt: skip

    # maintenance ----------------------------------------------------------------------------

    async def delete(self, collection_id: str, key: str, *, purge: bool = False) -> Dict[str, Any]:
        """Tombstone a document and delete its vectors; with ``purge``, erase it and prove it."""
        return await self._run("delete_document", collection_id, build_delete_graph,
                               {"collection": collection_id, "key": key, "purge": purge}, "report")  # fmt: skip

    async def gc(
        self, collection_id: str, *, blobs: bool = False, blob_grace_seconds: float = 3600.0
    ) -> Dict[str, Any]:
        """Delete vectors no active version holds; with ``blobs``, unreferenced blobs too."""
        return await self._run("collect_garbage", collection_id, build_gc_graph,
                               {"collection": collection_id, "blobs_enabled": blobs, "grace_seconds": blob_grace_seconds},
                               "report")  # fmt: skip

    async def rebuild(
        self,
        collection_id: str,
        *,
        store: Optional[str] = None,
        collection: Optional[str] = None,
        switch: bool = True,
        drop_previous: bool = False,
    ) -> Dict[str, Any]:
        """Rebuild the dense index from the catalog (track5 §11.6): a new generation in
        ``store``/``collection`` (default: the current ones), switched to when ``switch``.

        Nothing is parsed; vectors come from the embedding cache when the embedder is
        unchanged. With ``drop_previous`` the old generation's vectors are deleted after
        the switch (when it was a different store or collection).
        """
        current = self._dense(collection_id)
        target = current.model_copy(
            update={
                "store": store or current.store,
                "collection": collection if collection is not None else current.collection,
            }
        )
        report = await self._run("rebuild_index", collection_id, build_rebuild_graph,
                                 {"collection": collection_id, "switch": switch}, "report", dense=target)  # fmt: skip
        moved = (target.store, target.collection or "") != (current.store, current.collection or "")
        if drop_previous and switch and moved:
            out = await self._run("drop_index", collection_id, build_drop_index_graph,
                                  {"collection": collection_id}, None, dense=current)  # fmt: skip
            report["previous_deleted"] = out.get("deleted")
        return report

    async def rebuild_lexical(
        self,
        collection_id: str,
        lexical: Optional[LexicalIndexSpec] = None,
        *,
        switch: bool = True,
        drop_previous: bool = False,
    ) -> Dict[str, Any]:
        """Rebuild the lexical index from the catalog into ``lexical`` (default: the current
        spec): another table, or the same text under another analyzer. Nothing is parsed or
        embedded. With ``switch`` it becomes the collection's lexical index; with
        ``drop_previous`` the old one's entries are deleted after the switch."""
        current = self.collection(collection_id).spec.lexical
        target = lexical or current
        if target is None:
            raise CatalogError(
                f"collection {collection_id!r} has no lexical index; pass one to rebuild_lexical()"
            )
        report = await self._run_graph(
            "rebuild_lexical", collection_id,
            lambda: build_lexical_rebuild_graph(target, catalog=self.catalog_key, blobs=self.blobs_key),
            target.model_dump_json(), {"collection": collection_id, "switch": switch}, "report",
        )  # fmt: skip
        moved = current is not None and (target.index, target.collection) != (
            current.index,
            current.collection,
        )
        if drop_previous and switch and moved:
            out = await self._run_graph(
                "drop_lexical", collection_id,
                lambda: build_lexical_drop_graph(current, catalog=self.catalog_key, blobs=self.blobs_key),
                current.model_dump_json(), {"collection": collection_id}, None,
            )  # fmt: skip
            report["previous_deleted"] = out.get("deleted")
        return report

    def verify(self, collection_id: str) -> VerifyReport:
        spec = self.collection(collection_id).spec
        dense, lexical = spec.dense, spec.lexical
        return verify(
            self.catalog,
            self.blobs,
            collection_id,
            full_key(dense.store, "vector_store") if dense else None,
            (dense.collection or "") if dense else "",
            full_key(lexical.index, "kb_lexical") if lexical else None,
            (lexical.collection or "") if lexical else "",
        )
