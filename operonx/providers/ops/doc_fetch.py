"""DocFetchOp — fetch records by primary key from a store of record.

The second half of the retrieval pair. ``VectorSearchOp`` answers *which*
documents; this answers *what they are*. Keeping them separate means the
hydration hop gets its own trace span — in most RAG pipelines it costs
more wall-clock than the vector search itself, and bundled that cost is
invisible. See ``OP_TAXONOMY_REFACTOR_PLAN.md`` §5.8.
"""

from typing import Any, Dict, List, Optional

from operonx.core.configs import OpType
from operonx.core.ops import BaseOp
from operonx.core.ops.base import shorthand, split_shorthand_kwargs
from operonx.core.utils.common import Param
from operonx.providers.ops._utils import resolve_hub

__all__ = ["DocFetchOp"]


class DocFetchOp(BaseOp):
    """Op that hydrates ids into records via ResourceHub.

    Inputs:
        ids (list): Primary keys to fetch. Required — typically
            ``VectorSearchOp``'s ``ids`` output.
        collection (str): Table / collection. Default None, meaning the
            resource's configured default.
        fields (list[str]): Column projection. Default None = all.
        id_field (str): Primary-key field name. Default None, meaning the
            resource's configured default (itself defaulting to ``"id"``).

    Outputs:
        rows (list[dict]): Records **in ``ids`` order**.
        missing (list): Ids that matched no record.

    Two guarantees, and they are why this is an op rather than a snippet:

    1. ``rows`` follows ``ids`` order. Search returns score-ordered ids;
       ``SELECT … WHERE id = ANY(…)`` returns arbitrary order. Zipping
       them naively pairs every document with the wrong score — silently.
    2. Missing ids surface in ``missing`` instead of quietly shortening
       ``rows``. An index that has drifted from the store of record is a
       real condition and should be observable.

    Scope is fetch-by-ids with an optional projection. No joins, writes,
    transactions, or custom SQL — for those, write a bare ``@op`` against
    your own client.

    Example::

        hits = VectorSearchOp.of(resource="docs", query_vector=emb["embeddings"][0], top_k=20)
        docs = DocFetchOp.of(resource="main", ids=hits["ids"],
                             collection="docs", fields=["id", "title", "content"])
    """

    __slots__ = ["resource", "backend", "_initialized"]

    type: OpType = "doc-fetch"

    def __init__(
        self,
        resource: Optional[str] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs: Any,
    ):
        """Initialize DocFetchOp.

        Args:
            resource: Resource key for the document store. A bare name is
                looked up as ``doc_store:{resource}``; a key that already
                contains ``:`` is used verbatim.
            inputs: Input variable mappings.
            outputs: Output variable mappings.
            **kwargs: Additional keyword arguments for BaseOp.
        """
        kwargs.setdefault("bound", "io")
        super().__init__(**kwargs)

        self.resource = resource

        input_schema = {
            "ids": Param(type=list, required=True),
            "collection": Param(type=str, required=False, default=None),
            "fields": Param(type=list, required=False, default=None),
            "id_field": Param(type=str, required=False, default=None),
        }
        output_schema = {
            "rows": Param(type=list, required=True),
            "missing": Param(type=list, required=False),
        }

        self.inputs = self._merge_params(input_schema, inputs)
        self.outputs = self._merge_params(output_schema, outputs)

        self.backend = None
        self._initialized = False
        self._set_core(self._process)

    def warmup(self) -> None:
        """Eagerly resolve the backend on engine startup."""
        self._ensure_initialized()

    def _ensure_initialized(self):
        """Lazy-resolve the backend from ResourceHub, adopting its ``bound``."""
        if self._initialized:
            return
        hub = resolve_hub()
        key = self.resource if ":" in (self.resource or "") else f"doc_store:{self.resource}"
        self.backend = hub.get(key)
        backend_bound = getattr(self.backend, "bound", None)
        if backend_bound:
            self.bound = backend_bound
        self._initialized = True

    async def _process(
        self,
        ids: list,
        collection: Optional[str] = None,
        fields: Optional[List[str]] = None,
        id_field: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch and return ``rows`` (in ``ids`` order) plus ``missing``."""
        self._ensure_initialized()
        resolved_id_field = id_field or getattr(self.backend.config, "id_field", "id")
        rows, missing = await self.backend.fetch(
            ids,
            collection=collection,
            fields=fields,
            id_field=resolved_id_field,
        )
        return {"rows": rows, "missing": missing}

    @shorthand
    def of(cls, resource=None, **kwargs) -> "DocFetchOp":
        """Create a DocFetchOp with flat kwargs.

        Example::

            docs = DocFetchOp.of(resource="main", ids=hits["ids"], collection="docs")
        """
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
        return cls(resource=resource, inputs=input_mappings or None, **init_kwargs)

    def serialize(self) -> dict:
        """Serialize for the Rust backend, including resource config."""
        self._ensure_initialized()
        base = super().serialize()
        base["resource"] = self.resource
        if self.backend and hasattr(self.backend, "config"):
            base["resource_config"] = self.backend.config.model_dump(mode="json")
        return base

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Return doc-fetch-specific metadata."""
        meta: Dict[str, Any] = {"store": self.resource}
        if self.backend and hasattr(self.backend, "config"):
            cfg = self.backend.config
            meta["backend"] = getattr(cfg.api_type, "value", cfg.api_type)
        return meta
