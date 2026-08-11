# Operonx → Op Taxonomy Refactor Plan

**Status:** proposed. Sequenced for **1.1.0** (add + deprecate) → **2.0.0**
(delete). Third revision after cross-check found factual errors and design
inconsistencies in earlier drafts (see git log for the delta).

**TL;DR** — Operonx has two mutually inconsistent op-naming patterns in
1.0.0. Semantic ops (`LLMOp`, `EmbeddingOp`, `RerankOp` — each with a
factory + swappable backends) are good. Backend-named ops (`OnnxOp`,
`TritonOp` — each a runtime wrapper without a semantic) are bad. This
plan **deletes both backend-named ops**, extracts a **low-level Triton
helper** (`providers/triton/`) so user `@op`s can hit exotic models
without pain, ships **one new semantic op** (`VectorSearchOp` — earns
its place by completing the text/RAG stack alongside
LLM+Embedding+Rerank), and **cleans up the `OpType` enum**. No new STT /
TTS / classifier / OCR / VAD ops — those belong in user code as bare
`@op`s (see §4 for the criteria).

---

## 1 · The problem

Two op-naming patterns coexist in operonx 1.0.0. They give incompatible
answers to "how do I add a new backend for X?".

### Pattern A — semantic op + backend adapters (good)

```
LLMOp        → providers/llms/{anthropic, openai, gemini, azure}.py   via llms/factory.py
EmbeddingOp  → providers/embeddings/{tei, vllm, huggingface, onnx}.py via embeddings/factory.py
RerankOp     → providers/rerankers/{tei, vllm, onnx}.py               via rerankers/factory.py
```

- Each op has a stable semantic contract.
- Backends are swappable via `resource:` key + lazy-import factory.
- New backend = one file + one enum entry + one factory branch.
- Optional deps isolated via `pip install operonx[<extra>]`.

### Pattern B — backend-named ops (bad)

```
OnnxOp    → coupled to the ONNX runtime; semantic is actually "MLP or
             attention classifier head over embedding vectors" but the
             name says nothing about that
TritonOp  → pure transport wrapper; no semantic at all; users manually
             map Triton tensor names via inputs_map/outputs_map
```

- Op name announces the transport, not the intent.
- Different transports for the same semantic each need their own op.
- `TritonOp`'s `inputs_map`/`outputs_map` bypasses `Param` — the very
  wiring `Param` exists to obviate.

### `OpType` enum leaks the anti-pattern further

`operonx/core/configs/op_config.py`:

```python
OpType = Literal[
    # semantic (good)
    "llm", "embedding", "rerank",
    # backend-named (bad — no corresponding op classes today)
    "milvus", "mongo", "s3",
    # stale (should be gone post-1.0.0)
    "for", "while", "stream", "parser",
    # aspirational
    "tool-executor", "mcp",
    # infrastructure
    "graph", "branch", "code", "lambda", "prompt", "doc-processor",
    "data", "default", "dummy",
]
```

Three registered types (`milvus`, `mongo`, `s3`) name backends with no op
behind them. Four (`for`, `while`, `stream`, `parser`) predate 1.0.0's
back-edge loops + generator ops + `LLMOp.of(fields=…)` structured mode.
Also: `InterruptOp.type = "interrupt"` and `EmitOp.type = "emit"` are
assigned at class scope but **not in the Literal** — silent drift.

---

## 2 · The design principle

**One way to do things: semantic op + backend adapter — but only when the
op earns a framework slot.** Every op in `operonx/providers/` after this
refactor:

1. Names its **semantic intent**, never its transport.
2. Delegates transport to a **factory-selected backend** in a sibling
   package.
3. Uses **`Param` for input/output typing** — no `inputs_map` bags.
4. Ships with **1–2 backend adapters day one**; more via user PR.

**No escape hatches at the op layer.** If a user's model doesn't fit an
existing semantic op, the answer is a bare `@op` — operonx already gives
them `bound="io"`, tracing, ResourceHub, `Param` typing for free. To
make that ~30 LOC even smaller, ship **low-level transport helpers**
(`providers/triton/client.py`, `providers/_utils/onnx.py::load_onnx_session`)
so user `@op`s don't reimplement gRPC pooling or ONNX session loading.

**Trade cost, stated upfront (not buried in gaps):** deleting `TritonOp`
means users writing bespoke Triton wrappers pay ~15 LOC of `@op` around
`providers/triton/client.py`. Real cost, taken deliberately in exchange
for taxonomy legibility. Competitors (BentoML, KServe, Ray Serve) keep a
generic invoke op — operonx does not, on principle.

---

## 3 · What earns a framework op — the 4-criteria bar

Same bar used in `AGENT_EXTENSION_PLAN.md §7.1a`. A candidate earns a
dedicated `BaseOp` subclass only when it hits **all four**:

1. **Complex I/O contract** — transport, retries, response shapes that
   users would re-implement badly if left to bare `@op`.
2. **Rich metadata for tracing** — the span carries framework-renderable
   data (model, cost, tokens for LLMOp; provider, dim for EmbeddingOp).
3. **Reusable shape across many use cases** — the signature is stable
   enough that a base class is a real ceiling on variance.
4. **Non-trivial code volume** — enough boilerplate that inheriting from
   the base saves real work.

**Optional-field caveat:** optional outputs (like `words` on `SttOp`) are
NOT `**kwargs` grab-bags if they represent **capabilities specific
backends may or may not provide**, and the **core contract** is stable
across all. `EmbeddingOp` doesn't fail the bar because some backends
return sparse vectors and others dense — dense is the core, sparse is a
capability.

### Applied to every candidate we considered

