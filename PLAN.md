# operonx-kb — plan

`operonx-kb` (import `operonx_kb`) turns files into versioned documents with span-level provenance,
indexes them, and (from K2) answers with citations that resolve to page + bounding box. It is an
operonx package: every stage is an op, ingest and maintenance are `@graph`s, stores are
`resources.yaml` keys. Design source: `Operon/docs/roadmap/track5_knowledge.md` (§ numbers below
refer to it) and `ROADMAP.md` §5. This file records where we differ and what each phase must prove.

## 1 · Decisions that override track5

| # | Decision | Consequence |
|---|---|---|
| D1 | **No `docling` dependency.** We re-implement its conversion pipeline as our own operonx ops over our own model (§5). Ideas taken from docling, with the file they come from, are cited in the code. | No `parsers/docling.py`; `DoclingDocument` and docling-core types never leave the PDF backend adapter. |
| D2 | **PDF text + coordinates come from `docling-parse`** (MIT, C++), behind our `PdfBackend` adapter, extra `pdf`. | One adapter module imports `docling_parse`/`docling_core`; everything after it sees `PdfPage`/`PdfLine` (ours). |
| D3 | **ML layout and tables are an optional extra `layout`, off by default**, behind `LayoutDetector`/`TableStructurer` (in `ModelLayout`). Since `docling-ibm-models` 4 ships only TableFormer, the layout detector is docling's Heron (RT-DETRv2, Apache-2.0) run with `transformers`, as docling itself does; CPU torch from the PyTorch CPU index. The default is our heuristic layout: font size/weight, positions, geometric blocks, docling's rule-based reading order, ruled and aligned tables. | Core install has no torch. The heuristic is the `LayoutModel` everyone gets. K1d (`docs/bench/k1d_layout.md`): text recall 0.69 on the hand-checked reference (12 real pages) and 0.72 against docling's outputs (97 pages), from 0.44 / 0.51, at 0.13–0.15 s/page; `ModelLayout` reaches 0.90 / 0.94 at 2.6–3.6 s/page and is the only one with usable table cells (0.83 vs 0.13). Recommended: the heuristic for born-digital prose, reports, manuals, slides and bulk ingestion; `ModelLayout` for papers, forms and table-heavy PDFs. |
| D4 | **Office, HTML, Markdown parse with the stdlib** (`zipfile`, `defusedxml`, `html.parser`), porting the useful rules of docling's pure-Python backends. | Core deps: `operonx`, `pydantic`, `defusedxml`. No python-docx/pptx/openpyxl/selectolax/marko. |
| D5 | **No Rust.** A native hotspot is reported with a profile, never added. | |
| D6 | **No shims for upstream gaps.** U1 (`BaseVectorStore.delete`, `VectorUpsertOp`/`VectorDeleteOp`), U2 (`operonx.resources` entry points; an unknown category raises) and U6 (`operonx.core.media_store`) were built upstream on `feat/kb-upstream` (operonx PR #74) and the KB uses them directly. | The dense index **is** an operonx `vector_store:` resource written by `VectorUpsertOp`/`VectorDeleteOp` inside the ingest, delete and GC graphs; there is no KB index abstraction. Until PR #74 merges, run with `PYTHONPATH=<feat-kb-upstream>`. |
| D7 | Token counts use a deterministic regex tokenizer (`RegexTokenizer`, fingerprinted), not tiktoken: tiktoken downloads its BPE file on first use, which breaks offline CI. A model tokenizer can be plugged in later through the same `Tokenizer` protocol. | `chunker_fp` includes the tokenizer fingerprint. |
| D8 | The canonical text of a version is stored in the blob store under its own SHA-256 (`text_sha`), so `text_sha` *is* its blob key. The catalog stores spans; the blob store stores bytes. | `verify` checks `sha256(blob(text_sha)) == text_sha` and every span against it. |
| D9 | **Vector keys and the index ledger.** A chunk's vector key is the first 63 bits of its id (`vector_id`), an int64 every operonx backend accepts. Because a vector store cannot list what it holds, the catalog records every key the KB writes (`kb_index_entries`, the LangChain record-manager idea): a row is added before the upsert and removed after the delete, so the ledger always covers the index. GC deletes ledger entries no active version holds; `verify` compares ledger and active chunks; a 63-bit key collision raises instead of overwriting. | Works on FAISS, which stores no metadata and deletes by id only. |

Everything else in track5 §3 (principles), §5-§6 (model, ids, fingerprints), §11 (incremental
indexing) and §17 "Avoid" stands.

## 2 · The model (track5 §5, K0/K1 subset)

- `Document` (stable id `doc_` = H(collection, key)), `DocumentVersion` (`ver_` = H(document, raw_sha,
  pipeline_fp); status staged/committed/failed/superseded), `Page`, `Region` (page, normalised
  top-left bbox in [0,1]), `Element` (`el_` = H(version, path); `content_sha` = H(kind, text, attrs)),
  `Chunk` (`ch_` = H(document, chunker_fp, content_sha, occurrence) — stable across versions),
  `VersionChunk` (per-version spans, element ids, pages).
- **Canonical text**: one Markdown-flavoured serialisation of the body tree (headings `#`, list items
  `- `/`1. `, GFM tables, fenced code), blocks separated by a blank line, NFC and whitespace
  normalised. An element's span covers its *content* (not its markup), a container's span runs from
  its first to its last descendant. Furniture (page headers/footers) is kept in the tree with its
  text and regions but no span (`span=None`) and no text in canonical.
- **The invariant**: `canonical[e.span[0]:e.span[1]] == e.text` for every body element, chunk spans lie in
  canonical and `chunk.content_sha == H(span texts)`. Checked when a version is built and again on
  commit; a violation raises `SpanInvariantError` (fail loud, principle 7).
- Hashes are SHA-256 hex over NFC + whitespace-collapsed text (§6.1); ids are a typed prefix + 32 hex.
- Fingerprints: `fingerprint(component, version, config)`; `pipeline_fp` = H(parser, layout,
  structurer, serializer, chunker); a changed `pipeline_fp` means a new version on next ingest.

## 3 · Architecture (K1)

```
item {key, path | data, mime?, metadata?}
  └► plan_ingest ─► if skip ─► skipped ─────────────────────────────────────────────────────────────┐
                    else  ─► parse ─► build_tree ─► chunk_version ─► EmbedChunksOp ─► stage_index_writes
                          ─► (if any) VectorUpsertOp ─► commit_version ─► VectorDeleteOp(removed) ─► forget ─► report
```

- `operonx_kb.parsing`: `Parser` ABC → `ParsedDoc` (`RawBlock`s with kind, text, page, bbox, level,
  attrs; `PageInfo`s). Parsers: plain, markdown, html, docx, pptx, xlsx, pdf. `ParserRouter` picks by
  mime/extension.
- `operonx_kb.pdf`: the PDF pipeline, docling's StandardPdfPipeline stages as functions:
  backend (docling-parse) → lines → layout (`HeuristicLayout`: furniture, tables, geometric blocks,
  headings, lists) → reading order (docling's rule-based, `reading_order.py`) → assemble (dehyphenation, line joining) → `RawBlock`s.
- `operonx_kb.structure`: `build_version(parsed)` → element tree + canonical text + spans. Pure.
- `operonx_kb.chunking`: `StructuralChunker` (heading-scoped packing to a token budget, sentence
  splits of oversize elements, tables as evidence units with caption and referring paragraph,
  non-contiguous spans allowed) and `RecursiveChunker` (baseline).
- `operonx_kb.stores`: `Catalog` ABC + `SqliteCatalog` (stdlib `sqlite3`, WAL, versioned SQL
  migrations, no ORM) — the store of record. Blobs: operonx `MediaStore`/`LocalMediaStore` used
  as is (it is content addressed by SHA-256, writes atomically, and is not the expiring ClickHouse
  store). `PostgresCatalog` (extra `postgres`) runs the same SQL (`SqlCatalog`).
- Dense index: an operonx `vector_store:` (FAISS, pgvector, Qdrant) named in `DenseIndexSpec.store`,
  written by `VectorUpsertOp`/`VectorDeleteOp`, plus the catalog's index ledger (D9). Derived and
  rebuildable from the catalog + embedding cache.
- `operonx_kb.ops` (logic) / `operonx_kb.graphs` (wiring only): the ingest graph above;
  `EmbedChunksOp` is a `BaseOp` over `embedding:` resources with a catalog-backed embedding cache
  keyed by `(embedder_fp, embed_text_sha)`.
- `operonx_kb.kb.KnowledgeBase`: library API (`create_collection`, `add`, `delete`, `gc`, `verify`),
  each running its graph. `operonx_kb.cli`: `operonx-kb collections|create|add|list|status|delete|gc|verify`.
- Resource categories `kb_catalog:` (sqlite) and `kb_blob:` (local) reach operonx through
  `operonx.resources` entry points (and register on import). Ops receive resource *keys* (strings),
  never objects, so traces stay JSON.

Consistency (§11.2): index writes happen before the catalog flip; hydration (K2) joins hits to
active versions, so un-flipped entries are invisible and a crash leaves the catalog consistent.
Removed chunks are deleted from the index after the flip (`VectorDeleteOp`), then leave the ledger.

Known limitations (K1): XLSX cells are stored values (dates are Excel serials, number formats are not
applied); PDFs without a text layer produce no text (OCR is out of scope); the heuristic layout does
not detect figures without an image resource (vector plots), nor tables with neither rules nor ≥3
aligned rows, and cuts multi-line rows of ruled tables into one cell; right-to-left text is not
reordered (see `docs/bench/k1d_layout.md` §4).

## 4 · Phases and gates (measured; numbers recorded in `docs/bench/`)

| Phase | Scope | Gate |
|---|---|---|
| **K0** ✔ | PLAN, skeleton, pyproject + extras, test layout, model, normalisation, ids, hashes, fingerprints, span utilities, canonical serializer, fakes (`HashEmbedder`, `CountingEmbedder`) | Span invariant holds on 100% of K0 golden block fixtures and on hypothesis-generated trees |
| **K1a** ✔ | Blob store, SQLite catalog, dense index on operonx vector stores (U1) with the ledger, entry points (U2) + migrations, parsers (plain, md, html, docx, pptx, xlsx, pdf via docling-parse), heuristic layout, structurer, structural + recursive chunkers, ingest graph, embedding cache, chunk diff, commit flip, delete/tombstone/purge, GC, `verify`, CLI basics, golden corpus | (a) span invariant on 100% of golden docs; (b) re-ingest of the unchanged corpus = 0 parse spans and 0 embed calls; (c) one-paragraph edit re-embeds only the changed chunks (≤ 3); (d) purge leaves 0 index entries and 0 orphan blobs for that document; (e) PDF ingest throughput recorded |
| **K1b** ✔ | `layout` extra: docling's Heron layout detector (transformers) + TableFormer (`docling-ibm-models`) behind `LayoutDetector`/`TableStructurer`, as `ModelLayout` | Recorded in `docs/bench/k1b_layout.md`: equal to the heuristic on the golden PDFs; far ahead on docling's 97 real test pages (agreement with docling's reference output), at ~30x the CPU time. Default stays the heuristic (D3, no torch in core); `ModelLayout` is the recommended setting for real-world PDFs. |
| **K1c** ✔ | Postgres catalog behind `Catalog` (one SQL implementation for both dialects); `rebuild` into a new index generation from catalog + embedding cache, with switch and drop of the old one; conformance on FAISS, pgvector and Qdrant; generated 200-document corpus and 100-page PDF | Met (`docs/bench/k1c.md`): rebuild reproduces the id set and the top-10 of 50 queries with 0 parses and 0 embed calls; unchanged 200-doc re-ingest = 0 parses, 0 embeds, 0 upserts; one-paragraph edit in a 106-page PDF ≤ 3 re-embeds; purge leaves 0 vectors/ledger rows/blobs; conformance passes on all three backends and both catalogs |
| **K1d** ✔ | Honest layout measurement and a better heuristic: hand-checked reference of 12 real pages (`tests/layout_reference`), scorer fixes (figures by geometry, per-page scoring), cause diagnosis (`scripts/diagnose_layout.py`); rotated text, TeX/URW/Libertine font styles, frames and figure grids, geometric blocks with docling's rule-based reading order, leading-relative paragraph gaps, furniture and label rules, each with a regression test from a real page crop | Recorded in `docs/bench/k1d_layout.md`: golden PDFs exact (now a test); span invariant 100%; heuristic text recall 0.44 → 0.69 (hand) and 0.51 → 0.72 (docling), kind accuracy 0.34 → 0.58 and 0.42 → 0.63; 0.13–0.15 s/page, no ML, no Rust |
| **K2** ✔ | Lexical index (SQLite FTS5, Postgres FTS, Vietnamese-aware analyzer), `KBFilter` compiled per backend, dense/lexical/hybrid retrievers, hydration gate, `RerankOp`, context builder, cite-by-span answers with verified citations, eval datasets and metrics, CLI `query`/`eval` (§6) | Recorded in `docs/bench/k2.md`: hybrid beats dense on Recall@10 on 3 of 3 sets (significant on `xquad_vi` +0.020 and `corpus_vi` +0.034, noise on `xquad_en` +0.003), so **hybrid is the default**; rerank (multilingual MiniLM) does not pay and stays opt-in; Vietnamese wants `vi`+folding (unaccented questions 0.64 → 1.00 R@10 at no cost to accented ones); Postgres `ts_rank_cd` trails FTS5 `bm25` by 0.09-0.39 R@10. Tenant-leak conformance passes on FAISS, pgvector, Qdrant, SQLite FTS5 and Postgres FTS. **Open:** live citation precision 0.83 on 30 answers (gate 0.9) and the human check of those 30 |
| **D5** ✔ | The first real corpus: Vietnamese public documents with checked licenses (MLQA vi / Wikipedia CC BY-SA 3.0, 511 human-written QA cases; 63 official legal PDFs, not copyright-protected), pinned download script | Recorded in `docs/bench/d5.md`: hybrid beats dense by +0.069 Recall@10 (McNemar p < 0.001) and stays the default; rerank +0.055 MRR (opt-in, ~5 s/query on CPU); live citation precision 29/30. **Open:** OCR (68% of crawled legal PDFs are scans), the human check of the 30 answers, no licensed legal QA set yet |
| **K3** | track5 §18 P3: the admin API `operonx-kb/1` (§8) and Studio's Knowledge tab (operonx-studio): collections, documents, a viewer with bbox overlays, the chunk inspector, a query playground with clickable citations, trace deep links, save as eval case | Recorded in `docs/bench/k3.md`: screenshots of every view at desktop and phone width (light and dark); a cited answer opens the correct page and box on 20/20 sampled citations (checked in the browser and against pdfium's own text layer) |
| **K4** (built; gate open) | track5 §18 P4 (§9 below): contextual chunk enrichment at section scope, section and document summaries, the tree index (headings, or an LLM table of contents for heading-less documents) and beam tree search; every LLM step an `LLMOp` cached by content | Incrementality met (tests: re-ingest = 0 `LLMOp` spans and 0 model calls; delete and re-add answered by the cache; an edit re-contextualizes one window, re-summarizes one section). Recorded in `docs/bench/k4.md`: on `xquad_en` contextual gives no significant lift in any mode (hybrid ΔMRR +0.001, p = 0.93); cost $0.58/1k pages contextual, $0.16/1k pages tree, $0.00013 per tree query. **Open:** the run stopped at $1.18 when the OpenAI account ran out of credits; `vi_public`, `xquad_vi`, `corpus_vi` and the 100-case tree comparisons resume from the cache. Nothing is default-on. |
| **K6** | track5 §18 P6 (§10 below): the concept graph (no model), mentions committed with the version, personalized PageRank graph search seeded by the default retriever, filter-closed walk | Recorded in `docs/bench/k6.md` (see §10) |
| K5, K7 | track5 §18 P5 (visual pages: needs a GPU), P7 (hardening) | track5 gates |

## 6 · K2: retrieval, citations, eval (track5 §9, §10, §12.3, §15.3)

### Decisions

| # | Decision | Consequence |
|---|---|---|
| R1 | **The lexical index is a `kb_lexical:` resource** (`LexicalIndex`, shaped like operonx's `BaseVectorStore`: upsert, delete, search by int64 key with a payload). Backends: SQLite FTS5 (stdlib, `bm25()`) and Postgres FTS (extra `postgres`, a `tsvector` + GIN, `ts_rank_cd`). Text reaches the backend **pre-analyzed** by the KB's `Analyzer`, and both backends are configured to keep tokens as given, so they rank the same tokens. | Same key (`vector_id`) and the same ledger (`kb_index_entries`) as the dense index: ingest, delete, GC, rebuild and `verify` treat both indexes alike. Operonx has no lexical store; if it grows one, this contract is what goes upstream. |
| R2 | **Analyzer** (`simple` or `vi`, `fold_diacritics`): NFC, casefold, words; `vi` adds syllable bigrams (Vietnamese words are mostly two space-separated syllables), `fold_diacritics` strips tone and vowel marks and maps `đ`→`d`. Fingerprinted; a change is a new lexical generation. No pyvi/underthesea dependency. | Folding is not assumed: measured on accented and unaccented queries (`docs/bench/k2.md`), and the default follows the numbers. |
| R3 | **Index payload.** Every index entry carries the KB's filter fields, built by one function from the catalog's document: `kb_collection`, `kb_document`, `kb_tags`, `kb_acl`, `kb_mime`, `kb_created` (epoch seconds), and `kb_f_<name>` for each field the collection declares `filterable`. FAISS ignores payloads. | A pgvector table needs these columns (`metadata_columns:`); a missing one raises at upsert (operonx's check). |
| R4 | **`KBFilter` is closed** (`document_ids`, `tags_any`, `tags_all`, `acl_any`, `mime_in`, `created_after`, `created_before`, `fields`), always AND-ed with the collection, and compiled per backend: FAISS → post-filter against the catalog, pgvector → SQL (`{"where", "params"}`), Qdrant → a condition tree, FTS → SQL. An undeclared field, or a backend with no compiler, raises: a filter never degrades to no filter. **The hydration gate re-applies the filter against the catalog** (the store of record), so a stale payload (a reused chunk keeps the payload of the version that wrote it) can cost recall but never leak. | Conformance suite with a tenant-leak test per backend (`tests/conformance/test_filters.py`). |
| R5 | **Retriever contract** `(query, collection, filter, k) → hits` (chunk ids and scores, no text). Factories, each with a few parameters: `dense_retriever(dense)` (EmbeddingOp → VectorSearchOp), `lexical_retriever(lexical)`, `hybrid_retriever(a, b)` (RRF, k=60, over any two retrievers). `search_graph(retriever)` adds the **hydration gate** (active version of a live document of the collection, filter re-checked; dropped counts reported); `reranked(search, reranker)` wraps a search with `RerankOp`; `answer_graph(search, llm)` adds the context builder, the LLM and citation verification. No god graph. | Over-fetch: `ceil(1.5 k)` under a native filter, `post_filter_overfetch × k` on FAISS; the shortfall is reported. |
| R6 | **Cite by span.** `LLMOp(fields=["answer: str", "citations: list"], parser="json")`: markers `[n]` name sources; every citation is `{source, quote}` with a verbatim quote. A quote is verified only if it is found in the cited source's canonical text (NFC, whitespace-insensitive; tolerated: the kind of quotation mark and the case of the first letter, recorded on the citation as `tolerated`, evidence in `docs/bench/d5.md`), then resolved to its canonical span → elements → page + bbox. Unverifiable citations are dropped and reported (`dropped`), their markers removed, and sentences left without a verified marker listed in `unsupported_sentences`. | The answer a user sees cites only text that exists at the cited address. |
| R7 | **Context** = hits in rank order, each expanded by `neighbours` chunks of the same version and section, merged where they touch, packed to a token budget, numbered `[1..n]`. A source's text is exactly the canonical slices it covers. | Quotes are checked against the canonical text, never against a prompt rendering. |
| R8 | **Eval on operonx's `Eval` (evals E1–E6, merged).** The evaluators return `{passed, score, reason}`; `Eval` gives repeats, the fingerprint, pass shares with intervals and the `Gate`. A KB check is graded (MRR, nDCG, a citation share), so `score_metrics` averages each case's scores over its repeats and estimates the mean with operonx's `stats.estimate`; `compare_metric` compares two modes with `stats.compare_paired` (McNemar/Newcombe for 0/1, paired bootstrap otherwise). Metrics: Recall@k, MRR, nDCG@10 (labels are quotes resolved to spans in the active version at eval time; a hit covers a quote when its spans hold at least half of the quote's characters), citation precision (verified / all citations) and a faithfulness proxy (share of answer sentences whose words are found in their verified quotes). | No KB-local statistics: intervals and tests are operonx's. |
| R9 | **Eval sets.** (1) `xquad_vi` and (2) `xquad_en`: XQuAD (google-deepmind/xquad, CC BY-SA 4.0), downloaded at a pinned commit and checked by SHA-256; one HTML document per article, every third question (397 per language), label = the sentence holding the answer's start, expected answer = the answer text. (3) `corpus_vi`: ≥ 100 Vietnamese cases generated with the 200-document corpus; each Vietnamese document carries fact sentences drawn from its own seeded generator, and each case's quote is its fact sentence (derived mechanically, never hand-written). Any corpus plugs in as a folder of files plus a dataset JSONL of `{query, relevant: [{doc_key, quote}], answer}`: the real internal corpus (D5, open) is one more such pair. | The Vietnamese text of the original corpus is 6 distinct sentences repeated 1475 times over 40 documents, so no case could point at one document; the fact sentences fix that without changing the 160 other documents. |
| R10 | **Embeddings for the gate**: `intfloat/multilingual-e5-small` (MIT, 118M parameters, multilingual incl. Vietnamese, trained for query–passage retrieval) through operonx's `HFEmbedding`, whose mean pooling is the model's own; its `query: `/`passage: ` prefixes come from `DenseIndexSpec.query_template`/`passage_template` (part of the embedding fingerprint). Rerank: a cross-encoder through operonx's `HFReranker`. The hash fake is used in tests only. | Recorded with the numbers in `docs/bench/k2.md`. |

## 7 · Testing

`tests/unit` (pure functions), `tests/golden` (corpus + element-tree snapshots, `--update-golden`
to refresh), `tests/property` (hypothesis: span invariant, id determinism), `tests/graphs` (ingest
graph with `Operon`, asserting `"$errors" not in out`, counting parse spans from the run trace and
embed calls from `CountingEmbedder`; retrieval, answer and eval graphs with the `OverlapReranker` and
`ScriptedLLM` fakes reached through `ResourceHub.alias`), `tests/admin` (the admin API through
Starlette's test client, each answer checked against the catalog, and mounted the way `operonx
serve` mounts it), `tests/conformance` (Catalog, vector
backends, `LexicalIndex`, and the `KBFilter` tenant-leak suite on every backend). No network. Golden PDFs
are generated reproducibly by `tests/golden/make_pdfs.py` (reportlab, `invariant=1`, DejaVu fonts)
and committed.

## 8 · K3: the admin API and the Knowledge tab (track5 §16.2)

| # | Decision | Consequence |
|---|---|---|
| S1 | **Studio finds a KB by asking and speaks HTTP only.** A served `asgi` service whose `GET <path>/.well-known/operonx-kb` answers `{"api": "operonx-kb/1", …}` is a knowledge base; Studio proxies its Knowledge tab's requests to it (`/api/p/{pid}/kb/{service}/{path}`), only to a declared service that answered, under Studio's access table: GET is a viewer's, POST an editor's (a query runs the answer model). | `operonx_kb.admin` (Starlette, extra `admin`) is the whole contract, its routes listed in `admin/asgi.py`; a breaking change is `operonx-kb/2`, and Studio lists a service speaking another version with the reason it cannot read it. Studio never imports `operonx_kb`. |
| S2 | **Page images are rendered on request** from the version's own raw bytes with pypdfium2 (the renderer the ML layout uses), PNG, kept in a small in-process cache keyed by `(raw_sha, page, scale)`; `Page.image_sha` stays empty. | No ingest cost or blob space for pages nobody opens; the image is the page the boxes were measured on. Only PDFs have page images; any other source is shown as its canonical text in chunk bands, and a citation into it lights its span. |
| S3 | **Side-by-side lists are separate runs.** A playground query runs one search per asked mode (dense, lexical, hybrid, and the default mode reranked), and the answer as its own run, concurrently, each under its own trace id (`KnowledgeBase.search/ask(trace_id=)`). | No graph grows an output for the UI's sake; each list is exactly what that mode returns, and each opens in Studio's Runs (the app traces with `trace="project"`). A failed run's error carries its trace id. |
| S4 | **An eval case is the KB's row, written by Studio.** `POST /collections/{c}/eval-case` builds the dataset row (`operonx_kb.eval.cases.eval_case`) from the question and the verified citations kept (labels are their canonical quotes, resolved before the row is returned; id = H(collection, normalised question)); Studio saves it with its dataset rows API (operonx `Dataset.add`, deduped by id). The answer becomes `expected.answer` only when the reviewer ticks it. | A saved case is never stale at birth, and the same question is one case. |
| S5 | Not built in K3 (track5 §16.2 lists them; no K3 gate needs them): upload, delete and reindex routes (writes go through the CLI and Jobs), the version diff, the `kb.yaml` spec editor, eval scores on the collection card, "query neighbours" in the chunk inspector, index presence of other generations. | The tab is read-mostly plus query. Each is one route and one panel on the same contract when it is needed. |

## 9 · K4: enrichment and the tree index (track5 §7.5, §9.5, §11.4, §18 P4)

### Decisions

| # | Decision | Consequence |
|---|---|---|
| E1 | **Every LLM step of ingest is an `LLMOp` behind a content cache.** One stage shape (`graphs/enrich.py`, `llm_stage`): `lookup` reads `kb_enrichment_cache` (`(enricher_fp, input_sha) → value`, plus the call's tokens and cost), a generator yields only the misses, `LLMOp` answers them (`.parallel(max=N)`), `store` writes them back. A stage with no miss makes no model call and no `LLMOp` span (the generator and the model op do not run: `if_(misses > 0, …)`). `enricher_fp = H(kind, prompt version, settings, model)`; the model is the `llm:` resource's config without credentials (as `embedder_fingerprint` does). Requests are ready `messages` (`LLMOp(messages=)`), so document text never meets template formatting. | Re-ingest of unchanged content, a delete and re-add, a new collection over the same files and a pipeline change that leaves a section alone cost 0 model calls. The cache is not the operonx op cache (track5 §11.3). |
| E2 | **`contextual` is section-scoped with a bounded window** (track5 §11.4). A chunk's input: the document title, its heading path, the **window** of its section that holds it (the section's chunks packed in order up to `window_tokens`, default 1500) and the chunk. The model writes 1-2 sentences situating the chunk (Anthropic's recipe); the context is prepended to the chunk's `embed_text`, so it feeds both dense and lexical (track5 §2) and never the chunk's text, spans or citations (principle 2). The window comes first and the chunk last, so chunks of one window share a prompt prefix (OpenAI's automatic prompt cache, half price on cached input over 1024 tokens; recorded as `cached_tokens`). | A document summary is **not** part of the input, unlike §11.4: it would make every context depend on the whole document, which is what section scope exists to avoid. A long heading-less document is cut into windows instead of being sent whole per chunk. OpenAI Batch (`LLMOp(batch_mode=True)`) is not used: an `add()` would wait hours for its batch. |
| E3 | **A contextual chunk's id includes its context input**: `ch_ = H(document, chunker_fp, content_sha, occurrence, H(contextual_fp, input_sha))`. | The chunk diff stays an id diff: a chunk whose window changed is a new chunk (contextualized, embedded, indexed) and the old one is removed and collected; a chunk whose window did not change keeps its id, context, vector and index entries. A one-paragraph edit re-contextualizes the chunks of one window. Collections without `contextual` keep their ids. |
| E4 | **Enrichment failures fail the ingest** (principle 7 says fail-soft). | A version is never committed half enriched, which would mix two pipelines' embed texts in one version with nothing in its fingerprint to say so. What succeeded is cached, so the retry pays only for what failed; transport retries are the `llm:` resource's `max_retries`. |
| E5 | **The tree index is part of the version** (`kb_tree_nodes`, committed with it). Nodes: the document root, then the element tree's sections (headings); a document with no heading and at least `toc_min_tokens` (600) gets **synthesized sections**: the model reads its blocks, numbered, and returns `[{title, first_block, level}]` (the PageIndex approach on our elements, in windows of `toc_window_tokens`). A node holds `{id, parent, level, title, span, pages, summary, source}`; its span is canonical text, so its chunks are the version's chunks inside it. | The tree is versioned like everything else and needs no ledger: nothing derived outside the catalog. |
| E6 | **Summaries are one parallel pass** (track5 §9.5 has them bottom-up). A node's summary input is its text when the text fits `summary_input_tokens` (2000), else its opening text and the outline of its children's titles. The root's summary is the document summary. | No node waits for another; an edit re-summarizes the nodes whose own input changed, where bottom-up re-summarizes every ancestor. |
| E7 | **Tree search (`mode="tree"`) is a bounded beam loop seeded by the collection's default retriever.** Documents: the first `docs` (5) distinct documents among the seed's top `seed_depth` (30) hits. Each iteration shows the children of the frontier (document title, heading path, summary) to the navigator (`LLMOp`, JSON `{choose: [ids], enough: bool}`); chosen leaves are picked, chosen inner nodes become the frontier; it stops on `enough`, an empty frontier, `max_depth` (4) or the loop cap. Hits: the chunks inside the picked nodes (pick order; inside a node by seed rank, then document order), then the seed's other hits up to `k`. | Documents are selected by their passages, not by a separate doc-summary dense index (§9.5): one less derived index to keep consistent with deletes, GC and `verify`. The navigator's calls, tokens and picks are in the trace (`LLMOp` spans). |
| E8 | **The pipeline fingerprint includes the enabled enrichers.** | Turning `contextual` or `tree` on makes the next ingest of each document a new version (contexts change chunk ids; the tree is built); the fingerprint of a collection without them is unchanged, so existing versions stay valid. |
| E9 | **RAPTOR is not built in K4.** | It is optional in P4; a clustered summary tree only has a case if the structural tree with summaries lifts retrieval, which the gate below decides first. |
| E10 | An upstream gap found while building the tree loop: a loop entered from a branch arm never ran and reported nothing (the cycle rewrite left the branch routing to the moved op). Fixed in operonx (PR, with a test), not worked around. | The tree graph enters its loop from `if_(any candidate, …)`. |

### Comparisons

Each technique against the K2/D5 default (`hybrid`; `vi`+fold analyzer on Vietnamese, `simple` on
English; `multilingual-e5-small`), with the same documents, chunker and embedder, through operonx
`Eval` and the KB's evaluators (PLAN R8), model `gpt-4o-mini` (the cheapest capable model in
Operon's `resources.yaml`, the D5 answer model):

1. **Contextual**: a second collection of each set ingested with `contextual`; dense, lexical and
   hybrid on all cases of `vi_public` (511), `xquad_vi` (397), `xquad_en` (397), `corpus_vi` (120).
   No model call at query time, so no subsample.
2. **Tree**: the baseline collection with `tree`; `tree` against `hybrid` on 100 cases of each set
   drawn with a fixed seed (query-time model calls), paired on the same cases.
3. **Cost**: tokens and USD of every ingest stage from the cache rows (gpt-4o-mini list prices:
   $0.15/M input, $0.075/M cached input, $0.60/M output), per 1k pages (the legal PDFs of
   `vi_public`, 970 pages) and per 1M corpus tokens; per query for `tree` (calls, tokens, USD,
   p50/p95 latency). Spend is capped at $8 and reported.

### Gate

- Recall@10 (McNemar, exact, on single-label sets) and MRR (paired bootstrap) of each technique
  against hybrid, with 95% intervals, per set (`stats.compare_paired`).
- **A technique becomes default-on for a collection type only with a significant lift** (p < 0.05
  on Recall@10 or MRR, and no significant loss on the other) on that type's sets; types:
  short heading-less articles (`xquad_vi`, `xquad_en`), templated business documents
  (`corpus_vi`), a mixed real corpus (`vi_public`: Wikipedia paragraphs and legal PDFs).
  Otherwise it stays opt-in and the table is published (`docs/bench/k4.md`).
- Incrementality, as tests on the real graphs with `ScriptedLLM` and trace span counts:
  re-ingest of unchanged documents = 0 `LLMOp` spans and 0 model calls; delete and re-add = 0
  model calls (cache); a one-paragraph edit in a 60-section document re-contextualizes only that
  window's chunks and re-summarizes only that section; span invariant, `verify` and citations
  unchanged.

### Status

Built and tested; requires operonx main at or after #89 (#87 a loop entered from a branch arm,
#88 an op joining a stream with an op outside it, #89 a subgraph whose branch went around its
stream — each a silent failure the K4 graphs hit). Gate numbers and the stop are in
`docs/bench/k4.md`: contextual and tree stay opt-in until the resumed run measures a lift.

## 10 · K6: the concept graph and graph search (track5 §9.6, §18 P6)

K6 comes before K5 (visual pages): K5's ColQwen-family page embedder needs a GPU this
project's machine does not have, while the graph builds and queries on CPU with no model.

### Decisions

| # | Decision | Consequence |
|---|---|---|
| G1 | **Concepts without a model** (track5's `entities_lazy`, LazyGraphRAG's idea): a chunk's concepts are the names in its text — runs of capitalised words with the lowercase connectors names carry (`Duke of York`, `van`, `da`), cut at punctuation and at `and` — plus its document title and headings, folded (casefold, no diacritics). A heading weighs `title_weight` (3) against 1 per mention. Content n-grams were measured on the dev split and added nothing, so they are not extracted. | Ingest stays free and offline; a re-ingest of unchanged content extracts nothing (the version is skipped). Vietnamese capitalises proper names the same way. The opt-in LLM entity graph (LightRAG-style) waits for a measured reason and API credits. |
| G2 | **Mentions are part of the version** (`kb_graph_mentions`, committed with it, purged with it), like the tree index (E5). The graph of a collection is the mentions of its active versions. | No ledger, nothing derived outside the catalog; a tombstone hides a document's concepts at once; `verify`'s guarantees are unchanged. |
| G3 | **The graph is built in memory per collection generation** (the hash of the active version ids), from one catalog query, and cached (16 collections, LRU). A concept in more than `max_df_share` (0.5%) of the chunks, and more than `max_df_min` (10), links nothing in particular and is left out. numpy edge arrays and `bincount`, no scipy. | A commit, delete or purge is seen by the next search; a collection that does not change is read once. The rebuild is whole (incremental rebuilds wait for a collection where it costs). |
| G4 | **Graph search (`mode="graph"`) is seeded by the collection's default retriever**: its first `seeds` (3) hits, weighted 1/rank, are the restart distribution of a personalized PageRank walk (`alpha` 0.3, 30 steps) over the chunk-concept graph (HippoRAG 2). Chunks come by the mass the walk leaves on them, then the seed's other hits. No model call; the query's own words are not concepts (adding them did not help on dev). | One more mode beside `tree`, composable like every retriever (track5 §9.1). The seeds keep most of their own mass, so they stay first and what they link to follows: the gain is at Recall@5/10, not @2. |
| G5 | **A filter closes the walk**: under a `KBFilter`, only the chunks of documents the filter allows take part — mass that would enter any other chunk restarts — and hydration checks again. | A hidden document neither shows up nor carries the walk to what only it links to (tested). |
| G6 | The graph's query-time settings (`seeds`, `alpha`, `max_df_*`, `iterations`) are not in `pipeline_fp`; `title_weight` and the extractor version are. | Tuning the walk needs no re-ingest. |

### Gate

- Multi-hop: MuSiQue-Ans dev and 2WikiMultihopQA validation (`scripts/prepare_multihop.py`; licenses
  CC BY 4.0 and Apache-2.0), 400 questions each with 2+ supporting paragraphs, their paragraphs
  pooled per set (HippoRAG's setup). Settings chosen on the `dev` split (100 questions); the gate
  reads `test` (300): `graph` against `hybrid`, Recall@2/5/10 (the share of a question's
  supporting paragraphs) and MRR, paired bootstrap (`stats.compare_paired`).
- No harm: the K2 single-hop sets (`xquad_en`, `xquad_vi`, `corpus_vi`), where nothing links one
  answer to another.
- **Graph becomes the default only with a significant lift on multi-hop and no significant loss on
  single-hop**; otherwise it stays opt-in (`CollectionSpec(graph=GraphSpec())`, `mode="graph"`)
  and the table is published (`docs/bench/k6.md`). Also recorded: graph size, cold build time,
  query latency, ingest cost of the concept step.
- Tests: extraction cases; the walk (hops, the df cut, the filter mask, seeds outside the graph);
  the real ingest and search graphs (two hops found; an edit, a re-add and a delete change the
  graph at once; an unchanged re-add skips; a purge leaves no mention; a filter hides a bridge);
  catalog conformance on SQLite and Postgres.
