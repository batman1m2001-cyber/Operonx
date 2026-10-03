# Vector stores

Backends for `VectorSearchOp`, `VectorUpsertOp` and `VectorDeleteOp`.
A vector store here is a **derived index**:
vectors, ids, and small filterable metadata. It never holds document
content — that lives in your store of record and is hydrated by
`DocFetchOp`.

| Backend | `api_type` | Extra | Server? | Filtering | `bound` |
|---|---|---|---|---|---|
| FAISS | `faiss` | `operonx[faiss]` | no | **none** | `cpu` |
| pgvector | `pgvector` | `operonx[pgvector]` | Postgres | full SQL | `io` |
| Qdrant | `qdrant` | `operonx[qdrant]` | Qdrant | condition tree | `io` |

## Filters are backend-native

`filter=` takes the backend's **own dialect, untranslated**. There is no
portable filter DSL, deliberately: a DSL leaks (Qdrant has geo and nested
conditions, Postgres has all of SQL, Milvus has `json_contains`), and a
mistranslation is silent — a filter that fails to apply returns *more*
rows, which in a multi-tenant system is a data leak rather than a
warning.

Every backend validates the shape it is given and **raises** on anything
it doesn't recognise. Nothing degrades to "no filter".

The same query in each dialect —
`tenant = "acme" AND created_at >= 1700000000 AND doc_type IN (faq, manual)`:

### pgvector

Explicit SQL with bound parameters:

```python
filter = {
    "where": "tenant = %(tenant)s AND created_at >= %(since)s AND doc_type = ANY(%(types)s)",
    "params": {"tenant": "acme", "since": 1700000000, "types": ["faq", "manual"]},
}
```

Or equality sugar for the common case — disambiguated by the absence of
a `where` key:

```python
filter = {"tenant": "acme"}  # → WHERE tenant = %(f_tenant)s
```

Values are always bound. Column names in the sugar form are validated as
identifiers, since they cannot be bound parameters.

Only columns listed in `metadata_columns` are queryable or writable.

### Qdrant

Condition tree, passed through verbatim:

```python
filter = {
    "must": [
        {"key": "tenant", "match": {"value": "acme"}},
        {"key": "created_at", "range": {"gte": 1700000000}},
        {"key": "doc_type", "match": {"any": ["faq", "manual"]}},
    ]
}
```

Full vocabulary available: `must` / `should` / `must_not`;
`match.value|any|except|text`; `range`; `is_empty`, `is_null`, `has_id`;
`geo_radius`, `geo_bounding_box`; `nested`.

### FAISS — no filtering

```python
filter = None  # anything else raises
```

FAISS is a pure vector index with no metadata store. Passing a filter
raises rather than post-filtering in Python: over-fetching and then
filtering silently returns fewer than `top_k` hits, and a silent wrong
answer is worse than a refusal.

To approximate filtering, pre-partition into separate indices and select
one per call:

```yaml
vector_store:docs:
  api_type: faiss
  collections:
    acme:  ./data/acme.faiss
    globex: ./data/globex.faiss
```

```python
hits = VectorSearchOp.of(resource="docs", query_vector=v, collection="acme")
```

Filters are validated by qdrant-client's own Pydantic models, so an
unrecognised shape raises instead of being quietly ignored. Restrict what
comes back with `metadata_columns` — left unset, Qdrant returns the whole
payload, which is how document content sneaks into a derived index.

The distance function is fixed on the Qdrant **collection**, not per
query, so `metric` is informational for this backend.

### Milvus / Chroma (not yet shipped)

Recorded so the `filter` parameter's type makes sense. Milvus's dialect
is an **expression string**, which is why `filter` accepts `(dict, str)`:

```python
filter = 'tenant == "acme" && created_at >= 1700000000'  # Milvus
filter = {"$and": [{"tenant": {"$eq": "acme"}}]}  # Chroma
```

## Writes and deletes

`upsert(ids, vectors, metadata=None, collection=None)` inserts or
replaces by id. `delete(ids=None, filter=None, collection=None)` removes
by id **or** by filter — the same native dialect `search` takes:

```python
await store.delete(ids=[3, 7])                                   # any backend
await store.delete(filter={"tenant": "acme"})                    # pgvector
await store.delete(filter={"must": [{"key": "tenant", "match": {"value": "acme"}}]})  # Qdrant
```

Neither, both, or an empty filter raise: each could otherwise read as
"delete everything". `ids=[]` deletes nothing, and ids the index does not
hold are not an error, so a cleanup pass can simply run again. It returns
how many vectors went, or `None` on Qdrant, which does not say.

FAISS deletes by id only, and only from an id-mapped index
(`IndexIDMap`/`IndexIDMap2`, which `dim=` builds, or an IVF index). On a
bare flat index the ids are row positions, and removing one renumbers the
rest, so it raises instead. Deletes change the in-memory index; call
`save()` to persist.

In a graph these are `VectorUpsertOp` and `VectorDeleteOp`.

## Searching an empty index

`VectorSearchOp` sets `empty_index=True` and logs a WARNING when an
unfiltered search finds nothing: a nearest-neighbour index returns hits
whenever it holds any vector, so none means nothing was ever written (or
everything was deleted). Under a filter, no hits is a real answer and is
not flagged.

## Scores

Ordering is always best-match first. The score follows the metric, and is
normalised so backends report the same number for the same metric:

| `metric` | Meaning | Better is |
|---|---|---|
| `ip` | inner product | higher |
| `cosine` | cosine similarity | higher |
| `l2` | euclidean distance | lower |

pgvector's raw operators are all "smaller is closer" (`<#>` is the
*negative* inner product, `<=>` is cosine *distance*), so the backend
converts them in the SELECT list. The `ORDER BY` still uses the bare
operator — ordering by the converted expression is equivalent arithmetic
but opaque to the planner and silently drops the HNSW index.

## Adding a backend

1. Subclass `BaseVectorStore` in `providers/vector_stores/<name>.py`.
   Set `bound` (`"cpu"` for in-process, `"io"` for networked).
2. Implement `search()` → `(ids, scores, metadata)`, index-aligned and
   best-first. Implement `upsert()` and `_delete()` (`delete()` itself
   lives on the base, which checks its arguments first).
3. Validate your filter dialect and raise on unknown shapes. Never
   ignore a filter you don't understand.
4. Add the enum entry to `config.py` and a lazy-import branch to
   `factory.py`.
5. Add the extra to `pyproject.toml` and the lazy entry in `__init__.py`.

Roughly 100–150 LOC. See `faiss.py` (simplest) or `pgvector.py`
(filters + SQL) as references.
