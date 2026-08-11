# RAG

Retrieval-augmented generation in Operonx is a chain of ops:

```
EmbeddingOp → VectorSearchOp → DocFetchOp → RerankOp → LLMOp
```

Five hops, five trace spans.

## The two-store model

Retrieval uses **two stores with different jobs**, and keeping them apart
is the single most important design decision here.

| | Vector index | Document store |
|---|---|---|
| Holds | vectors, ids, small **filterable** metadata | the actual content |
| Role | derived data — droppable, rebuildable | source of truth |
| Answers | *which* documents | *what they say* |
| Op | `VectorSearchOp` | `DocFetchOp` |

**The index never stores document content.** That avoids the most common
silent RAG bug — a document updated in your database but stale inside the
vector store's payload, quietly feeding the model outdated context. It
also keeps index memory small, lets you change embedding models without
rewriting your corpus, and leaves ACL, audit, and retention where they
are actually implemented.

The index *does* carry filterable metadata (`tenant`, `doc_type`,
timestamps). Without it, filtered search would have to over-fetch and
post-filter, which silently breaks `top_k` — ask for 10, get 3, no error.

## Pre-requisites

```bash
pip install "operonx[standard,faiss]"        # or [pgvector] for Postgres
```

`resources.yaml`:

```yaml
embedding:bge-m3:
  api_type: openai
  api_key: ${OPENAI_API_KEY}
  base_url: https://api.openai.com/v1
  model: text-embedding-3-large
  dimensions: 3072

llm:gpt-4o:
  api_type: openai
  api_key: ${OPENAI_API_KEY}
  base_url: https://api.openai.com/v1
  model: gpt-4o

# The derived index — vectors, ids, filterable metadata. No content.
vector_store:docs:
  api_type: pgvector
  metric: cosine
  dsn: ${PG_DSN}
  table: docs_vec
  metadata_columns: [tenant, doc_type]

# The source of truth. Same database — one system, no dual-write risk,
# shared connection pool.
doc_store:main:
  api_type: postgres
  dsn: ${PG_DSN}
  collection: docs
  id_field: id
```

The matching schema:

```sql
CREATE TABLE docs_vec (
    id        bigint PRIMARY KEY,
    embedding vector(3072),
    tenant    text,
    doc_type  text
);
CREATE INDEX ON docs_vec USING hnsw (embedding vector_cosine_ops);
```

The index opclass must match `metric` (`vector_cosine_ops` /
`vector_ip_ops` / `vector_l2_ops`) or Postgres silently falls back to a
sequential scan.

## Pipeline

```python
import asyncio
import operonx
from operonx.core import Operon, graph, op, START, END, PARENT
from operonx.providers import DocFetchOp, EmbeddingOp, LLMOp, RerankOp, VectorSearchOp

@op
def build_context(rows: list):
    return {"context": "\n\n".join(f"[{r['title']}]\n{r['content']}" for r in rows)}

@graph
def rag(question, tenant):
    q_emb = EmbeddingOp.of(resource="bge-m3", texts=[question])

    # Which documents — ids, scores, and indexed metadata only.
    hits = VectorSearchOp.of(
        resource="docs",
        query_vector=q_emb["embeddings"][0],
        top_k=20,                       # over-fetch, then let the reranker cut
        filter={"tenant": tenant},      # matched against indexed metadata
    )

    # What they say — from the source of truth, in the same order.
    docs = DocFetchOp.of(
        resource="main",
        ids=hits["ids"],
        collection="docs",
        fields=["id", "title", "content"],
    )

    ranked = RerankOp.of(
        resource="bge-m3-reranker",
        query=question,
        documents=docs["rows"],
        top_k=5,
    )

    ctx = build_context(rows=ranked["documents"])

    answer = LLMOp.of(
        resource="gpt-4o",
        prompt={
            "system": "Answer using only the provided context.",
            "user": "Question: {question}\n\nContext:\n{context}",
        },
        question=question,
        context=ctx["context"],
    )

    START >> q_emb >> hits >> docs >> ranked >> ctx >> answer >> END
    answer["content"] >> PARENT["answer"]

async def main():
    operonx.bootstrap()
    engine = Operon(rag(question=PARENT["question"], tenant=PARENT["tenant"]))
    result = await engine.run(inputs={"question": "What is Operonx?", "tenant": "acme"})
    print(result["answer"])

asyncio.run(main())
```

## Order alignment — the one thing to get right

`VectorSearchOp` returns ids **ranked by score**.
`SELECT … WHERE id = ANY(…)` returns rows in **arbitrary** order.

Zipping those by hand pairs every document with the wrong score —
silently, with no error, and usually discovered in production.
`DocFetchOp` restores the order for you, and reports ids that matched
nothing in `missing` rather than quietly returning a shorter list.

Writing your own fetch op against a store Operonx doesn't ship? Use the
same helper:

```python
from operonx.providers.doc_stores import reorder_by_ids

@op(bound="io")
async def fetch_docs(ids: list):
    rows = await my_db.fetch("SELECT id, content FROM docs WHERE id = ANY($1)", ids)
    return {"rows": reorder_by_ids(rows, ids)}
```

## Filters

`filter=` takes the backend's **native dialect**, untranslated. Operonx
ships no portable filter DSL: mistranslation is silent, and a filter that
fails to apply returns *more* rows — a data leak in a multi-tenant system
rather than a warning. Every backend validates its own shape and raises
on anything unrecognised.

```python
# pgvector — equality sugar
filter={"tenant": "acme"}

# pgvector — full SQL, always with bound params
filter={
    "where":  "tenant = %(t)s AND created_at >= %(since)s",
    "params": {"t": "acme", "since": 1700000000},
}

# Qdrant — condition tree
filter={"must": [{"key": "tenant", "match": {"value": "acme"}}]}
```

FAISS holds no metadata and **raises** on any filter. Pre-partition into
separate indices and select with `collection=` instead. Full dialect
reference: `operonx/providers/vector_stores/README.md`.

## No server? Use FAISS + memory

Both stores have zero-infrastructure backends, so you can build and test
the whole pipeline before standing anything up:

```yaml
vector_store:docs:
  api_type: faiss
  metric: cosine
  dim: 3072

doc_store:main:
  api_type: memory
  collection: docs
  id_field: id
```

The graph doesn't change — only these two resource entries do. See
[`examples/python/ex16_rag_pipeline/`](https://github.com/batman1m2001-cyber/Operonx/tree/main/examples/python/ex16_rag_pipeline)
for a runnable version.

## Notes

- `EmbeddingOp.of` uses keyword args — never positional.
- `texts` is a list even for a single query; `embed["embeddings"]` is
  parallel-shaped, hence `["embeddings"][0]`.
- Over-fetch then rerank: `top_k=20` into the reranker, 5 out. Reranking
  a shortlist is far cheaper than widening the LLM context.
- Hydration usually costs more wall-clock than the vector search. It
  being its own node is what makes that visible in traces.
- For local ONNX embeddings, install `operonx[onnx]` and set
  `api_type: onnx`.

## Where to go next

- Agent loops over retrieval: [Agents](05-agents.md).
- Trace each op for debugging: [Tracing](07-tracing.md).
