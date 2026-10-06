"""``embed_chunks`` — embeddings for chunks, through an operonx ``embedding:`` resource.

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
from typing import Any, Dict, List

from operonx import op

from operonx_kb.model.ids import fingerprint
from operonx_kb.ops._resources import backend_settings, catalog_of, resolve

__all__ = ["embed_chunks", "embedder_fingerprint"]


def embedder_fingerprint(resource_key: str, backend: Any, template: str = "{text}") -> str:
    """``H(model, settings, template)`` of an embedding backend; credentials and endpoints
    excluded, so rotating a key does not re-embed the corpus. The default template adds
    nothing, so caches written before templates existed stay valid."""
    data: Dict[str, Any] = backend_settings(backend)
    if template != "{text}":
        data["template"] = template
    return fingerprint(f"{type(backend).__module__}.{type(backend).__qualname__}", "1", data)


@op(exclude={"trace": ["chunks", "vectors"]}, show_keys="stats")
async def embed_chunks(
    chunks: list, embedder: str, catalog: str, batch_size: int = 64, template: str = "{text}"
) -> dict:
    """Embed chunks, cache first.

    Args:
        chunks: Chunk dumps (``Chunk.model_dump()``) to embed.
        embedder: The embedder key; a bare name is ``embedding:<name>``.
        catalog: The catalog key holding the embedding cache.
        batch_size: Texts per embedder call.
        template: How a chunk's embed text is presented to the model
            (``"passage: {text}"`` for E5). Part of the cache key: the same text
            under another template is another vector.

    Returns:
        ``vectors`` (``chunk_id -> vector``) and ``stats`` (``embedded`` sent to the
        model, ``cached`` cache hits, ``calls``).
    """
    if "{text}" not in template:
        raise ValueError(f"embed_chunks template {template!r} must contain {{text}}")
    backend = resolve(embedder, "embedding")  # the hub caches the instance
    cat = catalog_of(catalog)
    fp = embedder_fingerprint(embedder, backend, template)
    by_sha: Dict[str, str] = {}
    for c in chunks:
        by_sha.setdefault(c["embed_text_sha"], c["embed_text"])
    cached = await asyncio.to_thread(cat.get_embeddings, fp, list(by_sha))
    missing = [sha for sha in by_sha if sha not in cached]
    fresh: Dict[str, List[float]] = {}
    calls = 0
    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        result = await backend.run([template.replace("{text}", by_sha[sha]) for sha in batch])
        calls += 1
        vectors = result["embeddings"]
        if len(vectors) != len(batch):
            raise ValueError(
                f"embedder {embedder!r} returned {len(vectors)} vectors for {len(batch)} texts"
            )
        fresh.update({sha: [float(x) for x in v] for sha, v in zip(batch, vectors)})
    if fresh:
        await asyncio.to_thread(cat.put_embeddings, fp, fresh)
    table = {**cached, **fresh}
    return {
        "vectors": {c["id"]: table[c["embed_text_sha"]] for c in chunks},
        "stats": {"embedded": len(fresh), "cached": len(cached), "calls": calls},
    }
