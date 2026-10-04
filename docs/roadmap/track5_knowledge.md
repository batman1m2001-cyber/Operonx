# Track 5: `operonx-kb`, a knowledge-base and document-intelligence package on OperonX

Status: design, 2026-10-04. Grounded in OperonX 1.14.0 (`/home/thanglq/Operon`, HEAD `a21082d`) and
operonx-studio (`/home/thanglq/operonx-studio`, HEAD `7de6d60`). The research is current to late 2026, and every
external claim cites its URL in §21. Nothing in either repo was modified.

---

## 0 · TL;DR

- **What it is:** a separate repo and package, `operonx-kb` (import `operonx_kb`), in a sibling folder
  `/home/thanglq/operonx-kb` with its own `git init`. It turns files into a versioned, provenance-preserving
  document model. It indexes that model several ways (dense, BM25, section tree, entity graph, page images) and
  answers questions with **span-level citations that resolve to page + bounding box**.
- **How it sits on OperonX:** every stage is an operonx op, and ingest, query and maintenance are `@graph`s.
  Batch ingestion runs as `Job`/`Runbook`, the query API as a `Service`, evals as `Eval`, and stores are
  `resources.yaml` keys registered through `REGISTRY.register`. It reuses `EmbeddingOp`, `RerankOp`,
  `VectorSearchOp`, `DocFetchOp`, `LLMOp`, `Media` and `MediaStore`. It adds no new runtime.
- **The load-bearing idea:** a **three-tier store**:
  1. **Catalog**: the store of record, in Postgres or SQLite. It holds documents, versions, the element tree,
     chunks, spans, enrichments and the graph.
  2. **Blob store**: raw files, page images and figure crops, content-addressed by SHA-256.
  3. **Derived indexes**: vectors, BM25, multivector and graph projections. They are droppable and rebuildable
     from tiers 1 and 2.

  This extends OperonX's own two-store rule ("the index is not a store", `OP_TAXONOMY_REFACTOR_PLAN.md:255`).
  **Hydration through the catalog is the consistency gate**, so incremental updates and deletes need no
  distributed transactions.
- **The one provenance invariant:** every version has one canonical text. Every element and chunk carries char
  spans into it, and every span maps to page regions. A citation is therefore a span, which makes it
  verifiable, and the eval labels are spans or quotes too, so they survive a change of chunker.
- **First backends:**
  - Store of record: SQLite (local/CI) and Postgres (prod) as catalog, and the local blob store.
  - Dense: FAISS and pgvector through the existing operonx backends.
  - Lexical: SQLite FTS5 and Postgres FTS.
  - Parsers: Docling (MIT) as the high-quality parser, plus zero-ML parsers for text, HTML, Office and the PDF
    text layer.
  - Qdrant (sparse and multivector) comes next.
- **Order of work:**
  1. Foundation: the model, ingest, incremental updates, deletes and rebuild.
  2. Hybrid retrieval, citations and eval.
  3. Studio integration.
  4. Contextual enrichment and tree retrieval.
  5. Visual page retrieval.
  6. Graph retrieval.

  Each phase passes a **measured gate** before the next starts.
- **Six upstream asks to operonx** (§19). The two that matter are `BaseVectorStore.delete()`/`VectorUpsertOp`,
  whose trigger in plan §5.7 has now been met, and plugin discovery for resource categories, which removes a
  silent raw-dict footgun.

---

## 1 · What OperonX gives us (read, not assumed)

### 1.1 Primitives the KB builds on

| Primitive | Where | What the KB uses it for |
|---|---|---|
| `@op` with `bound=`, `cache=`, `exclude=/include=`, `transient=`, `show_keys=`, `observe_max=` | `operonx/core/ops/transform/func_op.py:29-40` | Every stage. `bound="cpu"` for parsing and chunking. `exclude={"trace":[...]}` keeps whole documents out of traces. |
| Generator ops, `.parallel(max=N)`, `.collect()`, `max_pending` | `operonx/guide/03-control-flow.md:6-130` (`.parallel(max)` :82, `max_pending` :89) | Per-chunk enrichment fan-out with bounded concurrency, then a batched embed. |
| `.collect()` drops failed items; an empty generator never runs its collect | `operonx/guide/03-control-flow.md:79-81` | Design rule: enrichers **fail soft** (they never raise), and the fan-out generator **always yields ≥1 item** (§8.1). |
| An op that raises does not raise: its outputs are missing and the error lands in `$errors` | `operonx/guide/04-gotchas.md:7` | `commit_version` treats a missing input as failure and records status explicitly. Every graph test asserts `"$errors" not in out`. |
| `None` does not bind; merge inputs need defaults | `04-gotchas.md:193`, `03-control-flow.md:220-288` | All merge-point ops take `= None` defaults. |
| Inputs and outputs are traced as JSON | `04-gotchas.md:377` | Pass pydantic `.model_dump()`s or handles, and exclude heavy keys from the trace. |
| Loops via `PARENT.declare` plus an `if_(...).else_(op)` back-edge, capped at 1000 iterations | `03-control-flow.md:135-180`, `:349` | Tree search (beam over the section tree) and iterative retrieval. |
| `LLMOp` with `fields=` (structured), `stream=True`, `batch_mode=True` (OpenAI Batch, -50%) | `operonx/providers/ops/llm.py:231-273`, `:462` | Enrichers (context, summaries, entities), the tree-search policy, answers. Bulk enrichment uses batch mode. |
| Anthropic prompt caching on the system block | `operonx/providers/llms/anthropic.py:396` | Contextual retrieval: the section or document goes in the cached system prompt, and each chunk call pays only for the chunk. |
| Vision input: local image paths become base64 `image_url` | `operonx/providers/llms/base.py:374-405`, `llms/openai.py:83` | VLM parser fallback and page-image answering. |
| `EmbeddingOp` (`texts`→`embeddings`), backends openai/azure/gemini/tei/vllm/hf/onnx/triton | `providers/ops/embedding.py`, `providers/embeddings/config.py:7-15`, `embeddings/base.py:11,60` | Dense chunk and query embeddings, unchanged. |
| `RerankOp` (`query`,`documents`→`reranks`), backends tei/vllm/pinecone/hf/onnx | `providers/ops/rerank.py:19-39`, `rerankers/factory.py:29-53` | The rerank stage, unchanged. |
| `VectorSearchOp` returns ids, scores and metadata, **never content**; the backend `bound` is adopted at init | `providers/ops/vector_search.py`, `:104-118`; contract `vector_stores/base.py:3,31,60` | The dense index read path. |
| `DocFetchOp`: fetch by ids, order restored, `missing` reported | `providers/doc_stores/base.py:6,64`; Postgres/memory backends | Hydration from the catalog's `kb_chunks` table works as-is when the catalog is Postgres. |
| Vector backends FAISS/pgvector/Qdrant; native filters; no DSL | `vector_stores/config.py:91-97`; plan `OP_TAXONOMY_REFACTOR_PLAN.md:373-460` | Dense index backends. Filter policy (§12.3). |
| `REGISTRY.register(ConfigClass[_category], factory)` | `operonx/core/registry/config_registry.py:55` | The KB registers `kb_catalog:`, `parser:`, `lexical:`, `blob:`, `page_embedding:` and `graph:`. |
| `ResourceHub.get/warmup/health_check` | `core/registry/resource_hub.py:377,655,690` | Lazy backends; `operonx-kb doctor` health checks. |
| `Media(data, mime_type)`, auto-unwrapped for consumers | `operonx/core/media.py:27` | Page images and figure crops show in traces and in Studio (`/api/p/{pid}/media/{sha}`, studio `app.py:1983`). |
| `MediaStore` ABC (put/get/exists/delete/keys by SHA-256), `LocalMediaStore`, `detect_media` | `operonx/telemetry/media.py:264,292` | The KB blob store implements the same ABC, and `LocalMediaStore` is used directly. **Not** `ClickHouseMediaStore`: its blobs expire with runs (CHANGELOG 1.14.0), and a store of record must not expire. |
| `Job(source=DirSource, key=, on_error="record", concurrency=)`, `resume=True` | `app/jobs/job.py:98-118`, `app/jobs/sources.py:132-155` | Directory and bucket ingestion with resume by key. |
| `Runbook` (`a >> b`) | `guide/02-composition.md:130-155` | Reindex: build generation → eval gate → switch alias → GC. |
| `Service(http/websocket/webhook/schedule/asgi)`, `variants`, `playground` codec | `app/declare.py:96-200` | Ask API (http/ws), upload webhook, scheduled sync, **admin ASGI app** for Studio. |
| `Eval(dataset, evaluators, threshold)`, `llm_judge` | `app/evals.py:1-31,276,342` | Retrieval and answer evals that gate CI and reindex switches; they show in Studio's Evals tab. |
| `checkpoint`: an observer of cell writes (debug/replay) | `operonx/checkpoint/base.py:1-25` | **Not** used for durable ingestion resume. That job belongs to Job keys plus catalog status. |
| `agents.memory`: "Deliberately not a vector store" | `operonx/agents/memory.py:8-12` | The KB exposes a `kb_search` tool for agents instead of a MemoryProvider. |
| Op result cache: FNV-1a over JSON inputs, in-memory or file | `core/ops/base.py:850-866` | **Not** used for embeddings. It is keyed by neither model fingerprint nor durable store, so the KB keeps its own content-addressed caches (§11.3). |

### 1.2 Design positions OperonX already took, which the KB inherits

- **The index is not a store; hydration is unconditional** (`OP_TAXONOMY_REFACTOR_PLAN.md:255-290`). The KB
  makes this the consistency mechanism (§11).
- **Native filters, no portable DSL**, because translation bugs leak tenants (`:373-460`). The KB adds a *closed*
  filter set with conformance tests, not an open DSL (§12.3).
- **No pre-composed `retriever()` god-graph**, because retrieval is only about 50% invariant (`:604-655`).
  Hybrid retrieval is named as the likeliest stable factory (`:647-652`). The KB ships small factories with a
  stable *subgraph contract* (§9.1) plus recipes, never one umbrella.
- **No LangChain anywhere; vendor clients only** (`:461-466`). The KB borrows ideas from LlamaIndex and
  Haystack, never dependencies.
- **Four-criteria bar for a framework op** (`:158-176`). The KB is the layer where Parse, Chunk and Index ops
  *do* meet the bar, which is why they live here and not in operonx core.

### 1.3 Gaps found (each becomes an upstream ask or a KB-local shim)

| Gap | Evidence | Resolution |
|---|---|---|
| No `delete` on the vector store contract; `upsert` is unexposed | `vector_stores/base.py:31,60,69-71` (only `search`, `upsert`) | Upstream U1. Meanwhile a KB `DenseIndex` adapter calls backend clients for delete. |
| Unknown resource categories silently load as a **raw dict**, and the cached result persists | `core/registry/resource_hub.py:242-246`, cache at `:215-216` | Upstream U2 (entry-point discovery). KB: `import operonx_kb` registers at import time, and `operonx-kb doctor` fails loudly if a `kb_*:` key resolved to a dict. |
| Native provider citations are dropped: Anthropic text blocks are joined and `citations` discarded | `providers/llms/anthropic.py:232-235` | Upstream U3. Meanwhile the KB uses marker-based citations plus span verification (§10), which work on every provider. |
| Embeddings are dense-only, with no sparse or multivector output | `embeddings/base.py:11` (returns `{"embeddings"}`) | Sparse and multivector live in the KB (`SparseEncoder`, `PageEmbedder`) first. Upstream later as optional capabilities, per the plan's optional-field caveat (`:170-176`). |
| Dead `RerankingType.COHERE` enum with no factory branch | `rerankers/config.py:8` vs `rerankers/factory.py:29-53` | Upstream U5 (trivial, pre-existing; plan `:950-953`). |
| `MediaStore` lives under `telemetry` | `telemetry/media.py:264` | The KB imports it from there. Moving it is optional upstream U6. |

---

## 2 · The 2026 landscape: what we take, what we refuse

