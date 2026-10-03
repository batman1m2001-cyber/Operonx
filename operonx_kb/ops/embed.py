"""EmbedChunksOp — embeddings for chunks, through an operonx ``embedding:`` resource.

The embedder is any operonx ``BaseEmbedder`` resource, unchanged. What this op
adds over ``EmbeddingOp`` is the durable, content-addressed embedding cache in
the catalog (track5 §11.3): the key is ``(embedder_fp, embed_text_sha)``, so a
chunk whose embedded text was seen before is never sent to the model again —
not after a re-ingest, a delete and re-add, or a chunker change that leaves its
text alone. The operonx op cache is not used for this: it is keyed by neither
a model fingerprint nor a durable store.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from operonx.core.configs import OpType
from operonx.core.ops import BaseOp
from operonx.core.ops.base import shorthand, split_shorthand_kwargs
from operonx.core.utils.common import Param

from operonx_kb.model.ids import fingerprint
from operonx_kb.ops._resources import catalog_of, resolve

__all__ = ["EmbedChunksOp", "embedder_fingerprint"]

_SECRET_HINTS = ("key", "token", "secret", "password", "url", "header")


def embedder_fingerprint(resource_key: str, backend: Any) -> str:
    """``H(model and settings)`` of an embedding backend; credentials and endpoints excluded,
    so rotating a key does not re-embed the corpus."""
    config = getattr(backend, "config", None)
    data: Dict[str, Any] = {}
    if config is not None and hasattr(config, "model_dump"):
        dump = config.model_dump(mode="json")
        data = {
            k: v
            for k, v in dump.items()
            if v is not None and not any(h in k.lower() for h in _SECRET_HINTS)
        }
    return fingerprint(f"{type(backend).__module__}.{type(backend).__qualname__}", "1", data)


class EmbedChunksOp(BaseOp):
    """Embed chunks, cache first.

    Inputs:
        chunks (list[dict]): Chunk dumps (``Chunk.model_dump()``) to embed.

    Outputs:
        vectors (dict): ``chunk_id -> vector``.
        stats (dict): ``embedded`` (sent to the model), ``cached`` (cache hits), ``calls``.

    Example::

        emb = EmbedChunksOp.of(resource="bge-m3", catalog="kb_catalog:main", chunks=ch["todo"])
    """

    show_keys_default = ("stats",)

    __slots__ = ["resource", "catalog", "batch_size", "backend", "_initialized"]

    type: OpType = "embedding"

    def __init__(
        self,
        resource: Optional[str] = None,
        catalog: Optional[str] = None,
        batch_size: int = 64,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs: Any,
    ):
        """Initialize EmbedChunksOp.

        Args:
            resource: The embedder. A bare name is ``embedding:<name>``; a key with
                ``:`` is used verbatim.
            catalog: The catalog key holding the embedding cache.
            batch_size: Texts per embedder call.
        """
        kwargs.setdefault("bound", "io")
        kwargs.setdefault("exclude", {"trace": ["chunks", "vectors"]})
        super().__init__(**kwargs)
        if not resource or not catalog:
            raise ValueError(
                "EmbedChunksOp needs resource= (an embedder key) and catalog= (a kb_catalog key)"
            )
        self.resource = resource
        self.catalog = catalog
        self.batch_size = batch_size
        self.inputs = self._merge_params({"chunks": Param(type=list, required=True)}, inputs)
        self.outputs = self._merge_params(
            {"vectors": Param(type=dict, required=True), "stats": Param(type=dict, required=True)},
            outputs,
        )
        self.backend = None
        self._initialized = False
        self._set_core(self._process)

    def warmup(self) -> None:
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        self.backend = resolve(self.resource, "embedding")
        self._initialized = True

    async def _process(self, chunks: list) -> Dict[str, Any]:
        self._ensure_initialized()
        catalog = catalog_of(self.catalog)
        fp = embedder_fingerprint(self.resource, self.backend)
        by_sha: Dict[str, str] = {}
        for c in chunks:
            by_sha.setdefault(c["embed_text_sha"], c["embed_text"])
        cached = await asyncio.to_thread(catalog.get_embeddings, fp, list(by_sha))
        missing = [sha for sha in by_sha if sha not in cached]
        fresh: Dict[str, List[float]] = {}
        calls = 0
        for start in range(0, len(missing), self.batch_size):
            batch = missing[start : start + self.batch_size]
            result = await self.backend.run([by_sha[sha] for sha in batch])
            calls += 1
            vectors = result["embeddings"]
            if len(vectors) != len(batch):
                raise ValueError(
                    f"embedder {self.resource!r} returned {len(vectors)} vectors for {len(batch)} texts"
                )
            fresh.update({sha: [float(x) for x in v] for sha, v in zip(batch, vectors)})
        if fresh:
            await asyncio.to_thread(catalog.put_embeddings, fp, fresh)
        table = {**cached, **fresh}
        return {
            "vectors": {c["id"]: table[c["embed_text_sha"]] for c in chunks},
            "stats": {"embedded": len(fresh), "cached": len(cached), "calls": calls},
        }

    @shorthand
    def of(cls, resource=None, catalog=None, batch_size=64, **kwargs) -> "EmbedChunksOp":
        """Create an EmbedChunksOp with flat kwargs."""
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
        return cls(
            resource=resource,
            catalog=catalog,
            batch_size=batch_size,
            inputs=input_mappings or None,
            **init_kwargs,
        )

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        return {"model": self.resource}