| Candidate | Bar | Verdict | Rationale |
|---|---|---|---|
| `VectorSearchOp` | 4/4 | ✅ ship (1.1.0) | Completes the text/RAG stack (LLM+Embedding+Rerank+VectorSearch). Stable core: `query vector → top-K ids`. Every RAG framework has one. |
| `DocFetchOp` | 3.5/4 | ✅ ship (1.1.0) | **Reversal — earlier drafts rejected this.** See the note below. Stable core: `ids → rows`, identical across every store of record. Sits on the critical path of the canonical pipeline (§5.1 makes hydration unconditional), so demand is concrete, not speculative. Owning it also eliminates the silent ordering footgun (§5.3). |
| `SttOp` | 4/4 semantics, but... | ❌ don't ship | Zero users in operonx or agent plan (agent plan doesn't do audio). Callbot has one Triton STT call site; bare `@op` is 10 LOC. Would ship as speculation. |
| `TtsOp` | 4/4 semantics, but... | ❌ don't ship | Same reasoning as SttOp. Zero users beyond callbot. |
| `ClassifierOp` | 4/4 semantics, but... | ❌ don't ship | Zero users. Existing callbot classification is either rule-based (`skip_classify`) or via `LLMOp.of(fields=…)`. If concrete demand appears, ship `SentimentOp`/`ModerationOp` narrowly at that point. |
| `OcrOp` | 4/4 semantics, but... | ❌ don't ship | Zero users. Pure speculation. |
| `VadOp` | Architecturally incompatible | ❌ don't ship | Callbot's VAD lives in `speech/per_call_audio.py` — a background audio-worker thread with per-call `_CallCtx` holding Silero LSTM state, online Gaussian noise model, per-30Hz chunk mutation. Not compatible with `PARENT.declare` (per-graph-run scope, not per-chunk stream state). Audio DSP belongs in its own subsystem, not the graph model. |
| `SpeakerEmbedOp` | Zero users | ❌ don't ship | Same. |
| `OnnxOp` | Was in framework | ❌ delete | Backend-named. Its bespoke I/O ("MLP or attention head over embeddings") is too narrow to earn a semantic op. Users write bare `@op` around `providers/_utils/onnx.py::load_onnx_session`. |
| `TritonOp` | Was in framework | ❌ delete | Backend-named. Users write bare `@op` around `providers/triton/client.py` (new helper). |

**Discipline over completeness.** "HuggingFace ships one" isn't a
sufficient reason to ship one. **Concrete demand + stable shape + not
better served by bare `@op` around a helper** is. Under that rule this
plan ships exactly two new ops — and they're a pair, because §5.1 makes
them one pipeline.

**On the `DocFetchOp` reversal.** Earlier drafts rejected it, reasoning
that stores of record are "too heterogeneous" — the same argument that
killed `ClassifierOp`. That argument was wrong here. Fetch-by-ids is one
of the most stable contracts in the stack:

```
Postgres  → SELECT … WHERE id = ANY($1)
Mongo     → find({_id: {$in: ids}})
Redis     → MGET / pipelined HGETALL
DynamoDB  → BatchGetItem
ES        → mget
```

ids in, rows out, everywhere. The heterogeneity is in the *connection*,
which is exactly what `ResourceHub` already abstracts. Rejecting it also
meant telling users to build a parallel config-and-pooling system beside
the one operonx already ships — which is the real inconsistency.

**Why these two earn slots but STT/TTS/etc don't:**

- **VectorSearchOp + DocFetchOp** complete the text/RAG stack operonx
  already ships partially (LLMOp + EmbeddingOp + RerankOp). Their
  absence forces every RAG user to hand-roll a vector-store client *and*
  a document fetcher *and* their own connection pooling. The
  composability payoff is real:
  `EmbeddingOp → VectorSearchOp → DocFetchOp → RerankOp → LLMOp` is the
  canonical RAG pipeline in one graph, with every hop separately traced.
- **STT/TTS/Classify/OCR** don't cluster into a pipeline with existing
  ops. They're standalone task ops that would sit alone in a user's
  graph — same shape as a bare `@op` wrapper. The framework layer adds
  no compositional value.
- **VAD** is stateful in a way `PARENT.declare` doesn't handle (see §3
  table row). Fundamentally different architecture from graph ops.

---

## 4 · What's changing — full op inventory

| Op | Category | Semantic | Backend package | Status |
|---|---|---|---|---|
| `LLMOp` | unchanged | text → text (+ tools + structured) | `providers/llms/` | Reference pattern. No work. |
| `EmbeddingOp` | unchanged | text → vector | `providers/embeddings/` | Reference pattern. No work. |
| `RerankOp` | unchanged | (docs, query) → scored docs | `providers/rerankers/` | Reference pattern. No work. |
| `VectorSearchOp` | **NEW** | query vector → top-K (ids, scores, metadata) | `providers/vector_stores/` (faiss, pgvector day-one) | 1.1.0 |
| `DocFetchOp` | **NEW** | ids → rows, order-preserved | `providers/doc_stores/` (postgres day-one) | 1.1.0 |
| `OnnxOp` | **DEPRECATE (1.1.0) → DELETE (2.0.0)** | (was: MLP/attention head over embeddings) | — | Users write bare `@op` around `providers/_utils/onnx.py::load_onnx_session`. |
| `TritonOp` | **DEPRECATE (1.1.0) → DELETE (2.0.0)** | (was: generic Triton client) | — | Users write bare `@op` around new `providers/triton/client.py` helper. Callbot migrates its one STT call site. |

**Net:** +2 semantic ops (one pair — §5.1 makes them one pipeline),
–2 deletions, 3 unchanged. See §3 for every rejected candidate and why.

---

## 5 · The retrieval pair — `VectorSearchOp` + `DocFetchOp`

### 5.1 · The load-bearing principle: the index is not a store

**A vector DB is a derived index, not a store of record.** You must be
able to `DROP` it and rebuild from source. That gives you:

- **No dual-write bug.** Doc updated in Postgres but stale in the vector
  DB's payload is among the most common RAG production failures — and it
  fails silently, serving stale context to the LLM.
- **Cheap re-indexing.** Swap embedding models without rewriting your
  document corpus.
- **Real access control.** Row-level security, roles, audit live in a
  real DB. Vector DBs have primitive-to-no ACL.
- **Compliance.** GDPR delete, retention policy, audit trail are mature
  in Postgres, immature everywhere else.
- **Index cost.** Payload bloats RAM/SSD, often ~10×.

**The one exception:** the index MUST carry *filter-relevant* metadata —
`tenant_id`, `doc_type`, `created_at`, permission tags. Without it,
filtered search is impossible: you would over-fetch and post-filter,
which silently breaks top-k semantics (ask for 10, get 3, no error).

So the line this plan draws:

> **Vector index stores: vector + id + small filterable metadata.
> Never document content.**