| System / idea | What it is (late 2026) | Take | Refuse |
|---|---|---|---|
| **OpenKB** (VectifyAI, Apache-2.0, v0.4.x) \[1\] | Compiles docs into a Markdown wiki of summaries, concept and entity pages with `[[wikilinks]]`, PageIndex for long PDFs, `add/watch/remove/lint/recompile`, query and chat with citations, skill export | *Compiled knowledge* as an **optional derived artifact** (wiki and skill export, phase 7). The CLI verbs. `lint` as KB health checks. | Markdown files as the store of record, which loses bbox and span provenance. LiteLLM; we have operonx providers. |
| **PageIndex** (vectorless tree RAG) \[2\]\[3\] | An LLM builds a ToC tree (title, page range, summary) and does reasoning-based tree search; vendor-reported 98.7% on FinanceBench | A **tree index derived from our element tree**, with an LLM summary per node; tree search as a bounded beam loop (§9.5). | Treating vectorless as a replacement. It is one retriever among several, chosen by a router and measured against hybrid on our data. Vendor numbers are not evidence for our corpus. |
| **Docling** (MIT code; weights Apache/CDLA/MIT; Linux Foundation AAIF 2026; Granite-Docling-258M VLM) \[4\]\[5\]\[6\]\[7\] | `DoclingDocument`: texts, tables, pictures and kv items; body vs furniture trees; JSON-pointer parent/children; provenance (page, bbox, charspan); `HybridChunker` (tokenizer-aware, heading-contextualized, merges peers) | **Primary high-quality parser.** Our Element model mirrors its shape (body/furniture, groups, prov with bbox and charspan), so the adapter is thin. The structural chunker borrows HybridChunker's algorithm. | Making `DoclingDocument` our core type. Parser-specific types stay at the adapter edge so MinerU, Marker, VLMs and cloud parsers fit the same model. |
| **MinerU 2.5 / 2.5-Pro** \[8\]\[9\] | Top OmniDocBench scores (90.67; Pro 95.69 on v1.6). License: custom Apache-based code with revenue and MAU thresholds; some VLM weights AGPL-3.0 | Opt-in `parser:mineru` adapter for hard layouts and scans. | A default or core dependency, because of the license (§13.3). |
| **Marker 2 / Chandra 2** (Datalab) \[10\]\[9\] | Code moved to Apache-2.0 (2026-07); Surya weights under modified OpenRAIL-M with $5M thresholds; JSON with bboxes | Opt-in adapter. | A default (weights license). |
| **Unstructured** \[11\] | Typed elements, `parent_id` hierarchy, deterministic element ids (hash of text, position, page, file), `chunk_by_title` | **Deterministic content-derived ids** (§6). Element-type vocabulary. | Its SDK as a dependency. A cloud-API adapter is fine later. |
| **LlamaParse v2** \[12\] | Tiers (fast/cost-effective/agentic/agentic-plus), **dated, pinned parser versions**, layout extraction add-on | **Pin parser versions into the fingerprint** (§6.3), so a parser upgrade is a visible reindex and never silent drift. A cloud adapter later. | |
| **Evidence Units** (arXiv 2604.00500) \[13\] | Grouping figures and tables with their context text makes retrieval parser-independent; Recall@1 0.15→0.51 on OmniDocBench | The structural chunker **attaches captions and referring paragraphs to tables and figures** ("evidence unit" chunks). | |
| **Contextual Retrieval** (Anthropic) \[14\] | Prepend a 50-100-token LLM context to each chunk before embedding and BM25. Failure rate -35% (embeddings), -49% (+BM25), -67% (+rerank); about $1.02/M doc tokens with caching | A `contextual` enricher (phase 4), **section-scoped by default** so incremental updates stay cheap (§11.4). It feeds both dense and BM25. | Making it a default before it beats the baseline on our eval set. |
| **Late chunking** (Jina, arXiv 2409.04701) \[15\] | Embed the whole document's tokens and mean-pool per chunk span; +2.7-3.6% nDCG@10 on BEIR, no LLM cost | A `LateChunkingEmbedder` for local HF/ONNX models that expose token embeddings (`embeddings/config.py` documents `output_name` for BGE-M3 token embeddings). Later. | |
| **Hybrid BM25 + dense + RRF + rerank** \[16\]\[17\] | Standard. Qdrant Query API fuses server-side (RRF and DBSF) and supports sparse and ColBERT multivectors in one collection | **The default retrieval mode.** RRF is a pure op (score-scale free). Qdrant server-side fusion is an optimization behind the same contract. | Linear score blending as the default; the scales differ. |
| **RAPTOR** (arXiv 2401.18059) \[18\] | Recursive cluster-and-summarize tree; collapsed-tree retrieval | Summary nodes are just **Chunks with `level>0` and children**, so collapsed-tree retrieval is plain dense search with no new index. Later. | |
| **GraphRAG / LazyGraphRAG** (Microsoft) \[19\]\[20\] | Full GraphRAG has costly LLM indexing plus community reports, and 1.0 added an `update` delta. LazyGraphRAG builds a noun-phrase co-occurrence graph (indexing cost about vector RAG, 0.1% of GraphRAG) and defers LLM work to query time | **The lazy (no-LLM) concept graph as the first graph build**, because it is incremental and cheap. The LLM entity graph is opt-in. | Index-time community summarization as a default (cost, and non-incremental). |
| **LightRAG** \[21\] | Entity and relation extraction; dual-level (low/high keyword) retrieval; incremental union of graphs | Dual-level query keywords via `LLMOp(fields=)`. Incremental graph merges keyed by normalized entity. | Its storage abstraction (we have the catalog). |
| **HippoRAG 2** (ICML 2025) \[22\] | Personalized PageRank over a passage-entity graph; +7% associative memory | **PPR over the entity-chunk bipartite graph** as the graph retriever's scorer. | |
| **ColPali / ColQwen, ViDoRe v3** \[23\]\[24\]\[25\] | Page images as multivector late interaction. ViDoRe v3: visual beats text retrievers at equal size, text rerankers help much more (+13.2 vs +0.2 NDCG@10), **hybrid text+image is best end-to-end** | A page-image index (phase 5) fused with text via RRF, with a text reranker on top. | Vision-only retrieval as the default. |
| **MUVERA** \[26\] | Fixed-dimensional encodings reduce multivector search to single-vector MIPS | An FDE prefetch option, so pgvector users get page retrieval without Qdrant. Later. | |
| **LlamaIndex IngestionPipeline** \[27\] | A docstore of `doc_id → hash`, with UPSERTS / DUPLICATES_ONLY / UPSERTS_AND_DELETE strategies and a node+transformation cache | Per-transformation content caches; delete-on-missing for "full sync" sources. | |
| **LangChain indexing API** \[28\] | A RecordManager with cleanup modes `None/incremental/full/scoped_full` keyed by source id and hash | `SyncMode` on connectors with these exact semantics (§11.5). | |
| **Haystack 2** \[29\] | DocumentWriter with `DuplicatePolicy` NONE/OVERWRITE/SKIP/FAIL | Explicit duplicate policy on `add()`. | |
| **pgvector 0.8 / ParadeDB** \[30\] | Iterative index scans for filtered HNSW, `halfvec`, `sparsevec`; BM25 in Postgres via ParadeDB | pgvector with `hnsw.iterative_scan` on for filtered search; `halfvec` for dimensions above 2000. ParadeDB as a lexical backend later. | |
| **LanceDB** \[31\] | Lance format, versioned tables, FTS, multivector, hybrid with RRF | A later embedded backend for dense, lexical and multivector in one local file. | |
| **ClickHouse vector** \[32\] | HNSW (usearch) vector similarity index, 25.8+ | Later candidate for very large or analytical corpora; the team already runs ClickHouse. | Being first: no transactional join with the catalog. |
| **Anthropic Citations** \[33\] | `char_location`, `page_location`, `content_block_location` and `search_result_location` citations; the cited text equals the referenced blocks | Our `Citation` shape is a superset (spans, pages, bboxes) so native citations map in once U3 lands. | |
| **Chroma chunking eval** \[34\] | Token-level recall, precision and IoU against relevant excerpts | **Span-based eval labels** (§15.3) that are independent of the chunker. | |
| **RAG eval practice** \[35\] | Recall@k for first stage; nDCG/MRR for rerank; synthetic fact→question generation with adversarial distractors; error analysis on traces first | The metrics set and the synthetic generator (§15.3). | Reference-free LLM metrics as the only gate. |

---

## 3 · Principles (each one rules something out)

1. **The catalog is the truth; every index is derived.** `DROP` any index, run `operonx-kb rebuild`, and you get
   the same results. This rules out content in vector payloads (operonx §5.1) and any state that lives only
   inside an index.
2. **Every byte of text a user sees has an address.** That address is `(version_id, char span)`, which maps to
   elements and then to page regions. This rules out chunk text that cannot be traced back. "Contextualized"
   text is stored *beside* the chunk's span text, never in place of it.
3. **Content-addressed everything.** Raw bytes, canonical text, elements, chunks, enrichment inputs and embedding
   inputs are all hashed. Unchanged content is never re-parsed, re-enriched or re-embedded. This rules out
   "re-index the document" as the update strategy.
