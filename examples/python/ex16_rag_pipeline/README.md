# 16 · RAG Pipeline — the two-store model

```
EmbeddingOp → VectorSearchOp → DocFetchOp → RerankOp* → LLMOp
```

Five hops, five trace spans. (`RerankOp` is optional and omitted here to
keep the example to one API dependency.)

## The idea

Retrieval uses **two stores with different jobs**:

| | Vector index | Document store |
|---|---|---|
| Holds | vectors, ids, small **filterable** metadata | the actual content |
| Role | derived data — droppable, rebuildable | source of truth |
| Answers | *which* documents | *what they say* |
| Op | `VectorSearchOp` | `DocFetchOp` |

The index never stores document content. That is what avoids the most
common silent RAG bug: a document updated in your database but stale
inside the vector store's payload, quietly feeding the model outdated
context. It also keeps index memory small, lets you swap embedding
models without rewriting your corpus, and leaves ACL/audit/retention
where they belong.

The index *does* carry filterable metadata (`tenant`, `doc_type`,
timestamps) — without it, filtered search would have to over-fetch and
post-filter, which silently breaks `top_k`.

## Run it

No servers required — FAISS for vectors, an in-memory store for content.

```bash
uv sync
cp .env.example .env    # fill in OPENAI_API_KEY
uv run python main.py
```

```
Q: How long do refunds take?
A: Refunds are issued within 14 days of purchase...
```

## The one thing to notice

`DocFetchOp` returns rows **in the same order as the ids it was given**.

Vector search returns ids ranked by score. `SELECT … WHERE id = ANY(…)`
returns rows in whatever order the database likes. Zipping those two by
hand pairs every document with the wrong score — silently, with no error,
usually discovered in production. `DocFetchOp` restores the order for
you, and reports ids that matched nothing in `missing` rather than
quietly returning a shorter list.

Writing your own fetch op instead? Use the same helper:

```python
from operonx.providers.doc_stores import reorder_by_ids
```

## Going to production

Swap the two resource entries in `resources.yaml` — the graph doesn't
change:

```yaml
vector_store:docs-faiss:
  api_type: pgvector
  metric: cosine
  dsn: ${PG_DSN}
  table: docs_vec
  metadata_columns: [tenant]

doc_store:corpus:
  api_type: postgres
  dsn: ${PG_DSN}
  collection: docs
```

Pointing both at the same database is deliberate: one system, two tables,
transactional consistency, no dual-write risk, and a shared connection
pool.

With pgvector you also get filtering, which FAISS cannot do:

```python
hits = VectorSearchOp.of(
    resource="docs-faiss",
    query_vector=q_emb["embeddings"][0],
    top_k=20,
    filter={"tenant": "acme"},  # matched against indexed metadata
)
```

Filters are **backend-native** and never translated by operonx — see
`operonx/providers/vector_stores/README.md` for each backend's dialect.

## Ingestion

`seed()` writes both stores at startup for demo purposes. Real systems do
this in an ingestion job, writing the two together so the index never
points at a document that isn't there.