Consequence: **hydration is unconditional, not optional.** Every RAG
pipeline has a fetch step against the store of record. This is the
opposite of how LangChain / LlamaIndex / Haystack model it (they bundle
a docstore behind `similarity_search() → Document[]`), and the
divergence is deliberate — in a DAG, hydration deserves its own trace
span, its own `bound="io"`, and its own `.parallel()`. In most RAG
pipelines hydration costs more wall-clock than the vector search, and
today that cost is invisible.

### 5.2 · Op signature

```python
# operonx/providers/ops/vector_search.py
class VectorSearchOp(BaseOp):
    """Vector similarity search over a derived index.

    Returns ids + scores + filterable metadata. Does NOT return document
    content — see §5.1. Hydrate from your store of record in a separate
    op.
    """

    type: OpType = "vector-search"

    inputs = {
        "query_vector": Param(type=list, required=True),                 # list[float]
        "top_k":        Param(type=int,  required=False, default=10),
        # Backend-NATIVE filter. dict for most backends; str for Milvus
        # (boolean-expression dialect). See §5.4.
        "filter":       Param(type=(dict, str), required=False, default=None),
        "collection":   Param(type=str,  required=False, default=None),  # collection / table / index
    }
    outputs = {
        "ids":      Param(type=list),   # list[str|int] — score-ordered
        "scores":   Param(type=list),   # list[float]
        "metadata": Param(type=list),   # list[dict] — filterable fields ONLY, never content
    }
```

All three outputs are **index-aligned** — `ids[i]`, `scores[i]`,
`metadata[i]` describe the same hit.

**No `payloads` output.** Earlier drafts had one; it was removed
because the name invites exactly the mistake §5.1 forbids. One contract,
no backend-conditional behavior — even backends that *could* return
content don't.

### 5.3 · Canonical RAG pipeline (two stores, explicit)

```python
@graph
def rag(query, tenant):
    q_emb  = EmbeddingOp(resource="bge-m3", texts=[query])
    hits   = VectorSearchOp(
        resource="pgvector:docs",             # the derived index
        query_vector=q_emb["embeddings"][0],
        top_k=20,
        filter={"tenant": tenant},            # matched against indexed metadata
    )
    docs   = DocFetchOp(
        resource="postgres:main",             # the store of record
        ids=hits["ids"],
        collection="docs",
        fields=["id", "title", "content"],
    )
    ranked = RerankOp(resource="bge-m3-reranker", docs=docs["rows"], query=query)
    llm    = LLMOp.of(resource="claude-haiku",
                      prompt="Context: {ctx}\n\nQ: {q}",
                      ctx=ranked["docs"], q=query)
    START >> q_emb >> hits >> docs >> ranked >> llm >> END
```

Five hops, five trace spans. Hydration is a first-class node — which
matters because in most RAG pipelines it costs more wall-clock than the
vector search, and today that cost is invisible.

**The ordering footgun, and why the framework owns it.**
`VectorSearchOp` returns ids in score order. `SELECT … WHERE id = ANY(…)`
returns rows in *arbitrary* order. Zipping them naively mismatches every
doc to the wrong score — silently, with no error. Every user hits this
once, in production.

`DocFetchOp` restores score order internally (see §5.8), so callers
can't get it wrong. The same logic ships as a pure helper for users who
write their own fetch op anyway:

```python
from operonx.providers.doc_stores import reorder_by_ids
```

Five lines, exported from the package root, used inside `DocFetchOp`
and in the shipped example.

### 5.4 · Filters are backend-native (no DSL)

`filter=` takes the backend's own dialect, untranslated. A portable
filter DSL was considered and rejected:

- **It leaks.** Qdrant has geo-radius / nested / `has_id`; pgvector has
  all of SQL; Milvus has `json_contains`. A DSL either can't express
  these or grows forever chasing them — and needs a `filter_native=`
  escape hatch anyway, giving two ways to filter.
- **Translation bugs are silent and dangerous.** A filter that fails to
  apply doesn't error — it returns *more* rows than it should. In a
  multi-tenant system that is a data leak found in production, not a
  test failure.
- **The portability win mostly evaporates on inspection.** Backend
  migration is rare. Dev/prod parity doesn't need it (pgvector-docker →
  pgvector-prod is the same dialect). Swapping to FAISS for tests
  doesn't need it (FAISS has no filtering at all).

Same logical query — `tenant = "acme" AND created_at >= 1700000000 AND
doc_type IN (faq, manual)` — in each backend's native form:

**Qdrant** — condition-tree dict:

```python
filter={"must": [
    {"key": "tenant",     "match": {"value": "acme"}},
    {"key": "created_at", "range": {"gte": 1700000000}},
    {"key": "doc_type",   "match": {"any": ["faq", "manual"]}},
]}
```

Full vocabulary passes through: `must`/`should`/`must_not`;
`match.value|any|except|text`; `range`; `is_empty`, `is_null`,
`has_id`; `geo_radius`, `geo_bounding_box`; `nested`.

**pgvector** — SQL fragment + bound params (never interpolated, so no
injection path):

```python
filter={
    "where":  "tenant = %(tenant)s AND created_at >= %(since)s AND doc_type = ANY(%(types)s)",
    "params": {"tenant": "acme", "since": 1700000000, "types": ["faq", "manual"]},
}
```

Plus equality sugar for the common case, disambiguated by the absence
of a `where` key:

```python
filter={"tenant": "acme"}       # → WHERE tenant = %(tenant)s
```

**Milvus** — boolean-expression **string**, not a dict (this is why the
`filter` Param accepts `(dict, str)`):

```python
filter='tenant == "acme" && created_at >= 1700000000 && doc_type in ["faq","manual"]'
```

**Chroma** — Mongo-flavored dict:

```python
filter={"$and": [
    {"tenant":     {"$eq":  "acme"}},
    {"created_at": {"$gte": 1700000000}},
    {"doc_type":   {"$in":  ["faq", "manual"]}},
]}
```

**FAISS** — no filtering; anything but `None` raises:

```
ValueError: FAISS does not support metadata filtering. Its index holds
vectors only. Use pgvector or Qdrant for filtered search, or
pre-partition into separate FAISS indices and select via collection=.
```

Deliberately **no Python post-filtering fallback** — over-fetch-then-filter
silently breaks top-k, and a silent wrong-result is worse than a refusal.

