"""16 RAG Pipeline — the two-store retrieval model, end to end.

The shape this example exists to show::

    EmbeddingOp → VectorSearchOp → DocFetchOp → RerankOp → LLMOp

Five hops, five trace spans. The important idea is the split between the
two stores:

* **The vector index is derived data.** It holds vectors, ids, and small
  *filterable* metadata (tenant, doc_type, timestamps) — never document
  content. You should be able to drop it and rebuild from source.
* **The document store is the source of truth.** Content, ACLs, audit,
  retention all live here.

Keeping them apart avoids the most common silent RAG bug: a document
updated in your database but stale inside the vector index's payload,
quietly feeding the LLM outdated context. It also means hydration shows
up as its own span — in most pipelines it costs more wall-clock than the
vector search itself.

This runs with **no servers**: FAISS for vectors, an in-memory doc store
for content. Swap the two resource entries for pgvector + Postgres and
nothing else in the graph changes — see resources.yaml.

Requires ``OPENAI_API_KEY`` in ``.env``. Run from this directory::

    uv sync
    cp .env.example .env   # fill in OPENAI_API_KEY
    uv run python main.py
"""

from __future__ import annotations

import asyncio

import operonx
from operonx.core import END, PARENT, START, Operon, graph, op
from operonx.providers import DocFetchOp, EmbeddingOp, LLMOp, VectorSearchOp

# ── The corpus ──────────────────────────────────────────────────────────
#
# In a real system these live in your database and the vector index is
# built by a separate ingestion job. Here we seed both at startup.

DOCUMENTS = [
    {
        "id": 1,
        "title": "Refund policy",
        "content": "Refunds are issued within 14 days of purchase. "
        "Digital goods are refundable only if unopened.",
        "tenant": "acme",
    },
    {
        "id": 2,
        "title": "Shipping times",
        "content": "Standard shipping takes 3-5 business days. "
        "Express shipping arrives next business day.",
        "tenant": "acme",
    },
    {
        "id": 3,
        "title": "Warranty",
        "content": "All hardware carries a 2-year limited warranty covering manufacturing defects.",
        "tenant": "acme",
    },
    {
        "id": 4,
        "title": "Password reset",
        "content": "Reset your password from Settings → Security. "
        "Reset links expire after 30 minutes.",
        "tenant": "acme",
    },
]


@op
def build_context(rows: list) -> dict:
    """Flatten fetched documents into a prompt-ready context block."""
    if not rows:
        return {"context": "(no relevant documents found)"}
    return {
        "context": "\n\n".join(f"[{r['title']}]\n{r['content']}" for r in rows),
    }


@graph
def rag(question):
    """Embed → search → hydrate → answer.

    ``VectorSearchOp`` returns ids/scores/metadata only. ``DocFetchOp``
    turns those ids into records — **in the same order**, so hits stay
    aligned with their scores. Getting that wrong by hand (SQL returns
    arbitrary order) silently pairs every document with the wrong score.
    """
    q_emb = EmbeddingOp.of(resource="openai", texts=[question])

    hits = VectorSearchOp.of(
        resource="docs-faiss",
        query_vector=q_emb["embeddings"][0],
        top_k=3,
    )

    docs = DocFetchOp.of(
        resource="corpus",
        ids=hits["ids"],
        collection="docs",
        fields=["id", "title", "content"],
    )

    ctx = build_context(rows=docs["rows"])

    answer = LLMOp.of(
        resource="gpt-4o-mini",
        prompt={
            "system": "Answer using only the provided context. "
            "If the context doesn't cover it, say so.",
            "user": "Question: {question}\n\nContext:\n{context}",
        },
        question=question,
        context=ctx["context"],
    )

    START >> q_emb >> hits >> docs >> ctx >> answer >> END
    answer["content"] >> PARENT["answer"]


async def seed() -> None:
    """Index the corpus and load it into the document store.

    Real deployments do this in an ingestion job, not at query time — the
    two stores are written together so the index never references a
    document that isn't there.
    """
    from operonx.core.registry import ResourceHub

    hub = ResourceHub.instance()

    # 1. Embed every document once.
    embedder = hub.get("embedding:openai")
    texts = [f"{d['title']}. {d['content']}" for d in DOCUMENTS]
    result = await embedder.run(texts)
    vectors = result["embeddings"]

    # 2. Vector index gets ids + vectors. No content.
    store = hub.get("vector_store:docs-faiss")
    await store.upsert(ids=[d["id"] for d in DOCUMENTS], vectors=vectors)

    # 3. Document store gets the content — the source of truth.
    docs = hub.get("doc_store:corpus")
    docs.put(DOCUMENTS, collection="docs")


async def main() -> None:
    operonx.bootstrap()
    await seed()

    engine = Operon(rag(question=PARENT["question"]))

    questions = [
        "How long do refunds take?",
        "When does my warranty run out?",
        "How do I change my password?",
    ]
    for question in questions:
        result = await engine.run(inputs={"question": question})
        print(f"\nQ: {question}")
        print(f"A: {result['answer']}")


if __name__ == "__main__":
    asyncio.run(main())