4. **Fingerprint every transformation.** `(component, version, config)` hashes are stored with outputs, so a
   parser, chunker or model change is a **visible new generation**. It never silently mixes vectors from two
   models (operonx's own warning about BGE-M3 `output_name`, `embeddings/config.py` docstring).
5. **Stages are semantic interfaces with backend adapters** (operonx §2), and each has a canonical op. Pure
   algorithms (chunkers, fusion) are op parameters. Things with connections or models are `resources.yaml` keys.
6. **Small factories plus a stable subgraph contract, never a god-graph** (operonx §5.9). The Retriever
   contract is `(query, collection, filter, k) → hits`.
7. **Fail-soft enrichment, fail-loud integrity.** Enrichment is optional and degrades without failing (the same
   rule as `agents/memory.py:16-24`). A failed write, a span that does not round-trip or a filter that cannot be
   applied **raises**.
8. **Measured defaults.** No technique (contextual, tree, graph, visual) becomes a default without beating the
   hybrid baseline on the eval sets (§15). This follows the user's evidence-first rule.
9. **Library first, server second.** Everything works in a script with SQLite, FAISS and the local blob store.
   Postgres, Qdrant and the Studio tab are additive.

---

## 4 · Architecture

```
            ┌───────────────────────── INGEST (Job / webhook / API) ─────────────────────────┐
 Source ──► Connector ─► Plan ─► Fetch ─► Parse ─► Structure ─► Chunk ─► Enrich* ─► Embed ─► Index ─► Commit
 (dir,s3,   (list/sync,   (raw sha,  (blob     (Parser   (Element tree,  (Chunker,  (context,   (dense,   (writers   (catalog txn:
  url,api)   SyncMode)    diff,skip) store)    resource) canonical text, evidence   summary,    sparse,   per        flip active
                                                         spans, pages)   units)     entities,   page MV)  IndexSpec) version)
                                                                                     tree sums)
            └──────────────────────────────────────────────────────────────────────────────────┘
                     │ writes                                         │ derives
                     ▼                                                ▼
   ┌────────────── TIER 1: CATALOG (store of record) ─────────┐  ┌──── TIER 3: DERIVED INDEXES ───────┐
   │ collections, sources, documents, versions, pages,        │  │ dense (pgvector/FAISS/Qdrant)       │
   │ elements, chunks, version_chunks(spans), enrichments,    │  │ lexical (PG FTS/SQLite FTS5/…)      │
   │ entities, mentions, relations, index_generations,        │  │ page multivector (Qdrant/Lance)     │
   │ caches (embedding/enrichment), ingest_log                │  │ graph projection (PPR matrices)     │
   │ SQLite (local) | Postgres (prod)                         │  │ ids + filterable metadata only      │
   └──────────────────────────────────────────────────────────┘  └─────────────────────────────────────┘
   ┌────────────── TIER 2: BLOB STORE (sha256) ───────────────┐
   │ raw files, page images, figure crops, table images       │   MediaStore ABC (operonx.telemetry.media)
   └──────────────────────────────────────────────────────────┘

            ┌──────────────────────────────── QUERY (Service / library) ─────────────────────────────┐
 Request ─► Prepare (normalize, KBFilter, route) ─► Retrievers ∥ (dense | lexical | tree | graph | page)
          ─► Fuse (RRF) ─► Hydrate (catalog gate: active versions, ACL, text+provenance)
          ─► Rerank ─► Expand (parent/section/neighbors) ─► Context build ─► Synthesize ─► Verify citations
            └──────────────────────────────────────────────────────────────────────────────────────────┘

 MAINTENANCE (Runbook): reindex generation ─► Eval gate ─► switch alias ─► GC (index entries, blobs, cache)
```

---

## 5 · Core document model (`operonx_kb.model`, pure pydantic v2, no I/O)

### 5.1 Entities

```python
class Collection(BaseModel):
    id: str                       # slug, unique per catalog
    tenant: str | None = None
    spec: CollectionSpec          # pipeline + indexes + filterable fields (§7.9)
    active_generations: dict[str, str]   # index name -> generation id (blue/green alias)

class Source(BaseModel):          # where documents come from
    id: str; collection_id: str
    kind: Literal["dir", "s3", "url", "api", "gdrive", ...]
    uri: str; sync_mode: SyncMode = "incremental"   # none|incremental|full|scoped_full (LangChain semantics)

class Document(BaseModel):        # logical, stable identity across versions
    id: str                       # H(collection_id, key)  — key = user key or normalized source URI
    collection_id: str; key: str; source_id: str | None
    title: str | None; mime: str; tags: list[str] = []; acl: list[str] = []
    metadata: dict[str, Any] = {}            # only `spec.filterable` keys reach indexes
    active_version_id: str | None            # None == tombstoned / not yet committed
    deleted_at: datetime | None = None

class DocumentVersion(BaseModel): # immutable once committed
    id: str                       # H(document_id, raw_sha, pipeline_fp)
    document_id: str; ordinal: int
    raw_sha: str                  # sha256 of source bytes (blob key)
    text_sha: str                 # sha256 of canonical text
    pipeline_fp: str              # parser_fp + structure_fp + chunker_fp (§6.3)
    status: Literal["staged", "committed", "failed", "superseded"]
    stats: dict[str, Any]         # pages, elements, chunks, reused/new counts, cost_usd, durations
    error: str | None = None
    created_at: datetime

class Page(BaseModel):
    version_id: str; page_no: int  # 1-based
    width: float; height: float; unit: Literal["pt", "px"]
    image_sha: str | None          # rendered PNG in blob store (dpi in attrs)
    text_layer: bool               # False => OCR'd
```

### 5.2 Element tree (layout plus provenance)

```python
ElementKind = Literal[
  "document", "section", "title", "heading", "paragraph", "list", "list_item",
  "table", "figure", "caption", "formula", "code", "footnote", "kv",
  "page_header", "page_footer",   # furniture
]

class Region(BaseModel):          # where on a page
    page_no: int
    bbox: tuple[float, float, float, float]   # x0,y0,x1,y1 normalized to [0,1], origin top-left
    char_span: tuple[int, int] | None = None  # sub-span of the element's span this region covers

class Element(BaseModel):
    id: str                       # H(version_id, path_ordinal)  — positional, per version
    content_sha: str              # H(kind, normalized text, structural attrs) — survives versions
    version_id: str; parent_id: str | None; ordinal: int; depth: int
    kind: ElementKind; layer: Literal["body", "furniture"] = "body"
    level: int | None = None      # heading level
    text: str                     # exactly canonical_text[span[0]:span[1]]
    span: tuple[int, int]         # into DocumentVersion canonical text — THE invariant
    regions: list[Region] = []    # empty for non-paginated sources (html/md)
    attrs: dict[str, Any] = {}    # table: {"cells": [...], "html": ..., "n_rows": ...};
                                  # figure: {"image_sha": ..., "alt": ...}; code: {"lang": ...};
                                  # source anchors: {"html_xpath": ..., "docx_para": ..., "sheet": ..., "cell": "B7"}
    confidence: float | None = None   # OCR/layout confidence when the parser reports it
```

**Canonical text.** One deterministic serialization of the body tree per version: Markdown-flavored, with
headings as `#` lines and tables as GFM or linearized rows. The serializer is versioned (`serializer_fp`).
Every element's `span` points into it, and furniture is excluded but kept in the tree. The invariant
`canonical[e.span] == e.text` is checked at commit, **failing loud** (principle 7).

### 5.3 Chunks and versions

```python
class Chunk(BaseModel):
    id: str                       # H(document_id, chunker_fp, content_sha, occurrence)  — STABLE across versions
    content_sha: str              # sha256 of span text (what the user sees)
    document_id: str
    kind: Literal["text", "table", "figure", "evidence_unit", "page", "section_summary",
                  "doc_summary", "raptor_summary"]
    level: int = 0                # 0 = leaf; >0 = summary nodes (tree/RAPTOR); children in `child_ids`
    child_ids: list[str] = []
    heading_path: list[str] = []
    token_count: int
    embed_text_sha: str           # sha of the text actually embedded (context prefix + heading path + text)

class VersionChunk(BaseModel):    # chunk occurrence inside one version (offsets shift between versions)
    version_id: str; chunk_id: str; ordinal: int
    spans: list[tuple[int, int]]  # into that version's canonical text (non-contiguous allowed: evidence units)
    element_ids: list[str]
    pages: list[int]
```

Splitting the chunk into `Chunk` (stable content identity) and `VersionChunk` (per-version position) is what
makes incremental indexing cheap. An unchanged paragraph in v2 keeps its `chunk_id`, so its index entries, its
embedding and its enrichments are untouched. Only the span offsets move, and those live in the catalog.

### 5.4 Enrichment, graph, citation

```python
class Enrichment(BaseModel):      # any derived annotation; content-addressed on its inputs
    target_id: str; target_kind: Literal["chunk", "element", "version", "section"]
    enricher_fp: str; input_sha: str
    kind: str                     # "context_prefix" | "summary" | "questions" | "keywords" | "caption" | "entities"
    value: Any; cost_usd: float | None; model: str | None

class Entity(BaseModel):  id: str; collection_id: str; name: str; norm: str; type: str | None; description: str | None
class Mention(BaseModel): entity_id: str; chunk_id: str; span: tuple[int, int] | None; source: Literal["lazy", "llm"]
class Relation(BaseModel): id: str; src: str; dst: str; type: str; description: str | None
                           weight: float; evidence_chunk_ids: list[str]     # every edge cites evidence

class Hit(BaseModel):             # retriever output (pre-hydration it carries ids/scores only)
    chunk_id: str; score: float; rank: int; retriever: str
    scores: dict[str, float] = {} # per-retriever scores after fusion
    doc_id: str | None = None; version_id: str | None = None
    text: str | None = None; heading_path: list[str] = []; pages: list[int] = []
    regions: list[Region] = []; spans: list[tuple[int, int]] = []

class Citation(BaseModel):
    marker: int                   # [n] in the answer
    chunk_id: str; document_id: str; version_id: str; title: str | None
    spans: list[tuple[int, int]]  # narrowed to the supporting sentence(s) when verification finds them
    quote: str                    # exact canonical_text[span]
    pages: list[int]; regions: list[Region]
    support: Literal["verified", "partial", "unverified"]; support_score: float

class Answer(BaseModel):
    text: str; citations: list[Citation]; hits: list[Hit]
    unsupported_sentences: list[int] = []   # indices of answer sentences with no verified citation
    usage: dict[str, Any] = {}
```

---

## 6 · Identity, hashing, versions

### 6.1 Hashes (sha256, hex; text is NFC-normalized and whitespace-collapsed before hashing)

| Hash | Over | Used for |
|---|---|---|
| `raw_sha` | source bytes | Skip unchanged sources; the blob key; dedupe across collections |
| `text_sha` | canonical text | Detect "same text, new bytes" (re-saved PDF) and skip reparse-driven churn |
| `element.content_sha` | kind + normalized text + structural attrs | Element-level diff between versions |
| `chunk.content_sha` | span text | Stable `chunk_id` |
| `embed_text_sha` | exact embedded string | Embedding cache key, with `embedder_fp` |
| `enrichment.input_sha` | exact enricher input (chunk + scope context) | Enrichment cache key, with `enricher_fp` |

### 6.2 IDs

- These are deterministic and content-derived, as in Unstructured's element ids \[11\]. Re-running ingestion is
  idempotent by construction.
- They are 128-bit truncated hex with a typed prefix (`doc_`, `ver_`, `el_`, `ch_`, `ent_`) for readability in
  traces and Studio.
- Collisions within a `document_id` scope (identical paragraphs) are disambiguated by `occurrence`.

### 6.3 Fingerprints

Each component implements `fingerprint() -> str` as `H(class qualname, component_version, normalized config)`.
`pipeline_fp` combines parser, structure, serializer and chunker. `IndexSpec.fp` combines embedder (resource
config incl. `model`, `dimensions`, `output_name`), embed-text template, analyzer (lexical) and metric. Rules:

- A changed `pipeline_fp` means new versions on next sync. `operonx-kb plan` shows how many docs would reparse.
- A changed `IndexSpec.fp` means a new **index generation**, built beside the old one and switched after an
  eval gate (§11.6).
- Remote parsers pin a dated version (the LlamaParse-style pin \[12\]), which goes into the fingerprint.

---

## 7 · Stage interfaces (`operonx_kb.stages`)

All are ABCs with `fingerprint()`. Async where I/O-bound, and a `bound` class attribute exactly as in operonx
vector stores (`vector_stores/base.py:28`), so the op adopts it.

### 7.1 Connector

```python
class Connector(ABC):
    bound = "io"
    @abstractmethod
    def list(self, since: datetime | None = None) -> AsyncIterator[SourceItem]: ...   # key, uri, etag/mtime, size, mime
    @abstractmethod
    async def open(self, item: SourceItem) -> bytes | AsyncIterator[bytes]: ...
```

Day one: `DirConnector` (wraps operonx `DirSource` semantics, `app/jobs/sources.py:132`), `UrlConnector` and
`UploadConnector` (API/webhook). Later: S3/MinIO, Google Drive, web crawl.

### 7.2 Parser

```python
class Parser(ABC):
    bound = "cpu"                           # remote parsers override to "io"
    mimes: ClassVar[set[str]]
    @abstractmethod
    async def parse(self, data: bytes, *, mime: str, hints: ParseHints) -> ParsedDoc: ...
# ParsedDoc = parser-native-free intermediate: list[RawBlock(kind, text, page_no, bbox, parent_hint, attrs)],
#             pages: list[PageInfo], metadata (title, author, lang). Never a DoclingDocument.
```

Adapters: `plain`, `markdown`, `html` (selectolax), `docx`/`pptx`/`xlsx` (python-docx, python-pptx, openpyxl),
`pdf_text` (pypdfium2 text layer with char boxes, plus a line/block heuristic), `docling`, `vlm` (renders the
page and calls `LLMOp` with an image, `llms/base.py:374`), `mineru`, `marker`, `llamaparse`, `unstructured_api`.
A `ParserRouter` picks one by mime, by "has text layer?" and by size, with fallbacks: `docling → pdf_text → vlm`.

### 7.3 Structurer

`Structurer.build(parsed: ParsedDoc) -> (elements: list[Element], canonical: str, pages: list[Page])`. It
normalizes RawBlocks into the Element tree, nests sections by heading level, splits furniture, attaches captions
to figures and tables, serializes canonical text and assigns spans. It is a pure function, which makes it
**golden-testable**.

### 7.4 Chunker

```python
class Chunker(ABC):
    @abstractmethod
    def chunk(self, version: VersionView) -> list[ChunkDraft]: ...   # ChunkDraft = spans + element_ids + kind + heading_path
```

Built-ins:
- `structural`: the default. HybridChunker-like: walk sections, keep a tokenizer budget, split oversize
  elements at sentence boundaries, merge undersized same-heading peers, and keep tables whole or split by row
  groups with the header repeated.
- `evidence_unit`: figure or table plus caption plus referring paragraphs \[13\].
- `recursive`: a character or token splitter, as a baseline (Chroma showed it is strong when tuned \[34\]).
- `page`: one chunk per page (visual and tree hybrids).
- `sentence_window`: small chunks whose hydration expands to neighbors.

The token count uses the **embedder's tokenizer** when it is available (`tokenizer_path`), otherwise tiktoken
`cl100k`, recorded in the fingerprint.

### 7.5 Enricher

```python
class Enricher(ABC):
    scope: Literal["chunk", "section", "version", "collection"]
    @abstractmethod
    def inputs(self, target, view: VersionView) -> EnrichInput: ...      # determines input_sha (cache key)
    @abstractmethod
    async def run(self, inp: EnrichInput) -> EnrichOutput: ...           # never raises: errors -> output.error
```

Built-ins:
- `contextual` (Anthropic recipe, scope = section by default)
- `section_summary` and `doc_summary` (they feed the tree index)
- `questions` (HyDE-style hypothetical questions, indexed as extra dense entries pointing to the same chunk)
- `keywords`
- `figure_caption` (a VLM on figure crops)
- `table_summary`
- `entities_lazy` (noun phrases, no LLM)
- `entities_llm` (LightRAG-style extraction via `LLMOp(fields=...)`, `batch_mode` capable)
- `raptor` (cluster and summarize, later)

### 7.6 Encoders (index-side)

- **Dense:** reuse operonx `BaseEmbedder` through `EmbeddingOp` resources.
- **Sparse:** `SparseEncoder` (BM25 weights via the analyzer, SPLADE, or BGE-M3 sparse) → `{indices, values}`.
- **Page:** `PageEmbedder.embed_pages(images) -> list[np.ndarray[n_tokens, d]]` and
  `embed_query(text) -> np.ndarray[n_q, d]` (ColPali/ColQwen via `colpali-engine` locally, or an HTTP endpoint).

All of them carry `fingerprint()` and `dim`.

### 7.7 Index (write and read, derived)

```python
class Index(ABC):
    name: str; spec: IndexSpec; bound: str
    async def upsert(self, generation: str, entries: list[IndexEntry]) -> None   # IndexEntry = chunk_id + payload(filterable) + vector|sparse|multivector|text
    async def delete(self, generation: str, chunk_ids: list[str] | None = None, *, document_ids: list[str] | None = None) -> int
    async def search(self, generation: str, query: IndexQuery, k: int, filter: KBFilter) -> list[Hit]
    async def count(self, generation: str, filter: KBFilter | None = None) -> int     # used by verify/rebuild tests
    async def drop(self, generation: str) -> None
```

Implementations:
- `DenseIndex` wraps any operonx `BaseVectorStore` for search and upsert, with delete via U1 or a shim.
- `LexicalIndex` covers PG FTS, SQLite FTS5, ParadeDB, Qdrant sparse and Tantivy.
- `MultiVectorIndex` covers Qdrant and LanceDB.
- `TreeIndex` lives in the catalog: section nodes with summaries plus a doc-summary dense sub-index.
- `GraphIndex` uses catalog tables plus a cached sparse adjacency for PPR.

### 7.8 Retriever, Fuser, Reranker, Synthesizer, Verifier

- **Retriever**: a subgraph contract, `(query: QueryPlan, collection, filter: KBFilter, k) → hits: list[Hit]`
  (§9.1).
- **Fuser**: `rrf(lists, k=60, weights=None)` and `dbsf(...)`. Pure.
- **Reranker**: operonx `RerankOp`, unchanged. An `LLMReranker` (listwise via `LLMOp(fields=)`) is optional.
- **Synthesizer**: `ContextBuilder` plus `LLMOp` with a citation prompt. `stream=True` is supported.
- **Verifier**: `CitationVerifier` (lexical containment, then reranker-score entailment proxy, then optional
  LLM judge).

### 7.9 Collection spec (data, not code)

```python
class CollectionSpec(BaseModel):
    parser: str | ParserRouterSpec = "parser:auto"
    structure: StructureSpec = StructureSpec()
    chunker: ChunkerSpec = ChunkerSpec(kind="structural", max_tokens=512)
    enrichers: list[EnricherSpec] = []
    indexes: list[IndexSpec]                 # dense / lexical / tree / graph / page — named
    filterable: dict[str, Literal["keyword", "keyword[]", "int", "float", "datetime", "bool"]] = {}
    retention: RetentionSpec = RetentionSpec(keep_versions=5)
    render_pages: bool | Literal["auto"] = "auto"   # page PNGs for viewer + visual index
    language: str | None = None              # analyzer + prompts ("vi" enables word segmentation)
```

A spec lives in the catalog and in `kb.yaml` in the project, round-trippable, so Studio can show and edit it the
same way it edits `resources.yaml` today.

---

## 8 · OperonX ops and composed workflows

### 8.1 Op inventory (`operonx_kb.ops`)

| Op | Kind | `bound` | Notes |
|---|---|---|---|
| `plan_ingest` | `@op` | io | Resolves `Document`, computes `raw_sha`, compares it with the active version and `pipeline_fp`. Returns `action ∈ {skip, new, update}`. |
| `fetch_raw` | `@op` | io | Connector open → blob `put` (idempotent by sha). |
| `ParseOp` | `BaseOp` subclass, `resource="parser:..."` | from backend | Meets the four-criteria bar: complex I/O, rich span metadata (parser, pages, OCR'd pages), many backends. |
| `structure` | `@op(bound="cpu", exclude={"trace":["canonical"]})` | cpu | Element tree, canonical text, spans; invariant check. |
| `render_pages` | `@op(bound="cpu")` | cpu | pypdfium2 PNGs → blob store. It emits `Media(png, "image/png")` for the first N pages only (trace size). |
| `chunk_doc` | `@op(bound="cpu")` | cpu | `ChunkerSpec` → drafts → `Chunk`/`VersionChunk` plus the **diff** against the active version (`todo`, `reused`, `removed`). |
| `each_todo` | generator `@op` | sync | Yields each new chunk. **It yields a single `{"chunk": None}` sentinel when `todo` is empty**, because an empty generator never runs its `.collect()` (`guide/03-control-flow.md:80-81`). |
| `enrich_chunk` | `@op`/subgraph with `LLMOp` inside | io | Cache lookup first. On error it returns the chunk with `enrich_error`, never raises (`.collect()` drops raised items, `:79`). |
| `EmbedChunksOp` | `BaseOp` reusing `embedding:` resources | io | Embedding-cache lookup by `(embedder_fp, embed_text_sha)`; batches; writes the cache. |
| `encode_sparse`, `embed_pages` | `@op` | cpu/io | Only when those IndexSpecs exist. |
| `write_indexes` | `@op` | io | Upserts new entries per IndexSpec into the **active generation(s)** plus any building generation. Idempotent by `chunk_id`. |
| `commit_version` | `@op` | io | One catalog transaction: insert version, elements and version_chunks; flip `active_version_id`; mark the previous version `superseded`; enqueue GC of removed chunk ids. All inputs default to `None`, and any missing one becomes `status="failed"` with the reason. |
| `extract_graph`, `build_tree` | `@op` / subgraph | io | Post-commit, optional, idempotent by `input_sha`. |
| Query side: `prepare_query`, `dense_search`, `lexical_search`, `page_search`, `tree_search` (subgraph), `graph_search` (subgraph), `rrf`, `hydrate`, `expand`, `build_context`, `verify_citations` | mixed | | `dense_search` is `EmbeddingOp → VectorSearchOp` plus a filter compile. `hydrate` can be `DocFetchOp` against the PG catalog, plus an active-version and ACL gate. |

These pass **pydantic dumps of lightweight handles** (ids, counts, small previews) wherever a downstream op can
re-read from the catalog or blob store. Full trees and chunk lists pass in-process, but are excluded from traces
via `@op(exclude={"trace": [...]})` (`func_op.py:59-61`), with `show_keys` naming a preview such as `stats`.

### 8.2 Ingest graph (per-collection factory; wiring only)

```python
# operonx_kb/graphs/ingest.py   (ops in operonx_kb/ops/*.py; graph files only wire — operonx guide 05 rules)
from operonx import END, START, graph
from operonx.app.serve import egress, ingress
from operonx.core.ops import if_
from operonx_kb.ops import (ParseOp, EmbedChunksOp, plan_ingest, fetch_raw, structure, chunk_doc,
                            each_todo, enrich_chunk, write_indexes, commit_version, skipped, report)

def build_ingest_graph(spec: CollectionSpec):
    @graph
    def ingest(collection):
        src   = ingress()                                        # {"path","name"} from DirSource, or an upload
        plan  = plan_ingest(item=src["item"], collection=collection)
        raw   = fetch_raw(plan=plan["plan"])
        parsed = ParseOp.of(resource=spec.parser, blob=raw["blob"], plan=plan["plan"])
        tree  = structure(parsed=parsed["parsed"], plan=plan["plan"])
        ch    = chunk_doc(tree=tree["tree"], plan=plan["plan"])
        todo  = each_todo(chunks=ch["todo"])                     # always yields >= 1 (sentinel)
        enr   = enrich_chunk(chunk=todo["chunk"].parallel(max=8), tree=tree["tree"], plan=plan["plan"])
        emb   = EmbedChunksOp.of(resources=spec.dense_embedders(), chunks=enr["chunk"].collect())
        idx   = write_indexes(plan=plan["plan"], chunks=ch["all"], encoded=emb["encoded"])
        done  = commit_version(plan=plan["plan"], tree=tree["tree"], chunks=ch["all"], written=idx["written"])
        skip  = skipped(plan=plan["plan"])
        rep   = report(committed=done["version"], skipped=skip["reason"])   # merge: both default None
        out   = egress(item=rep["result"])
        START >> src >> plan >> if_(plan["action"] == "skip", skip).else_(raw)
        raw >> parsed >> tree >> ch >> todo >> enr >> emb >> idx >> done
        done >> rep
        skip >> rep
        rep >> out >> END
    return ingest
```

The factory has a *static* pipeline per collection, so the Studio canvas shows the real stages (parser name,
which indexes). This uses the existing `Service(variants=...)` and graph-factory support (`app/declare.py`
docstring at `Service`). Phase 1 includes a test asserting that a chain inside an `else_` arm merges at `rep`
exactly once. The guide documents the merge for single-op arms (`03-control-flow.md:220-288`), and
per the evidence rule the chain case gets verified, not assumed.

The run:

```python
APP = Application("kb", jobs=[
  Job("ingest_handbook", graph=build_ingest_graph(handbook_spec),
      source=DirSource("raw/handbook", pattern="**/*", recursive=True),
      key="path", inputs={"collection": "handbook"}, concurrency=4, on_error="record"),
], ...)
```

`operonx run ingest_handbook --resume` skips done keys (`guide/02-composition.md:125`). Within a key,
`plan_ingest` skips unchanged content.

### 8.3 Query graph (hybrid default; Retriever subgraphs plug in)

```python
@graph
def ask(collection):
    src   = ingress()                                   # {"query","filter","k","modes","answer":bool}
    q     = prepare_query(req=src["item"], collection=collection)   # KBFilter compile, route, rewrite
    d     = dense_retriever(plan=q["plan"])              # subgraph: EmbeddingOp -> VectorSearchOp
    l     = lexical_retriever(plan=q["plan"])            # subgraph
    f     = rrf(dense=d["hits"], lexical=l["hits"], k=q["k_fuse"])
    h     = hydrate(hits=f["hits"], plan=q["plan"])      # catalog gate: active version, tombstones, ACL
    rr    = RerankOp.of(resource="bge-reranker", query=q["text"], documents=h["texts"], top_k=q["k"])
    ctx   = build_context(hits=h["hits"], reranks=rr["reranks"], budget=q["ctx_budget"])
    ans   = LLMOp.of(resource="answerer", prompt=ANSWER_PROMPT, sources=ctx["sources"], question=q["text"])
    v     = verify_citations(answer=ans["content"], sources=ctx["sources"], plan=q["plan"])
    out   = egress(item=v["answer"])
    START >> src >> q
    q >> d
    q >> l
    d >> f
    l >> f                                               # f waits for both hard edges
    f >> h >> rr >> ctx >> ans >> v >> out >> END
```

The two retrievers run concurrently because nothing orders them. A "first one wins" variant for latency SLAs
uses the operonx soft edge `~` (`03-control-flow.md:289-345`). Streaming answers use a websocket door and
`LLMOp(stream=True)`, and citations are emitted as a final frame after verification.

### 8.4 Maintenance (Runbook)

```python
with Runbook("reembed_bge_m3_v2") as rb:
    build >> evaluate >> switch >> gc        # build gen (Job over chunks, embedding cache-aware),
                                             # Eval(threshold) gate, alias flip, drop old gen + GC
```

Other jobs:
- `gc`: index entries of unreferenced chunks, unreferenced blobs, cold cache rows.
- `verify`: index counts against the catalog per document, plus span round-trip sampling.
- `rebuild` (from catalog only).
- `sync` (connector list → SyncMode deletes).

`schedule(every="1h")` services can run `sync` (`app/declare.py:111`).

---

## 9 · Retrieval modes

### 9.1 The Retriever contract

A Retriever is any `@graph` with input `plan: QueryPlan` (query text and variants, `KBFilter`, collection,
`k`, generation ids) and output `hits: list[Hit]`, holding ids and scores only. Hydration is centralized after
fusion. That makes every retriever composable with `rrf` and swappable without touching anything else. It is
the "stable shape" operonx §5.9 asked for before freezing a factory. Factories exported:
`dense_retriever(index="dense")`, `lexical_retriever`, `hybrid_retriever` (dense + lexical + RRF; the one
operonx's plan predicted, `:647-652`), `tree_retriever`, `graph_retriever` and `page_retriever`. Each has
three to five parameters, never ten.

### 9.2 Dense

`EmbeddingOp(texts=[query, *variants])` → `VectorSearchOp` per variant with the compiled native filter
(§12.3). Query-instruction prefixes (E5/BGE "query:") come from `IndexSpec.query_template`. Over-fetch is
`k × 1.5` to absorb rows the catalog gate drops (§11.2), and the drop rate is reported in `Hit` stats so
drift is visible.

### 9.3 Lexical

The `Analyzer` is pluggable and fingerprinted:
- `simple`: Unicode NFKC, casefold, punctuation split.
- `vi`: pyvi/underthesea word segmentation, since Vietnamese multi-syllable words matter for BM25. This is
  relevant to the team's corpora (Edupia, educa).
- `en_stem`

Backends:
- PG FTS: a `tsvector` column fed by pre-analyzed tokens with the `simple` config, plus a GIN index. Ranking
  with `ts_rank_cd` is not BM25. It is good enough for P2, and ParadeDB BM25 comes later.
- SQLite FTS5, which has built-in `bm25()`.
- Qdrant sparse with IDF modifier.

### 9.4 Hybrid, fusion and rerank

- RRF with `k=60` is the default (rank-based, no score calibration \[16\]\[17\]). DBSF is optional.
- Rerank with a cross-encoder through `RerankOp`. The ViDoRe v3 finding that text rerankers add far more than
  visual ones \[25\] is one more reason the reranker always runs on **text**, including for page hits (via the
  page's text).
- Qdrant server-side fusion (prefetch dense and sparse, then fuse) is an *optimization* the `hybrid_retriever`
  factory can choose when both indexes are the same Qdrant collection. The output contract is identical.

### 9.5 Tree retrieval (PageIndex-style, built from our element tree)

**Index.** The section tree comes from headings (Element `level`). Where a document has no usable headings
(scans, slides), we **synthesize** structure with an LLM ToC pass over page-level text (the PageIndex approach
\[3\]). Each node holds `{node_id, title, level, page_range, span, summary}`, and summaries are generated
bottom-up by the `section_summary` enricher (cached by `input_sha`). Doc-level summaries also go into a small
dense sub-index for **document selection**.

**Search.** A bounded beam loop in operonx:

```python
@graph
def tree_search(plan):
    PARENT.declare(frontier=None, depth=0, picked=[], done=False)   # roots set by init on first pass
    init = select_docs(plan=plan)                                    # doc-summary dense search -> top D docs
    step = expand_frontier(plan=plan, frontier=PARENT["frontier"], roots=init["roots"],
                           depth=PARENT["depth"])                     # shows children (title+summary)
    pick = LLMOp.of(resource="navigator", prompt=NAV_PROMPT, fields=["choose: list", "enough: bool"],
                    question=plan["text"], options=step["options"])
    adv  = advance(choose=pick["choose"], enough=pick["enough"], frontier=step["frontier"],
                   depth=PARENT["depth"], picked=PARENT["picked"], max_depth=6, beam=3)
    adv["frontier"] >> PARENT["frontier"]; adv["depth"] >> PARENT["depth"]; adv["picked"] >> PARENT["picked"]
    to_hits = sections_to_hits(picked=adv["picked"], plan=plan)       # chunks inside picked section spans
    START >> init >> step >> pick >> adv >> if_(adv["done"] == True, to_hits).else_(step)
    to_hits >> END
```

The stop condition is computed in an op (`done`), as the guide requires (`03-control-flow.md:176`).
Budgets: `beam`, `max_depth` and the LLM call cap are part of the `QueryPlan`, and cost is visible per trace.
An **agentic variant** exposes `toc`, `open_node(id)` and `read(id, page_range)` as `@tool`s to
`build_react_agent` (`guide/01-ops.md` agents section) for interactive exploration. It is not the default
because its cost is unbounded.

### 9.6 Graph retrieval

**Build.** Default `entities_lazy`: noun-phrase or keyword extraction (language-aware, and for Vietnamese
segmenter n-grams), then co-occurrence edges within chunks and sections. No LLM, so it is incremental by
construction, in the spirit of LazyGraphRAG \[20\]. Opt-in `entities_llm` extracts typed entities and relations
with descriptions (LightRAG-style \[21\]). Entity resolution: normalized name plus embedding similarity above a
threshold. Every relation stores `evidence_chunk_ids`.

**Query.**
1. `LLMOp(fields=["entities: list", "themes: list"])` extracts low- and high-level keywords \[21\].
2. Match seed entities via an entity-name dense index.
3. **Personalized PageRank** over the entity-chunk bipartite graph, seeded by the matched entities (HippoRAG 2
   \[22\]), using `scipy.sparse` power iteration on a cached CSR adjacency per collection generation.
4. Chunks ranked by PPR mass become Hits. Relation descriptions can be added to the context as "graph facts",
   each citing its evidence chunks.

GraphRAG global community reports: **avoid** by default (§17).

### 9.7 Visual page retrieval

- `render_pages` (PNG at 144 dpi).
- `PageEmbedder` (ColQwen-family) produces multivector page embeddings into `MultiVectorIndex` (Qdrant
  multivector MaxSim first; LanceDB later; MUVERA FDE \[26\] single-vector prefetch for pgvector users).
- A page hit becomes `Hit(kind="page")`. The catalog maps the page to its elements, so the reranker and the
  synthesizer get the page's text, and citations can cite the page bbox or narrow to elements.
- Fused with text retrievers by RRF, since hybrid is best end-to-end on ViDoRe v3 \[25\].
- For figure-heavy answers, `build_context` can attach page images to the answer call, because vision input
  is already supported through `LLMOp` (`llms/base.py:374`).

### 9.8 Router

`prepare_query` picks modes:
- An explicit `modes=` in the request wins.
- Otherwise there is a rules table: quoted phrases or IDs lean lexical; "summarize section X" goes to tree;
  "how is A related to B" goes to graph; figure or chart words go to page.
- An optional `LLMOp(fields=["modes: list"])` classifier can decide instead.

The default is `hybrid`. The router's decision is a traced output, so its errors are diagnosable from Studio.

---

## 10 · Synthesis and citations

1. **Context build.** Dedupe hits, merge adjacent chunks of the same section (sentence-window or parent
   expansion through the element tree), pack into a token budget, and number the sources `[1..n]`, each with
   title, heading path and pages.
2. **Answer.** The prompt requires `[n]` markers after each claim, which is portable across every operonx
   provider. Structured mode is an alternative: `LLMOp(fields=["answer: str", "claims: list"])`, where each
   claim is `{text, sources:[n], quote}`.
3. **Verify** (`verify_citations`):
   - For each answer sentence with a marker, locate the best supporting span inside the cited source: exact or
     fuzzy quote containment first, then a reranker-score entailment proxy, then an optional `llm_judge`.
   - Narrow `Citation.spans` to that sentence and set `support`.
   - Sentences with no verified support go into `unsupported_sentences`. They are flagged and never silently
     dropped.
4. **Resolve.** span → `VersionChunk` → elements → `Region`s (page, bbox), so a click in Studio highlights the
   exact box on the page image.
5. **Native citations** (Anthropic `search_result` / `char_location` \[33\]) are a later *alternative path*,
   once operonx carries `citations` through `LLMOp` (U3; today they are dropped at
   `providers/llms/anthropic.py:232-235`). Mapping them in is direct: a search-result block is one source
   chunk, and a block index is a sentence or element.

---

## 11 · Incremental indexing, versioning, deletes, rebuild

### 11.1 Update flow

`plan_ingest`:
- If `raw_sha` is unchanged and `pipeline_fp` is unchanged, **skip** (no parse).
- If `raw_sha` changed but `text_sha` is unchanged after parse, commit the new version with **zero** index
  writes, because the chunks are identical.
- Otherwise `chunk_doc` diffs chunk ids against the active version:
  - `reused`: the same id; its index entries already exist.
  - `todo`: new ids, which are enriched and embedded (cache-aware).
  - `removed`: ids no longer referenced, queued for GC.

### 11.2 Consistency without distributed transactions

Index writes for `todo` happen **before** `commit_version` flips `active_version_id` in one catalog
transaction. Between the two:
- New entries are **invisible**, because `hydrate` joins hits to the catalog and drops any chunk that is not in
  an active version of a live document.
- Removed chunks stay **visible until the flip**, which is correct: the old version is still active.

After the flip, GC deletes removed entries asynchronously, and any garbage in between is filtered at
hydration. A crash at any point leaves the catalog consistent. Retrying is idempotent: upserts by `chunk_id`,
and commit checks the version id. This is operonx's "hydration is unconditional" principle
(`OP_TAXONOMY_REFACTOR_PLAN.md:280-290`) put to work. The cost is a small over-fetch, which is measured
(§9.2).

### 11.3 Caches (catalog tables, durable, content-addressed)

| Cache | Key |
|---|---|
| `embedding_cache` | `(embedder_fp, embed_text_sha) → vector` (`halfvec` in PG) |
| `enrichment_cache` | `(enricher_fp, input_sha) → value` |
| `parse_cache` | `(parser_fp, raw_sha) → ParsedDoc blob sha` |

They make reprocessing after a chunker change cheap: parsing and unchanged-chunk embeddings are reused. They
also make index generations cheap to rebuild. They are **not** the operonx op cache (`core/ops/base.py:850-866`
is FNV over JSON inputs, process-local or file-pickled, and knows no model fingerprint).

### 11.4 Contextual enrichment vs. incrementality

Context derived from the *whole document* invalidates every chunk's context when any byte changes. The
`contextual` enricher therefore defaults to **section scope**: the input is the section heading path plus the
section text, plus a doc-summary that is itself cached. An edit then re-contextualizes one section, not the
whole document. Document scope is available, and cost-estimated by `operonx-kb plan`.

### 11.5 Sources and deletes

`SyncMode` follows LangChain's record-manager semantics \[28\]:
- `none`: add and update only.
- `incremental`: delete documents of seen sources that vanished from the listing.
- `full`: delete everything not seen this run.
- `scoped_full`

A delete is two steps:
1. Tombstone: `deleted_at` is set and `active_version_id` becomes `None`, which is invisible at once through
   hydration.
2. GC purges index entries (`Index.delete(document_ids=...)`), graph mentions (relations whose evidence becomes
   empty are dropped), caches if unreferenced, and blobs by reference count.

`purge=True` (GDPR) runs both synchronously and then `verify`, asserting zero entries for that document in
every index and zero blobs. That gives a provable delete.

### 11.6 Index generations (blue/green)

An `IndexSpec.fp` change, such as a new embedder, creates generation `g2` beside `g1`:
1. A build Job runs over the catalog. No parsing is needed, and the embedding cache is reused across models
   only if the fingerprint matches.
2. An `Eval` gate runs on `g2` (`app/evals.py:342`, with `threshold=`).
3. The alias flips in `collections.active_generations`, so queries pick it up on the next request.
4. `g1` is dropped after a grace period.

Ingest during the build writes to both generations.

### 11.7 Version retention

The catalog keeps `keep_versions` versions per document: elements, spans and stats. Only the active version is
indexed. "What did the KB say on date D" works by querying a pinned version set, which hydrates spans from an
old version and re-embeds lazily if needed. That is later.

---

## 12 · Storage abstraction and backends

### 12.1 Interfaces

| Abstraction | ABC | Day one | Next | Later |
|---|---|---|---|---|
| Catalog (store of record) | `Catalog` (repositories: collections, documents, versions, elements, chunks, enrichments, graph, caches, log) with `transaction()` | **SQLite** (stdlib `sqlite3`, WAL), **Postgres** (psycopg3 pool, reusing operonx's `_pg.get_pool` pattern) | — | — |
| Blob | operonx `MediaStore` (`telemetry/media.py:264`) | `LocalMediaStore` | S3/MinIO (`blob:s3`) | GCS/Azure |
| Dense | operonx `BaseVectorStore` via `DenseIndex` | FAISS (CI and local), pgvector (same DB as catalog) | Qdrant | LanceDB, ClickHouse |
| Lexical | `LexicalIndex` | SQLite FTS5, PG FTS | Qdrant sparse, ParadeDB | Tantivy (local), OpenSearch |
| Multivector | `MultiVectorIndex` | — | Qdrant multivector | LanceDB; pgvector plus MUVERA FDE |
| Graph | `GraphStore` | catalog tables plus scipy CSR | — | Neo4j adapter only on concrete demand |

**Migrations.** These are versioned SQL files per dialect plus a `kb_schema_version` table, upgraded on first
use. This is the pattern operonx's ClickHouse store uses ("schema is at version 2 … upgrades on first use",
CHANGELOG 1.14.0). There is **no ORM**, consistent with operonx's anti-ORM boundary (`doc_stores/base.py:6-13`).

### 12.2 Postgres layout (prod default: one database, three roles)

```
kb_collections, kb_sources, kb_documents, kb_versions, kb_pages,
kb_elements          (version_id, id, parent_id, ordinal, kind, layer, level, span int4range, regions jsonb, attrs jsonb, content_sha)
kb_chunks            (id, document_id, content_sha, kind, level, heading_path text[], token_count, embed_text_sha, text)  -- text = span text of first version (display)
kb_version_chunks    (version_id, chunk_id, ordinal, spans int4range[], element_ids text[], pages int[])
kb_enrichments, kb_entities, kb_mentions, kb_relations, kb_index_generations,
kb_embedding_cache   (embedder_fp, sha, vec halfvec)       kb_enrichment_cache, kb_parse_cache, kb_ingest_log
kbx_dense_<gen>      (chunk_id pk, embedding vector|halfvec, collection, document_id, tags text[], acl text[], <filterable cols>)
kbx_lex_<gen>        (chunk_id pk, tsv tsvector, same filter cols)
```

Hydration is `DocFetchOp(resource="kb", collection="kb_chunks")` joined to the active version by a view
`kb_active_chunks`. The two-store-in-one-DB deployment gets transactional consistency for free (operonx plan
§5.5 rationale).

### 12.3 Filters: a closed `KBFilter`, compiled per backend, conformance-tested

```python
class KBFilter(BaseModel):
    collection: str                            # always present
    document_ids: list[str] | None = None
    tags_any: list[str] | None = None; tags_all: list[str] | None = None
    acl_any: list[str] | None = None           # principal ids of the caller
    mime_in: list[str] | None = None
    created_after: datetime | None = None; created_before: datetime | None = None
    fields: dict[str, Any] = {}                # ONLY keys declared in CollectionSpec.filterable; else raise
```

This is **not** the portable DSL operonx rejected (`OP_TAXONOMY_REFACTOR_PLAN.md:373-460`). It is a fixed,
small vocabulary over fields **the KB itself created** in every index, so each backend compiler is finite and
fully tested. The **tenant-leak conformance test** runs per backend: for each filter field, seed matching and
non-matching rows, and assert that non-matching rows never return and that unknown fields raise. The plan's
rule holds: "a filter must never silently degrade to no filter" (`vector_stores/base.py:45-48`). A raw native
filter is still accepted as `native_filter=` and **AND-ed** with the compiled one, never replacing it.

---

## 13 · Package structure, dependencies, extras

### 13.1 Module tree

```
operonx-kb/                               (sibling of Operon; own git; github.com/batman1m2001-cyber/operonx-kb)
├── pyproject.toml                        operonx>=1.14 core dep; extras below
├── PLAN.md                               this design, condensed (written before code — user rule)
├── AGENTS.md / CLAUDE.md
├── operonx_kb/
│   ├── __init__.py                       KnowledgeBase, CollectionSpec, IndexSpec, KBFilter, models; registers resources
│   ├── registry.py                       REGISTRY.register(...) for kb_catalog:, parser:, lexical:, blob:, page_embedding:, graph:
│   ├── model/                            ids.py hashing.py document.py element.py chunk.py enrich.py graph.py query.py citation.py
│   ├── text/                             canonical.py (serializer) spans.py tokenize.py analyzers/{simple,vi,en}.py sentences.py
│   ├── stages/                           connector.py parser.py structurer.py chunker.py enricher.py encoders.py index.py
│   │                                     retriever.py fusion.py synth.py verify.py   (ABCs only)
│   ├── connectors/                       dir.py url.py upload.py s3.py
│   ├── parsers/                          plain.py markdown.py html.py office.py pdf_text.py docling.py vlm.py
│   │                                     mineru.py marker.py llamaparse.py unstructured_api.py router.py
│   ├── structure/                        build.py sections.py captions.py furniture.py
│   ├── chunkers/                         structural.py evidence_unit.py recursive.py page.py sentence_window.py
│   ├── enrichers/                        contextual.py summaries.py questions.py keywords.py captions.py
│   │                                     entities_lazy.py entities_llm.py raptor.py
│   ├── encoders/                         sparse_bm25.py splade.py page_colqwen.py late_chunking.py
│   ├── stores/
│   │   ├── catalog/                      base.py sqlite.py postgres.py migrations/{sqlite,postgres}/NNN_*.sql
│   │   ├── blobs.py                      MediaStore re-export + S3MediaStore
│   │   ├── dense.py                      DenseIndex over operonx BaseVectorStore (+ delete shim until U1)
│   │   ├── lexical/                      base.py sqlite_fts.py pg_fts.py qdrant_sparse.py paradedb.py
│   │   ├── multivector/                  qdrant.py lancedb.py
│   │   └── graph.py                      catalog-backed graph + CSR/PPR
│   ├── filters.py                        KBFilter + per-backend compilers
│   ├── ops/                              ingest.py query.py maintenance.py (all @op / BaseOp; logic lives here)
│   ├── graphs/                           ingest.py query.py retrievers.py tree.py graphrag.py maintenance.py (wiring only)
│   ├── synth/                            context.py prompts.py citations.py
│   ├── eval/                             dataset.py labels.py metrics.py synth_qa.py evaluators.py
│   ├── admin/                            asgi.py (Starlette app for Studio) schemas.py
│   ├── app.py                            kb_application(...) helper -> operonx Application (jobs, services, evals)
│   ├── cli.py                            operonx-kb {init,add,sync,list,status,query,plan,rebuild,verify,gc,eval,doctor,export}
│   └── testing/                          fakes.py (HashEmbedder, ScriptedLLM, MemoryCatalog) golden.py conformance/
├── tests/  unit/ golden/ conformance/ integration/ eval/ graphs/
├── golden/ docs/ (fixtures, ≤ 30 files, licensed for redistribution) expected/ (snapshots)
├── datasets/ (eval JSONL — operonx Dataset format)
└── scripts/bench_*.py                    ingest throughput, query latency, index size, cost per 1k pages
```

### 13.2 Extras

| Extra | Pulls | Why separate |
|---|---|---|
| (core) | `operonx>=1.14`, `pydantic`, `numpy`, `tiktoken`, `pypdfium2` (Apache/BSD), `selectolax`, `regex` | Library-first: text, HTML, Markdown and PDF text layer; SQLite, FAISS-less brute force for tiny sets |
| `office` | `python-docx`, `python-pptx`, `openpyxl` | Office formats |
| `docling` | `docling` (MIT; permissive weights \[7\]) | ML layout and tables, about 1 GB of models |
| `faiss` / `pgvector` / `qdrant` | via `operonx[faiss]` / `operonx[pgvector]` / `operonx[qdrant]` | Reuse operonx extras |
| `postgres` | `psycopg[binary]`, `psycopg-pool` | Catalog |
| `vi` | `pyvi` or `underthesea` | Vietnamese analyzer |
| `graph` | `scipy` | PPR |
| `visual` | `colpali-engine`, `torch` | Page embeddings, heavy |
| `mineru`, `marker` | upstream packages | **Licensed opt-in** (§13.3) |
| `s3` | `aioboto3` | Blob store |
| `serve` | `operonx[serve]` | Admin ASGI, services |
| `all` | everything except `visual`, `mineru` and `marker` | Mirrors operonx's choice to skip torch in `all` (`pyproject.toml` comment) |

### 13.3 License policy

Defaults stay MIT/Apache/BSD. MinerU (revenue and MAU thresholds, AGPL VLM weights \[9\]) and Marker (OpenRAIL-M
weights with $5M thresholds \[9\]) are installable only through their extras. The adapters log a one-time
license notice, and `operonx-kb doctor` lists the licenses of installed parsers. These license facts come from
a 2026 comparison post \[9\] and **must be re-verified against upstream LICENSE files at adoption time**.

---

## 14 · Python API sketches

### 14.1 Library (script, notebook, tests)

```python
import operonx
from operonx_kb import KnowledgeBase, CollectionSpec, IndexSpec, ChunkerSpec, EnricherSpec, KBFilter

operonx.bootstrap(resources="resources.yaml")           # llm:, embedding:, vector_store:, kb_catalog:, parser:
kb = KnowledgeBase("kb_catalog:main")                    # sqlite:///.operonx/kb.db in dev, postgres in prod

handbook = kb.create_collection("handbook", CollectionSpec(
    parser="parser:auto",
    chunker=ChunkerSpec(kind="structural", max_tokens=480, contextualize_headings=True),
    enrichers=[EnricherSpec(kind="contextual", llm="llm:haiku", scope="section")],
    indexes=[IndexSpec.dense("dense", embedder="embedding:bge-m3", store="vector_store:kb"),
             IndexSpec.lexical("bm25", store="lexical:kb", analyzer="vi")],
    filterable={"department": "keyword", "year": "int"},
    language="vi",
))

rep = await handbook.add("raw/policy_2026.pdf", key="policy", metadata={"department": "hr", "year": 2026})
rep.action, rep.version_id, rep.stats["chunks_new"], rep.stats["chunks_reused"]

res = await handbook.query("Nhân viên được nghỉ phép bao nhiêu ngày?", k=8, answer=True,
                           filter=KBFilter(collection="handbook", fields={"department": "hr"}))
print(res.answer.text)
for c in res.answer.citations:
    c.marker, c.title, c.pages, c.regions[0].bbox, c.support, c.quote

hits = await handbook.retrieve("leave days", modes=["dense", "bm25"], k=20)   # no synthesis
await handbook.delete("policy", purge=True)                                    # provable delete (§11.5)
await kb.verify("handbook")                                                    # counts + span round-trips
```

These library calls run the same operonx graphs (`Operon(build_ingest_graph(spec)).run(...)`), so script runs
are traced exactly like served ones.

### 14.2 As an OperonX application (`app/main.py` in a product)

```python
from operonx.app import Application, Service, asgi, http, websocket, webhook, schedule
from operonx.app.jobs import Job, Runbook
from operonx.app.jobs.sources import DirSource
from operonx_kb.app import kb_admin_app, kb_evals
from operonx_kb.graphs import build_ingest_graph, build_ask_graph, sync_graph
from specs import handbook_spec

APP = Application("handbook_kb",
    services=[
        Service("ask",    http("POST", "/ask", port=8020),             graph=build_ask_graph(handbook_spec)),
        Service("ask_ws", websocket("/ask", port=8020), max_inflight=32, graph=build_ask_graph(handbook_spec, stream=True)),
        Service("upload", webhook("/kb/upload", port=8020),            graph=build_ingest_graph(handbook_spec)),
        Service("sync",   schedule(every="1h", port=8020),             graph=sync_graph(handbook_spec)),
        Service("kb_admin", asgi("/kb", port=8021), app=kb_admin_app("kb_catalog:main")),   # Studio talks to this
    ],
    jobs=[Job("ingest_handbook", graph=build_ingest_graph(handbook_spec),
              source=DirSource("raw/handbook", recursive=True), key="path",
              inputs={"collection": "handbook"}, concurrency=4, on_error="record"),
          *kb_evals(handbook_spec, datasets=["dataset:handbook_retrieval", "dataset:handbook_answers"])],
    trace=["trace_local:default"],
)
```

### 14.3 Extending (a third-party parser)

```python
from operonx_kb.stages import Parser, ParsedDoc
class MyOcrParser(Parser):
    mimes = {"application/pdf"}; bound = "io"
    def fingerprint(self): return fp(self, version="2026-10-01", config=self.cfg)
    async def parse(self, data, *, mime, hints) -> ParsedDoc: ...
register_parser("myocr", MyOcrConfig, MyOcrParser)    # -> resources.yaml: parser:myocr {api_type: myocr, ...}
# then: tests/conformance  ->  run_parser_conformance(MyOcrParser(...))   (span/bbox/page invariants)
```

---

## 15 · Testing strategy

### 15.1 Layers

| Layer | What | Runs |
|---|---|---|
| Unit | Hashing, ids, serializer, span utilities, analyzers, RRF, filter compilers, chunkers on synthetic trees | Every commit, under 30 s |
| **Invariants (property-based, hypothesis)** | `canonical[e.span] == e.text` for every element. Chunk spans ⊆ canonical. Every chunk of a paginated doc has ≥1 page. Element tree is acyclic and ordinals are contiguous. Ingesting twice gives **0 writes**. Deleting gives **0 residual entries** in every index. `rebuild()` index set == original. RRF is order-invariant to input list order. | Every commit |
| **Golden documents** | About 30 fixtures: born-digital PDF, two-column paper, scanned PDF, table-heavy financial PDF, slides, DOCX with nested lists, XLSX, HTML with nav furniture, Vietnamese PDF, a no-heading PDF. A snapshot of the normalized element tree (kind, level, text sha, page, bbox rounded to 0.01) plus chunk boundaries. `operonx-kb golden diff/update`, with tolerance on bbox and an exact match on structure. Per parser adapter. | Every commit (non-ML parsers); nightly (Docling and other ML parsers) |
| **Conformance suites** | One reusable suite per ABC (`Catalog`, `Index`, `LexicalIndex`, `MediaStore`, `Parser`), including the **tenant-leak filter test** and delete/rebuild semantics, the way operonx keeps a third-party transport gate | Every backend; Postgres and Qdrant in docker CI |
| Graph tests | Run each `@graph` with `Operon(...)` and fakes. Assert `"$errors" not in out` (`04-gotchas.md:7`). The branch-merge-of-chains test (§8.2). The empty-doc sentinel test (`03-control-flow.md:80`). | Every commit |
| **Incremental tests** | Edit one paragraph of a 100-page fixture and assert that embed calls equal the changed-chunk count, from fake-embedder counters **and** from trace span counts. Swap a page order. Re-save the PDF with new bytes and the same text. Change the chunker fingerprint, then reparse 0 and re-embed only changed chunk texts. | Every commit |
| Retrieval eval | §15.3 | Nightly, plus a CI gate on a small set |
| Benchmarks | `scripts/bench_ingest.py` (pages/min per parser, cost/1k pages), `bench_query.py` (p50/p95 per mode, against N chunks), index bytes per chunk | On demand. Numbers recorded in `docs/bench/*.md` before any perf decision (user's evidence rule) |

### 15.2 Fakes

- `HashEmbedder`: deterministic token-hash vectors with lexical-ish semantics, so retrieval tests are
  meaningful.
- `ScriptedLLM`: prompt-hash → response, with recorded cassettes for enrichers. The operonx guide recommends
  scripted model ops in tests (`guide/01-ops.md`, agents section).
- `MemoryCatalog`, `LocalMediaStore(tmp)`, and FAISS or brute-force dense.

No network in unit or graph tests.

### 15.3 Retrieval and answer eval sets

**Format.** Operonx `Dataset` JSONL (`app/evals.py:15-22`):

```json
{"id":"hb-017","input":{"query":"Nghỉ phép năm bao nhiêu ngày?","collection":"handbook"},
 "expected":{"relevant":[{"doc_key":"policy","quote":"12 ngày làm việc","page":4}],
             "answer":"12 working days"},"tags":["vi","factoid"]}
```

**Labels are quote- or span-anchored, not chunk ids.** At eval time a quote is resolved to the span in the
current active version. Chunker and parser changes therefore don't invalidate the set, and token-level IoU
works \[34\].

**Metrics** (as operonx evaluators returning `{passed, score, reason}`, `app/evals.py:24-31`):
- First stage: Recall@k (k = 5, 10, 20).
- After rerank: MRR and nDCG@10.
- Token-level precision, recall and IoU \[34\].
- Answer: correctness via `llm_judge` (`app/evals.py:276`), **citation precision** (verified/total) and
  **citation recall** (gold quotes covered).
- Unsupported-sentence rate.
- Cost and latency from traces.

**Sources of cases:**
1. Small redistributable public subsets: BEIR SciFact or FiQA (dense, hybrid), a FinanceBench sample (tree),
   ViDoRe v3 public tasks (visual) \[24\]\[25\], MuSiQue or 2Wiki (graph, multi-hop).
2. A **Vietnamese internal set** from the team's own documents.
3. **Synthetic generation** (`operonx-kb eval synth`): sample spans, have the LLM write a question answerable
   only from that span, plus adversarial near-miss questions \[35\]. Human spot-check before admission.
4. **Production promotion**: Studio's existing review → dataset flow (`/api/p/{pid}/review/run/{run}/dataset`,
   studio `app.py:2471`) turns real queries plus a reviewer's relevance marks into cases.

**Gates.** Every phase's "default on" decision and every generation switch requires the eval to pass (§18).
Results are per mode, so "hybrid vs dense vs tree" is a table, not an opinion.

---

## 16 · Studio integration

Studio extracts project IR in a subprocess under the project's own interpreter and imports nothing from it
(operonx-studio `README.md`). The KB keeps that rule: **Studio never imports `operonx_kb`.** It talks to the
project's `kb_admin` ASGI service.

### 16.1 Zero-code integration (phases 1-2; already works on today's Studio)

| Need | Existing Studio surface |
|---|---|
| Ingest pipeline visualized | Canvas of the factory-built ingest graph (per-collection static wiring, §8.2) |
| Run ingestion, see per-document status and resume | Jobs tab (`/api/p/{pid}/jobs`, `/jobs/{name}/run`, studio `app.py:2214,2287`) |
| Query traces per stage, with cost and latency | Traces: tree, flow and timeline (`app.py:1787-1894`); `show_keys` on retrieval ops (`ids`, `scores`) |
| Page images and crops in traces | `Media(png)` → `/api/p/{pid}/media/{sha}` (`app.py:1983`) |
| Query playground | Play tab on the `ask` http or ws door (`app.py:3135-3237`) |
| Retrieval and answer evals | Evals tab (`app.py:2821-2895`); datasets editable (`app.py:2915`) |
| Grow golden sets from production | Review queue → dataset (`app.py:2406-2471`) |

### 16.2 The Knowledge tab (phase 3; small Studio PR plus the KB admin API)

**Discovery.** Studio lists services (`/api/p/{pid}/services`, `app.py:2341`). A service whose
`GET <path>/.well-known/operonx-kb` returns `{"api": "operonx-kb/1", "collections": [...]}` gets a
**Knowledge** tab. Studio proxies requests to it with the user's session, and its access checks stay in force
(`access.py`). The contract is versioned (`api: operonx-kb/1`) so the two repos release independently.

**Admin API** (`operonx_kb/admin/asgi.py`, Starlette, read-mostly):
`GET /collections`, `GET /collections/{c}/documents?status&q&page`, `GET /documents/{id}` (versions, stats,
errors), `GET /versions/{id}/tree`, `GET /versions/{id}/pages/{n}` (image sha plus regions of elements and
chunks), `GET /chunks/{id}` (span text, embed text, enrichments, index presence per generation, neighbors),
`POST /collections/{c}/query` (mode toggles; returns per-retriever lists, fused, reranked, answer and
citations), `POST /documents` (upload), `DELETE /documents/{id}`, `POST /collections/{c}/reindex`
(starts a Runbook), `GET /collections/{c}/health` (verify counts, drift, orphan rate, cost to date).

**Screens:**
1. **Collections**: doc counts, chunk counts, generations, last sync, eval scores (from Evals), spec viewer
   and editor (`kb.yaml`).
2. **Documents**: a filterable table with status, version history and a version diff (elements
   added/removed/changed by `content_sha`).
3. **Document viewer**: page image with **bbox overlays** (elements colored by kind, chunks as outlines).
   Clicking an element shows its span text, and clicking a chunk highlights all of its regions across pages.
4. **Chunk inspector**: span text vs. embedded text (context prefix highlighted), token count, heading path,
   enrichments with model and cost, where it is indexed (dense, lexical and so on, per generation), and
   "query neighbors" (top-k similar chunks).
5. **Query playground with citations**: a query box, mode checkboxes, filter builder (from
   `spec.filterable`), and side-by-side lists (dense, lexical, tree, graph, page → fused → reranked). The answer
   shows clickable `[n]` markers that open the viewer on the cited page with the cited span's regions
   highlighted, plus unsupported sentences in amber. "Open trace" deep-links into the existing Traces view,
   since the run id is returned. "Save as eval case" uses the existing dataset rows API.
6. **Graph explorer** (phase 6): entity neighborhood, relations with evidence chunks.

UI work follows the user's rule: a feature branch in operonx-studio, with screenshots at desktop and phone
width after every change.

---

## 17 · Must-have / Later / Avoid

### Must-have (the foundation; nothing ships without these)

1. The document model with canonical text and the **span invariant**; deterministic content-derived ids;
   fingerprints.
2. Catalog (SQLite and Postgres) plus blob store plus migrations, with the catalog as store of record.
3. Parsers: plain, Markdown, HTML, Office, PDF text layer, Docling; a `ParserRouter` with fallback; a parse
   cache.
4. Structural chunker with evidence units for tables and figures; recursive baseline.
5. Dense (FAISS, pgvector) and lexical (SQLite FTS5, PG FTS) indexes; `KBFilter` with conformance tests.
6. Ingest graph and Job: skip unchanged, chunk diff, embedding cache, idempotent writes, catalog-flip commit.
7. Deletes (tombstone, GC, purge plus verify), `rebuild`, `verify`, index generations with an eval-gated
   switch.
8. Hybrid retriever (RRF) plus `RerankOp` plus hydration gate plus context builder plus answers with **verified
   span citations** resolving to page and bbox.
9. Eval: span- or quote-anchored datasets, retrieval and answer metrics as operonx evaluators, CI gate.
10. Golden docs, invariant tests and conformance suites; fakes; benchmarks scripted.
11. `operonx-kb` CLI (init, add, sync, list, status, query, plan, rebuild, verify, gc, eval, doctor) and the
    library API.
12. Admin ASGI API plus the zero-code Studio path; the Knowledge tab in phase 3.

### Later (on measured demand)

- Contextual enrichment (P4, default-on only if it wins on our eval).
- Tree index and tree search, and LLM ToC synthesis (P4).
- RAPTOR summaries.
- Late chunking.
- HyDE/question enrichment.
- Visual: page images, ColQwen multivector, Qdrant multivector, MUVERA FDE, VLM parser (P5).
- Graph: lazy concept graph, LLM entities, PPR retriever, graph explorer (P6).
- Qdrant hybrid (sparse) and server-side fusion; ParadeDB BM25; LanceDB embedded backend; ClickHouse vector
  for very large corpora.
- Connectors: S3, Google Drive, web crawl; `SyncMode` full and scoped_full.
- Native provider citations (after U3).
- ACL principals from an IdP.
- Version-pinned "as of date D" queries.
- OKF/Markdown wiki export and OpenKB-style compiled wiki plus SKILL.md export.
- An MCP server exposing `kb_search`/`kb_read`.
- `operonx.agents` tool pack (`kb_search` as a `@tool`).
- Cloud parsers: LlamaParse, Unstructured API.
- MinerU and Marker adapters (licensed opt-in).

### Avoid

- **Content in vector payloads**, or any index treated as the source of truth (operonx §5.1).
- **LangChain, LlamaIndex or Haystack as dependencies** (operonx §5.5). We borrow ideas only.
- **A god `retriever()` or `rag()` graph** with a long kwargs list (operonx §5.9). Use factories plus the
  subgraph contract.
- **An open portable filter DSL.** Use the closed `KBFilter` with conformance tests, and AND native filters
  onto it.
- **Re-index-the-document as the update strategy**; whole-document-scoped contextual enrichment as the default.
- **GraphRAG index-time community summarization** as a default (cost, non-incremental \[19\]\[20\]).
- **Neo4j or any graph DB dependency** before catalog tables plus scipy are proven insufficient by a
  benchmark.
- **`DoclingDocument` (or any parser type) as the core model**; parser types stay at adapter edges.
- **Non-permissive parsers or weights in defaults** (MinerU, Marker) \[9\].
- **Building our own OCR, layout or embedding models**; fine-tuning embedders inside the package.
- **Pickle-based caches**; the operonx op cache for embeddings; operonx `checkpoint` as durable ingest state.
- **Vision-only or vectorless-only defaults** without eval evidence \[25\].
- **Studio importing the KB package**; the HTTP contract only.
- **`print()`**: use operonx `LOGGER`.
- Rust (operonx-rs is dropped).

---

## 18 · Phased roadmap (each phase has a measured gate)

| Phase | Scope | Gate (measured, recorded in `docs/bench/` or Eval run records) |
|---|---|---|
| **P0 · Plan and skeleton** (≈1 wk) | `PLAN.md` committed first. Repo at `/home/thanglq/operonx-kb` (own git, sibling of Operon). Model, hashing, ids, canonical serializer, span utilities, fakes. Registry hookup. CI. | Span invariant holds on 100% of non-ML golden docs. `import operonx_kb` registers every category (`doctor` proves no `kb_*:` key resolves to a raw dict, `resource_hub.py:242-246`). |
| **P1 · Foundation ingest** (≈3 wk) | Catalog (SQLite, PG), blob store, parsers (plain, HTML, MD, Office, `pdf_text`, Docling), structurer, structural and recursive chunkers, dense (FAISS, pgvector), ingest graph and Job, chunk diff, embedding and parse caches, commit flip, tombstone/GC/purge, `rebuild`, `verify`, CLI basics. Upstream U1 and U2 PRs. | (a) Re-ingest of an unchanged 200-doc corpus makes **0** embed calls and 0 parses (trace span counts). (b) A one-paragraph edit in a 100-page doc makes re-embeds equal to the changed chunks (≤3). (c) Purge leaves **0** entries in every index and 0 orphan blobs. (d) Rebuild matches the original id set and top-10 results on 50 queries. (e) Ingest throughput on golden PDFs measured for pdf_text and Docling, no target, just recorded. |
| **P2 · Retrieval, citations, eval** (≈3 wk) | Lexical (SQLite FTS5, PG FTS, `vi` analyzer), RRF hybrid, `RerankOp`, hydration gate, expansion, context builder, answer graph, citation verification and resolution, `KBFilter` and the conformance tenant-leak test, Eval datasets (2 public subsets plus a ≥100-case Vietnamese internal set), metrics, CI gate. | Baseline table: dense vs. lexical vs. hybrid vs. hybrid+rerank on all sets. **Hybrid becomes default only if it beats dense on Recall@10 on ≥2 of 3 sets**, otherwise dense is the default and the table is published. Citation precision ≥0.9 on the answer set, verified by a human on 30 samples. Query p95 recorded per mode. |
| **P3 · Studio** (≈2 wk) | Admin ASGI API (`operonx-kb/1`), Studio Knowledge tab (collections, documents, viewer with bbox overlays, chunk inspector, query playground with clickable citations, trace deep links, save as eval case). | User acceptance with screenshots at desktop and phone width. A cited answer opens the correct page and box on 20/20 sampled citations. |
| **P4 · Enrichment and tree** (≈3 wk) | `contextual` (section scope; Anthropic caching or OpenAI batch), section and doc summaries, tree index and beam tree search, LLM ToC synthesis for heading-less docs, optional RAPTOR. | Contextual and tree each compared with the P2 default on the same sets, plus cost per 1k pages and query cost. A technique turns **default-on per collection type only with a measured lift** (for example, the tree mode is routed for long structured docs only if it wins there). |
| **P5 · Multimodal** (≈3 wk) | Page rendering, `PageEmbedder` (ColQwen), Qdrant multivector, page retriever plus text fusion, figure captions, VLM parser fallback. | ViDoRe v3 public subset plus an internal scanned or slide set: hybrid text+page vs. text-only. Storage bytes per page recorded. |
| **P6 · Graph** (≈3 wk) | Lazy concept graph, optional LLM entities and relations, entity resolution, PPR retriever, graph facts in context, graph explorer. | Multi-hop subset (MuSiQue or 2Wiki) plus internal "relationship" questions: graph+hybrid vs. hybrid. Stays **experimental** unless it wins. Index cost recorded (lazy vs. LLM). |
| **P7 · Hardening and reach** (rolling) | S3, Drive and crawl connectors; SyncMode full and scoped; ACL principals; Qdrant hybrid; ParadeDB; LanceDB; MCP server; agent tool pack; wiki and skill export; late chunking; native citations after U3. | Per item, behind its own measured gate. |

Branching follows the user's workflow: one branch per phase, commits batched, merged when the phase gate is
recorded. Paid probes (LLM enrichment and judges) run inside a phase, never before the plan is written.

---

## 19 · Upstream asks to operonx (judged on operonx's merits; separate PRs to `/home/thanglq/Operon`)

| # | Ask | Evidence | Why it is right for operonx itself |
|---|---|---|---|
| **U1** | Add `BaseVectorStore.delete(ids=None, filter=None, collection=None)` for FAISS (id map), pgvector and Qdrant. Ship `VectorUpsertOp` and `VectorDeleteOp`. | `vector_stores/base.py:31,60` has no delete. `:69-71` defers `VectorUpsertOp` "until … concretely needs it". | Plan §5.7's trigger (concrete demand) is met. Without delete, no operonx user can keep an index consistent with deletes. |
| **U2** | Plugin discovery for resource categories: an `operonx.resources` entry-point group loaded alongside `_load_builtin_providers`. Make an unknown category in a typed position an error or warning, not a raw dict. | `core/registry/resource_hub.py:44-51` (built-ins only), `:242-246` (raw dict fallback, cached at `:215-216`) | Any third-party package (KB, studio plugins) hits the same silent failure. Today it is order-dependent. |
| **U3** | Carry provider-native citations: Anthropic `citations` on text blocks into `extras["citations"]` with block offsets; pass `search_result` / `document` content blocks through `_convert_messages`. | `providers/llms/anthropic.py:232-235` (text joined, citations dropped) | Grounded generation is a first-class LLM capability and `extras` already exists for uncommon fields (`ops/llm.py:189`). |
| **U4** | (Later) Optional `sparse` output capability on `EmbeddingOp` for BGE-M3 and SPLADE backends. | `embeddings/base.py:11` | The plan's own optional-field caveat (`:170-176`). Wait until the KB proves the shape on two backends. |
| **U5** | Remove or implement the dead `RerankingType.COHERE`. | `rerankers/config.py:8` vs `factory.py:29-53`; plan `:950-953` | Pre-existing bug. |
| **U6** | (Optional) Move `MediaStore`/`LocalMediaStore`/`detect_media` from `telemetry` to a neutral module (re-exported). | `telemetry/media.py:264-292` | It is a general content-addressed blob store, not telemetry. |

The KB pins `operonx>=1.14` and ships shims for U1 and U2 so it does not block on upstream releases. The shims
are deleted when the upstream versions land.

---

## 20 · Risks and the decisions left open

**Decided here** (so they are not re-litigated):
- Package name `operonx-kb`.
- The three-tier store.
- The span invariant.
- The chunk/version-chunk split.
- The closed `KBFilter`.
- RRF default fusion.
- Section-scoped contextual enrichment.
- Lazy graph first.
- Catalog-table graph.
- SQLite and Postgres catalogs.
- No ORM.
- Docling as primary ML parser.
- Studio via the HTTP contract.
- Per-collection graph factories.

**Risks:**
1. **Docling CPU cost and model download on CI.** Mitigation: ML parsers run in nightly golden runs, and P1
   records throughput before any performance work.
2. **Postgres FTS is not true BM25.** Accepted for P2, and the P2 eval decides whether ParadeDB, Qdrant sparse
   or Tantivy moves up.
3. **The Vietnamese analyzer's quality** depends on the segmenter. It is measured on the internal set; `simple`
   stays the fallback.
4. **Branch-merge with chain arms and other unverified operonx semantics.** Each has a P1 test (§8.2). If a
   semantic differs, the graph is restructured, not worked around silently.
5. **Eval set representativeness.** Without the internal Vietnamese set, every gate measures the wrong shape
   (the user's "benchmark must match the shape" rule). Building it is in P2 scope, not optional.

**Genuinely the user's call (one item):** whether the first production collection is an Edupia/educa corpus,
which sets which internal eval set gets built first. The design does not depend on the answer.

---

## 21 · Sources

1. OpenKB, VectifyAI: https://github.com/VectifyAI/OpenKB ; overview https://knightli.com/ja/2026/05/17/openkb-llm-knowledge-base/
2. PageIndex overview: https://lilting.ch/en/articles/pageindex-llm-tree-rag ; https://yuv.ai/blog/pageindex
3. PageIndex repo: https://github.com/VectifyAI/PageIndex
4. DoclingDocument concepts: https://docling-project.github.io/docling/concepts/docling_document/
5. Docling paper: https://arxiv.org/pdf/2501.17887
6. Docling HybridChunker: https://docling-project.github.io/docling/_generated/examples/hybrid_chunking/
7. Granite-Docling / Docling 2026: https://www.dbta.com/Editorial/News-Flashes/IBM-Releases-New-Granite-Docling-Model-to-Deliver-End-to-End-Document-Understanding-171525.aspx ; https://aitoolsatlas.ai/tools/docling/changelog
8. MinerU2.5: https://neurohive.io/en/state-of-the-art/mineru2-5-open-source-1-2b-model-for-pdf-parsing-outperforms-gemini-2-5-pro-on-benchmarks/ ; MinerU2.5-Pro https://arxiv.org/abs/2604.04771 ; OmniDocBench https://arxiv.org/abs/2412.07626
9. Parser licenses (Docling/MinerU/Marker, 2026): https://particula.tech/blog/docling-vs-mineru-vs-marker-pdf-parser
10. Marker: https://pypi.org/project/marker-pdf/ ; Chandra OCR https://www.beri.net/learning/chandra-ocr-docs
11. Unstructured elements and chunking: https://docs.unstructured.io/open-source/core-functionality/chunking ; https://docs.unstructured.io/platform/document-elements
12. LlamaParse v2 tiers and versions: https://developers.llamaindex.ai/llamaparse/parse/guides/tiers/index.md ; https://www.llamaindex.ai/blog/introducing-llamaparse-v2-simpler-better-cheaper
13. Evidence Units: https://arxiv.org/abs/2604.00500
14. Anthropic Contextual Retrieval: https://anthropic.com/news/contextual-retrieval
15. Late chunking: https://arxiv.org/html/2409.04701v3 ; https://github.com/jina-ai/late-chunking
16. Qdrant hybrid queries: https://qdrant.tech/documentation/concepts/hybrid-queries
17. Qdrant 1.10 universal query, IDF, ColBERT: https://qdrant.tech/blog/qdrant-1.10.x/
18. RAPTOR: https://arxiv.org/abs/2401.18059
19. GraphRAG 1.0 (incremental update): https://www.microsoft.com/en-us/research/blog/moving-to-graphrag-1-0-streamlining-ergonomics-for-developers-and-users/
20. LazyGraphRAG: https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/
21. LightRAG: https://arxiv.org/html/2410.05779v3
22. HippoRAG 2: https://arxiv.org/abs/2502.14802 ; https://github.com/OSU-NLP-Group/HippoRAG
23. ColPali: https://arxiv.org/abs/2407.01449
24. ViDoRe V3: https://arxiv.org/html/2601.08620v1
25. ViDoRe V3 findings (visual vs. text, rerankers, hybrid): https://arxiv.org/html/2601.08620v1 ; leaderboard https://mteb-leaderboard.hf.space/benchmark/ViDoRe(v3)
26. MUVERA: https://arxiv.org/abs/2405.19504 ; https://research.google/blog/muvera-making-multi-vector-retrieval-as-fast-as-single-vector-search/
27. LlamaIndex ingestion pipeline and document management: https://developers.llamaindex.ai/python/framework/module_guides/loading/ingestion_pipeline/
28. LangChain indexing API: https://python.langchain.com/docs/how_to/indexing
29. Haystack DocumentWriter / DuplicatePolicy: https://docs.haystack.deepset.ai/docs/documentwriter
30. pgvector 0.8: https://www.thenile.dev/blog/pgvector-080 ; production notes https://bigdataboutique.com/blog/pgvector-in-production
31. LanceDB: https://docs.lancedb.com/features ; Lance in DuckDB https://duckdb.org/2026/05/21/test-driving-lance.html
32. ClickHouse ANN indexes: https://clickhouse.com/docs/reference/engines/table-engines/mergetree-family/annindexes
33. Anthropic citations (search_result location): https://www.rubydoc.info/github/anthropics/anthropic-sdk-ruby/main/Anthropic/Models/CitationsSearchResultLocation ; citation types https://docs.spring.io/spring-ai/docs/current/api/org/springframework/ai/anthropic/Citation.html
34. Chroma, evaluating chunking: https://research.trychroma.com/evaluating-chunking
35. RAG eval practice: https://blog.premai.io/rag-evaluation-metrics-frameworks-testing-2026 ; https://tessl.io/registry/skills/github/hamelsmu/evals-skills/evaluate-rag

**OperonX files cited** (all under `/home/thanglq/Operon` unless noted):
- Guide and plans: `operonx/guide/0{1..5}-*.md`, `OP_TAXONOMY_REFACTOR_PLAN.md`.
- Providers: `operonx/providers/{ops,embeddings,vector_stores,doc_stores,rerankers,llms}/…`.
- Core: `operonx/core/{media.py,registry/*,ops/base.py,ops/transform/func_op.py}`.
- App and the rest: `operonx/telemetry/media.py`, `operonx/checkpoint/base.py`, `operonx/app/{declare.py,evals.py,jobs/*}`,
  `operonx/agents/memory.py`, `pyproject.toml`, `CHANGELOG.md`.
- Studio: `/home/thanglq/operonx-studio/{README.md,operonx_studio/app.py}`.