**Every backend validates its own dialect shape and raises on anything
unrecognized.** A filter that quietly fails to apply is a tenant-isolation
leak; it must never degrade to "no filter".

The docs cost of choosing native over a DSL is one page:
`providers/vector_stores/README.md` carries exactly this table so the
native syntax is one click away rather than a trip to vendor docs.

### 5.5 · Backends

No LangChain, anywhere — every backend talks to the vendor's own client,
exactly as `providers/llms/` uses `anthropic` / `openai` directly.

| Backend | Ship | Client (official) | Extra | Async | `bound` |
|---|---|---|---|---|---|
| **FAISS** | **day one** | `faiss-cpu` (Meta) | `operonx[faiss]` | No — sync C++ | `cpu` |
| **pgvector** | **day one** | `psycopg[binary]` + pgvector ext | `operonx[pgvector]` | Yes — `AsyncConnection` | `io` |
| **Qdrant** | fast-follow | `qdrant-client` | `operonx[qdrant]` | Yes — `AsyncQdrantClient` | `io` |
| Milvus | later | `pymilvus` | `operonx[milvus]` | Yes — `AsyncMilvusClient` | `io` |
| Chroma | later | `chromadb` | `operonx[chroma]` | Yes — `AsyncHttpClient` | `io` |
| Weaviate | later | `weaviate-client` | — | Yes | `io` |
| MongoDB | probably never | — | — | — | — |

**Why FAISS + pgvector day one** (this ordering follows from §5.1):

- **pgvector** is the cleanest expression of the two-store rule. Your
  docs are already in Postgres; the vector table
  (`id, embedding, tenant_id`) sits beside the doc table; hydration is a
  `WHERE id = ANY(...)` against the *same database*. One system,
  transactional consistency, zero dual-write risk. Filtering is just SQL.
- **FAISS** needs no server, so CI runs without docker — and being
  vector-only by nature, it *proves* the ids-only contract instead of
  letting us quietly lean on payload.
- **Qdrant** is the right answer once you outgrow Postgres, but its
  headline feature (payload storage) is one §5.1 tells you not to use,
  so it loses its day-one claim.
- **MongoDB** vector search is Atlas-only (managed cloud); no
  self-hosted equivalent. Narrow audience.

### 5.6 · Backend ABC

```python
class BaseVectorStore(ABC):
    """Backend contract. `bound` is declared per-backend because FAISS is
    CPU-bound (local index) while every network-backed store is I/O-bound;
    VectorSearchOp reads it at init to pick the right thread pool."""

    bound: str = "io"        # FAISS overrides to "cpu"

    @abstractmethod
    async def search(self, query_vector, top_k, filter=None, collection=None) -> tuple[list, list, list]:
        """→ (ids, scores, metadata), index-aligned, score-ordered."""

    @abstractmethod
    async def upsert(self, ids, vectors, metadata=None, collection=None) -> None:
        """Write path. Declared now, wired to an op later — see §5.7."""
```

### 5.7 · Write path deferred (but designed for)

Vector search implies vector insert. Indexing is usually an offline
batch job, but the agent plan's memory subsystem will want on-the-fly
writes (store this turn's embedding).

**Decision:** put `upsert()` on the ABC now, ship only `VectorSearchOp`.
Add `VectorUpsertOp` when agent-memory concretely needs it — no rework,
because the backend contract is already right. Consistent with the
"no speculation" discipline in §3.

### 5.8 · `DocFetchOp` — the store of record

§5.1 makes hydration unconditional, which puts it on the critical path
of every RAG pipeline. That earns it a framework op (§3 bar, with the
reversal noted there) rather than leaving each user to rebuild
connection config and pooling beside the ones `ResourceHub` already
provides.

```python
# operonx/providers/ops/doc_fetch.py
class DocFetchOp(BaseOp):
    """Fetch records by primary key from a store of record.

    Returns rows in the SAME ORDER as the `ids` input — so a
    VectorSearchOp → DocFetchOp chain stays score-aligned without the
    caller doing anything. See §5.3.
    """

    type: OpType = "doc-fetch"

    inputs = {
        "ids":        Param(type=list, required=True),                   # list[str|int]
        "collection": Param(type=str,  required=False, default=None),    # table / collection
        "fields":     Param(type=list, required=False, default=None),    # projection; None = all
        "id_field":   Param(type=str,  required=False, default="id"),
    }
    outputs = {
        "rows":    Param(type=list),   # list[dict] — id-aligned with `ids`
        "missing": Param(type=list),   # list[str|int] — ids with no record
    }
```

**Two guarantees worth stating** — both are the reason this is an op and
not a snippet:

1. **`rows` is index-aligned with `ids`.** Backends restore order after
   the fetch. Callers never zip mismatched data.
2. **Missing ids surface explicitly** in `missing` rather than silently
   shortening `rows`. An index that has drifted from the store of record
   (deleted doc, failed sync) is a real condition; it should be
   observable, not invisible.

#### The boundary — stated so this doesn't become an ORM

`DocFetchOp` does **fetch-by-ids with optional projection. Nothing
else.**

| Supported | Not supported — write your own `@op` |
|---|---|
| Fetch N records by primary key | Joins across tables |
| Column/field projection (`fields=`) | Arbitrary `WHERE` clauses |
| Per-call `collection` selection | Writes, upserts, deletes |
| Missing-id reporting | Transactions |
| Order restoration | Custom SQL, stored procedures, query-in-YAML |

Anything past that line, users write a bare `@op` — the escape hatch is
preserved, and `providers/doc_stores/` clients stay importable for it.
Without this boundary written down, the op grows an ORM one feature
request at a time.

**No query strings in `resources.yaml`.** Resources carry connection
config only (DSN, pool size, credentials) — the same split operonx
already uses for LLMs, where the model config is a resource but the
prompt is a call-site argument. Business logic does not live in YAML.

#### Backends

| Backend | Ship | Client (official) | Extra | Fetch primitive |
|---|---|---|---|---|
| **Postgres** | **day one** | `psycopg[binary]` | `operonx[postgres]` | `SELECT … WHERE id = ANY($1)` |
| Mongo | later | `motor` / `pymongo` async | `operonx[mongo]` | `find({_id: {$in: ids}})` |
| Redis | later | `redis` (async) | `operonx[redis]` | pipelined `MGET` / `HGETALL` |
| DynamoDB | later | `aioboto3` | — | `BatchGetItem` |
| Elasticsearch | later | `elasticsearch[async]` | — | `mget` |

**Postgres day one only, deliberately.** pgvector users already run
Postgres, so the *same* docker-compose instance serves both the pgvector
integration tests and the `DocFetchOp` tests — one dependency, one CI
service, and the cleanest expression of the two-store model (one
database, two tables, no dual-write risk). Other backends land on
demand, each ~80–120 LOC behind the same factory pattern.

### 5.9 · No pre-composed `retriever()` graph (considered, rejected)

Since §5.3 documents `VectorSearchOp → DocFetchOp → RerankOp` as *the*
canonical shape, the obvious next step is to ship it as a `@graph`
factory so callers write one node instead of three. We're not doing
that. The composition ships as an **example**, not as API.

**Because retrieval isn't stable enough to freeze.** Roughly half of
real pipelines don't fit the fixed shape:

| Variant | Common? | Fits `search → fetch → rerank`? |
|---|---|---|
| No rerank (latency / cost) | Very | No |
| Hybrid: vector + BM25 → fusion | Increasingly standard | No |
| Multi-query (HyDE, expansion) → parallel search → dedupe | Common where quality matters | No |
| Multi-index (search N collections, merge) | Common in multi-tenant | No |
| Parent-document (search chunks, fetch parents) | Common | Partially — fetch key ≠ search id |
| Sentence-window (search sentence, fetch neighbours) | Common | No |
| Plain search → fetch → rerank | Most common single shape | Yes |

That's "most common variant", not "stable core" — the same distinction
that decided §2b.

**And the parameters explode.** Covering even a few variants needs
`vector_resource`, `doc_resource`, `rerank_resource`, `search_top_k`,
`rerank_top_k`, `rerank=True|False`, `filter`, `collection`, `fields`,
`id_field` — ~10 before hybrid or multi-query, at which point the call
is longer than the three nodes it replaced. That is the god-init this
whole plan exists to avoid.

**Why `build_react_agent` earns a factory and this doesn't.** The agent
plan *does* ship composed graphs, so the objection isn't "no composed
graphs" — it's stability. A ReAct loop (`llm → tools → llm → …`) is
~95% invariant across agent implementations, and the scaffolding it
hides (back-edge, reducers, shared cells) is genuinely fiddly. Retrieval
is ~50% invariant and the thing being hidden is three lines of ordinary
wiring.

**What ships instead:** the composition lives in
`examples/python/ex16_rag_pipeline/` (P1d) and as a copy-pasteable block
in `docs/guide/08-vector-search.md`. A recipe adapts to hybrid /
multi-query / no-rerank by editing; a signature has to be fought.

**When to revisit:** if one specific composed shape proves both stable
*and* painful. The likeliest candidate is **hybrid retrieval** — vector
+ BM25 + reciprocal-rank fusion — where the fusion math is fiddly and
invariant. That would be `build_hybrid_retriever(...)` as a factory, on
concrete demand. Never the generic `retriever()` umbrella.

---

## 6 · Callbot migration in detail

Callbot uses `TritonOp` in **one** place (STT) and has **zero** `OnnxOp`
call sites. (Earlier drafts of this plan claimed a callbot OnnxOp
classifier — that was wrong; `src/speech/denoise_classifier.py` is a
hand-written `BaseOp` subclass calling `onnxruntime` directly, entirely
outside the OnnxOp API.)

### `TritonOp(stt) → bare @op`

Before ([`src/callbot/graph.py:114`](../../educa-reminder-agent/src/callbot/graph.py#L114)):

```python
stt = TritonOp(
    resource="stt",
    inputs_map={"AUDIO_SIGNAL": "speech_audio"},
    outputs_map={"TRANSCRIPT": "transcript", "EMBEDDING": "embedding"},
    inputs={"speech_audio": stt_input["speech_audio"]},
)
```

After (in callbot, ~15 LOC):

```python
# in callbot: src/callbot/ops/stt.py
from operonx.core import op
from operonx.providers.triton.client import get_triton_client   # new helper

@op(bound="io")
async def stt(speech_audio):
    client = get_triton_client(url="…from resources.yaml…")   # module-level cached
    result = await client.infer(
        model_name="stt",
        inputs={"AUDIO_SIGNAL": speech_audio},
        outputs=["TRANSCRIPT", "EMBEDDING"],   # request BOTH — critical for denoise gate
    )
    return {
        "transcript": result["TRANSCRIPT"],
        "embedding":  result["EMBEDDING"],       # feeds DenoiseClassifier — MUST be requested
    }
```

**Critical: EMBEDDING must be requested explicitly.** Callbot's
`DenoiseClassifier` gates the whole noise-rejection path on
`stt["embedding"]` (see `denoise_classifier.py:76-78` — falls back to
`is_speech=True` silently if missing). The old `TritonOp` requested it
via `outputs_map`; the bare `@op` must too.

**Latency requirement:** `get_triton_client(url)` MUST return a
process-cached `InferenceServerClient` — building a fresh gRPC channel
per STT call would regress first-frame latency on the real-time hot
path. Today's `TritonOp` cache is at `providers/ops/triton.py:49-57`
(`_triton_clients: dict[str, client]`); the new
`providers/triton/client.py` inherits that cache verbatim.

### `OnnxOp(classifier) → nothing`

Nothing to migrate. Callbot has no `OnnxOp` call sites. Left in the
inventory (§4) purely for the case of external users who might have one.

---

## 7 · Module layout — before / after

**Before (operonx 1.0.0):**

```
operonx/providers/
├── llms/       {anthropic, openai, gemini, azure}.py + base, config, factory, batch_coordinator
├── embeddings/ {tei, vllm, huggingface, onnx}.py    + base, config, factory
├── rerankers/  {tei, vllm, onnx}.py                 + base, config, factory
├── onnx/       backend.py, config.py, factory.py   ← standalone ONNX backend (OnnxInferenceBackend class)
├── ops/
│   ├── llm.py
│   ├── embedding.py
│   ├── rerank.py
│   ├── onnx.py       ← DELETE (2.0.0)
│   └── triton.py     ← DELETE (2.0.0)
├── _utils/
│   ├── huggingface.py
│   └── onnx.py       ← already contains load_onnx_session(dir) → (session, tokenizer, device)
├── auth/
└── parsing.py
```

**After (operonx 2.0.0):**

```
operonx/providers/
├── llms/          (unchanged)
├── embeddings/    (unchanged)
├── rerankers/     (unchanged)
├── vector_stores/ {faiss, pgvector}.py day-one · {qdrant}.py fast-follow
│                  + base.py (ABC w/ `bound` attr), config.py, factory.py,
│                  README.md (filter dialects)                       ← NEW
├── doc_stores/    {postgres}.py day-one · {mongo, redis}.py later
│                  + base.py, config.py, factory.py,
│                  _reorder.py (reorder_by_ids)                      ← NEW
├── triton/        client.py, decode.py, dtypes.py                  ← NEW (low-level helper for user @ops)
├── onnx/          backend.py, config.py, factory.py                (unchanged — used by embeddings/onnx.py, rerankers/onnx.py, and user @ops)
├── ops/
│   ├── llm.py
│   ├── embedding.py
│   ├── rerank.py
│   ├── vector_search.py                                            ← NEW
│   └── doc_fetch.py                                                ← NEW
├── _utils/
│   ├── huggingface.py
│   └── onnx.py    (unchanged — hosts load_onnx_session)
├── auth/
└── parsing.py
```

**What's NOT in the layout (deliberate):**

- **No `providers/{classifiers,stt,tts}/` packages.** These would only
  earn their keep with concrete demand; today there is none (§3 table).
- **No `providers/_utils/audio.py`.** Only ships if a future audio op
  needs shared codec/resample helpers. Speculation-free.
- **No `providers/ops/{onnx_invoke,triton_invoke}.py` escape hatches.**
  User's own `@op` is the escape hatch; the low-level helpers make it
  cheap (~15 LOC per exotic model).
- **No STT/TTS/Classifier/OCR/VAD ops.** All belong in user code —
  either as bare `@op`s (STT/TTS/Classifier/OCR) or as separate
  subsystems (VAD, see §3 table).

---

## 8 · `OpType` enum cleanup

Ships with 2.0.0 (breaking, but only visible to code that reads `OpType`
directly).

| Current entry | 2.0.0 action | Reason |
|---|---|---|
| `"llm"`, `"embedding"`, `"rerank"` | keep | Match existing ops |
| — | **add `"vector-search"`** | Matches new `VectorSearchOp` |
| — | **add `"doc-fetch"`** | Matches new `DocFetchOp` |
| **`"interrupt"`** | **add** (currently missing from Literal but used as `InterruptOp.type`) | Fix drift |
| **`"emit"`** | **add** (currently missing from Literal but used as `EmitOp.type`) | Fix drift |
| `"milvus"`, `"mongo"`, `"s3"` | **remove** | Backend-named, no ops today |
| `"for"`, `"while"`, `"stream"` | **remove** | Replaced by back-edges + generators in 1.0.0 |
| `"parser"` | **remove** + retag `core/exceptions.py:92`'s `ParserError.op_type` to `"code"` | `ParserOp` removed in 1.0.0; only residual is the exception tag |
| `"onnx"`, `"triton"` | **remove** | `OnnxOp`/`TritonOp` deleted; ONNX/Triton are backends and helpers now |
| `"tool-executor"` | keep or migrate to `"tool"` | Aligns with `ToolOp` from `AGENT_EXTENSION_PLAN.md` |
| `"mcp"` | keep (reserved) | Future MCP client op |
| `"graph"`, `"branch"`, `"code"`, `"lambda"`, `"prompt"`, `"doc-processor"`, `"data"`, `"default"`, `"dummy"` | keep | Real infrastructure / control-flow types |

---

## 9 · Migration path

### operonx 1.1.0 — additive

- Ship `VectorSearchOp` + `providers/vector_stores/` package — `base.py`
  (ABC with `bound` attr + `search()`/`upsert()`), `config.py`,
  `factory.py`, and the **FAISS + pgvector** backends. Qdrant follows
  right after (not blocking).
- Ship `DocFetchOp` + `providers/doc_stores/` package — `base.py`,
  `config.py`, `factory.py`, `_reorder.py`, and the **Postgres**
  backend. Mongo/Redis on demand.
- ✅ **Shipped (PR #20)** `providers/triton/{client,decode,dtypes}.py`,
  extracted from `providers/ops/triton.py`:
  - `client.py` — `TritonClient.get(url)`, process-cached so the gRPC
    channel is reused; dict-in/dict-out `infer()`.
  - `decode.py` — bytes/str output decoding + single-element collapse.
  - `dtypes.py` — `DTYPE_MAP` + numpy coercion helpers.
- `OnnxOp` and `TritonOp` **remain functional** but emit a
  `DeprecationWarning` on `__init__`:

  ```
  OnnxOp is deprecated (removed in 2.0.0). Write a bare @op around
  operonx.providers._utils.onnx.load_onnx_session — returns
  (session, tokenizer, device_str) from a directory containing
  model.onnx + tokenizer.json. See OP_TAXONOMY_REFACTOR_PLAN.md §6.
  ```

  ```
  TritonOp is deprecated (removed in 2.0.0). Write a bare @op around
  operonx.providers.triton.client.get_triton_client(url) — reuses the
  same async gRPC client cache and dtype translation, in ~15 LOC.
  See OP_TAXONOMY_REFACTOR_PLAN.md §6 for the pattern.
  ```

- Consumers (callbot, others) migrate on 1.1.0 at their pace.
- `OpType` enum unchanged in 1.1.0 (backward-compat).

### operonx 2.0.0 — breaking cleanup

- **Delete `operonx/providers/ops/{onnx,triton}.py`.**
- **`OpType` enum cleanup** per §8 (remove stale + backend-named; add
  vector-search, interrupt, emit).
- **Retag `core/exceptions.py:92`'s `ParserError.op_type`** from
  `"parser"` to `"code"`.
- **Ship `MIGRATION.md` §Op-taxonomy** with the same recipes users saw
  in the 1.1.0 deprecation warnings.

### After 2.0.0 — backfill

- Add more vector store backends (pgvector, Milvus, Chroma) as demand
  appears — none block 2.0.0.
- Consider `SentimentOp` / `ModerationOp` / `SttOp` / `TtsOp` etc. ONLY
  when concrete demand shows up. Never on speculation.

---

## 10 · Phase roadmap

Small refactor. Additive work is parallelizable; deprecation + delete
are marker commits.

```
        P0   ✅ providers/triton/ helper                      (PR #20, merged)
Week 1  P1a     vector_stores ABC + factory + FAISS + VectorSearchOp
Week 1  P1b     pgvector backend (+ docker-compose CI)
Week 2  P1c     doc_stores ABC + factory + Postgres + DocFetchOp
Week 2  P1d     two-store RAG example + docs
Week 2  P2      Deprecation warnings on OnnxOp/TritonOp
                                                  ← Ship 1.1.0

Later   P3      Delete OnnxOp/TritonOp + OpType cleanup
                                                  ← Ship 2.0.0
Later           Qdrant backend · Mongo/Redis doc stores   (not blocking)
```

| # | Phase | Deliverable | Size | State |
|---|---|---|---|---|
| **P0** | Triton helper | `providers/triton/{client,decode,dtypes}.py` extracted from `providers/ops/triton.py`, preserving the module-global client cache. `TritonOp._process` 120 → 19 lines. 40 tests. | 1d | ✅ merged (PR #20) |
| **P1a** | VectorSearch core | `providers/vector_stores/{base,config,factory}.py` per §5.6 (incl. `bound` class attr) + `providers/ops/vector_search.py` + **FAISS** backend (no server → CI without docker; proves the ids-only contract). Unit tests. | 1.5–2d | ⏳ next |
| **P1b** | pgvector backend | `providers/vector_stores/pgvector.py` — async `psycopg`, SQL-fragment filter + equality sugar (§5.4). Integration tests against a docker-compose'd Postgres+pgvector in CI. | 1.5d | ⏳ |
| **P1c** | DocFetch | `providers/doc_stores/{base,config,factory,_reorder}.py` + **Postgres** backend + `providers/ops/doc_fetch.py` per §5.8. Order restoration + `missing` reporting are the load-bearing tests. Reuses P1b's docker-compose Postgres. | 1.5d | ⏳ |
| **P1d** | RAG example + docs | `examples/python/ex16_rag_pipeline/` showing the **two-store** shape end-to-end (EmbeddingOp → VectorSearchOp → DocFetchOp → RerankOp → LLMOp). `docs/guide/08-vector-search.md` + `providers/vector_stores/README.md` with the §5.4 filter table. | 1d | ⏳ |
| **P2** | Deprecation | `DeprecationWarning` on `OnnxOp.__init__` / `TritonOp.__init__` with the wording from §9. `CHANGELOG.md` entry. Ship 1.1.0. | 0.5d | ⏳ |
| **P3** | Delete + enum | Remove `operonx/providers/ops/{onnx,triton}.py`. Trim + extend `OpType` per §8. Retag `ParserError.op_type`. `MIGRATION.md` §Op-taxonomy. Ship 2.0.0. | 1d | ⏳ |
| **Follow-up** | Qdrant backend | `providers/vector_stores/qdrant.py` — condition-tree filter (§5.4). Not blocking 1.1.0. | 1d | — |

**1.1.0 remaining: ~6 days** (P0 done). 2.0.0 delta is trivial once
callbot has migrated.

---

## 11 · Honest gaps

The refactor is clean but not free.

1. **Loss of `TritonOp`'s escape-hatch role.** Users with exotic Triton
   models pay ~15 LOC of `@op` around `providers/triton/client.py`.
   Real cost, deliberately taken (§2). Competitors (BentoML, KServe,
   Ray Serve) keep a generic invoke op; operonx does not on principle.

2. **Vector store backend coverage will be incomplete on day one.** Two
   backends (FAISS + pgvector) is the minimum viable set; Qdrant lands
   right after. Users may want Milvus / Chroma / Weaviate. Mitigation:
   the factory pattern makes adding a backend a small isolated PR
   (~100–150 LOC). Document the recipe in
   `providers/vector_stores/README.md`.

2a. **Native filters mean graphs are backend-coupled.** Swapping
   pgvector → Qdrant rewrites every `filter=` in every graph. Accepted
   deliberately (§5.4): a portable DSL leaks, and its silent
   translation bugs land in a security-relevant path. The escape stays
   open — `filter=` can learn a DSL later (shapes are unambiguous:
   `{"$and": …}` vs Qdrant's `{"must": …}`) with native moving to
   `filter_native=`. Backward compatible.

2b. **`DocFetchOp`'s boundary will be pushed on.** Fetch-by-ids +
   projection covers the common case, but users will want joins,
   permission filters, soft-delete handling, decryption. The boundary
   in §5.8 is what keeps this from becoming an ORM — the escape hatch
   is a bare `@op`, and `providers/doc_stores/` clients stay importable
   for it. Expect to defend that line in review.

3. **Callbot's classifier stays in callbot forever.** `speech/denoise_classifier.py`
   is bespoke and doesn't map onto any framework op. That's honest —
   real-time audio-thread classifiers aren't graph ops.

4. **No `ClassifierOp` means callers with light classification needs use
   `LLMOp.of(fields=…)` (expensive) or hand-write a bare `@op` around
   ONNX (fast but per-user).** If concrete demand for a well-defined
   text classifier appears, ship `SentimentOp` / `ModerationOp`
   narrowly at that point — not the generic umbrella.

5. **Pre-existing bug not fixed by this plan:**
   `providers/rerankers/config.py` declares `RerankingType.COHERE` but
   `create_reranking()` has no COHERE branch (dead enum entry). Flag
   for follow-up PR; out of scope here.

6. **Decisions now settled (were open in earlier drafts):**
   - **Two-store model** — index carries vector + id + filterable
     metadata only; content lives in the store of record; hydration is
     an explicit user `@op`. §5.1.
   - **No `payloads` output** — `ids` / `scores` / `metadata`, index-aligned. §5.2.
   - **Filters are backend-native, no DSL.** §5.4.
   - **`filter` Param accepts `(dict, str)`** — Milvus's dialect is an
     expression string. §5.2.
   - **`collection=`, not `namespace=`** — matches Qdrant/Milvus
     vocabulary; `namespace` is Pinecone-specific.
   - **`bound` is a backend class attribute**, not fixed on the op —
     FAISS is `cpu`, network stores are `io`. §5.6.
   - **`upsert()` on the ABC now, `VectorUpsertOp` later.** §5.7.
   - **Day-one backends: FAISS + pgvector**; Qdrant fast-follow. §5.5.
   - **`DocFetchOp` ships alongside `VectorSearchOp`** — hydration is
     unconditional under §5.1, so it's on the critical path, not
     speculative. Reverses earlier drafts. §5.8 + §3.
   - **`DocFetchOp` guarantees id-order restoration and reports
     `missing` ids** rather than silently shortening `rows`. §5.8.
   - **`DocFetchOp` boundary: fetch-by-ids + projection only.** No
     joins, writes, transactions, custom SQL, or query-in-YAML. §5.8.
   - **No pre-composed `retriever()` graph.** The canonical composition
     ships as an example, not API — retrieval is ~50% invariant, so
     freezing it means a god-init. §5.9.
   - **Optional-deps naming: `operonx[faiss]`, `operonx[pgvector]`,
     `operonx[qdrant]`, `operonx[postgres]`** — backend name only,
     matching `operonx[onnx]`.
   - **Resource-key convention: `<backend>:<name>`**
     (`pgvector:docs`, `faiss:docs`, `qdrant:docs`) — matches how
     `embeddings/factory.py` dispatches on `EmbeddingType`.
   - **No LangChain dependency**, here or anywhere — every backend uses
     the vendor's own client, as `providers/llms/` already does. §5.5.

---

## 12 · Relationship to `AGENT_EXTENSION_PLAN.md`

Both plans are independent. This one is taxonomy hygiene + one new op;
the agent plan is building agent primitives on top. The overlap is one
line:

> §4 of `AGENT_EXTENSION_PLAN.md` says "RAG lives in providers/ops/,
> not agents/". This plan makes that concrete by shipping
> `VectorSearchOp` there. When the agent plan's memory subsystem lands,
> it consumes `VectorSearchOp` via its Ref, not by taking a dependency
> on the underlying vector store.

Nothing else in this plan depends on the agent plan or vice versa. Ship
in whichever order lands first.

---

## 13 · First concrete step

**P0 is done** — `providers/triton/{client,decode,dtypes}.py` landed in
PR #20 with the module-global client cache preserved and 40 tests.
`TritonOp._process` is down to 19 lines of name-mapping.

Design questions that were open before P1 are now settled in §11 item 6
— no separate ADR needed. Build order for **P1a** (next):

1. `providers/vector_stores/base.py` — `BaseVectorStore` ABC per §5.6,
   with the `bound` class attribute and both `search()` and `upsert()`
   declared (upsert unwired until §5.7).
2. `providers/vector_stores/config.py` — `VectorStoreType` enum +
   `VectorStoreConfig`, mirroring `embeddings/config.py`.
3. `providers/vector_stores/factory.py` — `create_vector_store(config)`
   with per-branch lazy imports and `_missing_extra_message`, mirroring
   `embeddings/factory.py`.
4. `providers/vector_stores/faiss.py` — FAISS backend. `bound = "cpu"`.
   Raises the §5.4 message on any non-`None` filter.
5. `providers/ops/vector_search.py` — `VectorSearchOp`, reading `bound`
   from the resolved backend.
6. Unit tests — index-alignment of `ids`/`scores`/`metadata`, score
   ordering, `top_k` honored, FAISS filter-raises, factory lazy-import
   error message.

Then **P1b** (pgvector, incl. the SQL-fragment + equality-sugar filter
shapes and docker-compose'd integration CI), **P1c**
(`providers/doc_stores/` + Postgres + `DocFetchOp` per §5.8 — the
load-bearing tests are order restoration and `missing` reporting;
reuses P1b's Postgres service), then **P1d** (the two-store RAG example
+ `providers/vector_stores/README.md` carrying the §5.4 filter table).

Ordering rationale: FAISS first because it needs no server (fast unit
tests, no docker in CI) and because being vector-only it *proves* the
ids-only contract from §5.2 rather than letting us lean on payload.

Docs land with P1c: `docs/guide/08-vector-search.md`,
`providers/vector_stores/README.md` (the §5.4 filter table + the
add-a-backend recipe), and a `docs/api/providers.md` update.

Everything after P1 is P2 (deprecation warnings) + P3 (2.0.0 cleanup).

---

## Sources studied

- `operonx/providers/ops/{llm,embedding,rerank,onnx,triton}.py` — current op shapes
- `operonx/providers/{llms,embeddings,rerankers}/{base,config,factory}.py` — reference pattern
- `operonx/providers/onnx/backend.py` — the `OnnxInferenceBackend` class (kept)
- `operonx/providers/_utils/onnx.py` — `load_onnx_session(dir) → (session, tokenizer, device)` — the actual location and signature of the ONNX helper users will call
- `operonx/providers/ops/triton.py` — source of the `providers/triton/` extraction (client cache lines 49–57, dtype map, output decoding lines 266–284)
- `operonx/core/configs/op_config.py` — the `OpType` Literal (missing `interrupt`, `emit`)
- `operonx/core/exceptions.py:92` — `ParserError.op_type = "parser"` residual to retag
- `operonx/core/ops/flow/{interrupt_op,emit_op}.py` — where `InterruptOp.type = "interrupt"` and `EmitOp.type = "emit"` are set but not in the Literal
- `operonx/providers/rerankers/{config,factory}.py` — pre-existing `RerankingType.COHERE` dead entry
- `/home/thanglq/educa-reminder-agent/src/callbot/graph.py` — the one `TritonOp` call site this refactor migrates
- `/home/thanglq/educa-reminder-agent/src/speech/per_call_audio.py` — VAD state model (`_init_vad_buffers`, `_CallCtx`) that grounds the "VadOp doesn't fit `PARENT.declare`" argument in §3
- `/home/thanglq/educa-reminder-agent/src/speech/denoise_classifier.py` — the ACTUAL callbot classifier (hand-written `BaseOp`, not `OnnxOp`) that earlier drafts of this plan misidentified
- `MIGRATION.md` (operonx 1.0.0) — deprecation-then-remove pattern
- Cross-check agents run 2026-08-10: factual accuracy check, callbot migration reality-check, adversarial design critique (findings incorporated in this rewrite)
