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
| D3 | **ML layout and tables (`docling-ibm-models`, MIT, torch) are an optional extra `layout`, off by default**, behind `LayoutModel`/`TableModel` ABCs. The default is our heuristic layout: font size/weight, positions, columns, reading order, ruled and aligned tables. | Core install has no torch. The heuristic is the `LayoutModel` everyone gets; the ML adapter must beat it on golden docs (K1b gate) to be recommended. |
| D4 | **Office, HTML, Markdown parse with the stdlib** (`zipfile`, `defusedxml`, `html.parser`), porting the useful rules of docling's pure-Python backends. | Core deps: `operonx`, `pydantic`, `defusedxml`. No python-docx/pptx/openpyxl/selectolax/marko. |
| D5 | **No Rust.** A native hotspot is reported with a profile, never added. | |
| D6 | **No shims for upstream gaps.** U1 (vector delete/upsert ops) and U2 (entry-point discovery of resource categories) are being built upstream on `feat/kb-upstream`. Work that needs them comes last. Until then the derived index sits behind our `Index` ABC with the in-memory implementation, and categories register at `import operonx_kb` (the existing `REGISTRY.register` mechanism, as `operonx.providers.registry` does). | K1 tests run against `MemoryDenseIndex`; the operonx-vector-store-backed index and the `operonx.resources` entry point land when upstream is ready. |
| D7 | Token counts use a deterministic regex tokenizer (`RegexTokenizer`, fingerprinted), not tiktoken: tiktoken downloads its BPE file on first use, which breaks offline CI. A model tokenizer can be plugged in later through the same `Tokenizer` protocol. | `chunker_fp` includes the tokenizer fingerprint. |
| D8 | The canonical text of a version is stored in the blob store under its own SHA-256 (`text_sha`), so `text_sha` *is* its blob key. The catalog stores spans; the blob store stores bytes. | `verify` checks `sha256(blob(text_sha)) == text_sha` and every span against it. |

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
  └► plan_ingest ─► if skip ─► skipped ───────────────────────────────────────────────┐
                    else  ─► store_raw ─► parse ─► structure ─► chunk ─► EmbedChunksOp ─► write_index ─► commit ─► collect_garbage ─► report
```

- `operonx_kb.parsing`: `Parser` ABC → `ParsedDoc` (`RawBlock`s with kind, text, page, bbox, level,
  attrs; `PageInfo`s). Parsers: plain, markdown, html, docx, pptx, xlsx, pdf. `ParserRouter` picks by
  mime/extension.
- `operonx_kb.pdf`: the PDF pipeline, docling's StandardPdfPipeline stages as functions:
  backend (docling-parse) → lines → layout (`HeuristicLayout`: furniture, columns, blocks, headings,
  lists, tables) → reading order → assemble (dehyphenation, line joining) → `RawBlock`s.
- `operonx_kb.structure`: `build_version(parsed)` → element tree + canonical text + spans. Pure.
- `operonx_kb.chunking`: `StructuralChunker` (heading-scoped packing to a token budget, sentence
  splits of oversize elements, tables as evidence units with caption and referring paragraph,
  non-contiguous spans allowed) and `RecursiveChunker` (baseline).
- `operonx_kb.stores`: `Catalog` ABC + `SqliteCatalog` (stdlib `sqlite3`, WAL, versioned SQL
  migrations, no ORM) — the store of record. Blobs: operonx `MediaStore`/`LocalMediaStore` used
  as is (it is content addressed by SHA-256, writes atomically, and is not the expiring ClickHouse
  store). Postgres catalog behind the same ABC is K1c.
- `operonx_kb.index`: `Index` ABC (upsert/delete/search/count/ids/drop per generation) and
  `MemoryDenseIndex`. Derived and rebuildable from the catalog + embedding cache.
- `operonx_kb.ops` (logic) / `operonx_kb.graphs` (wiring only): the ingest graph above;
  `EmbedChunksOp` is a `BaseOp` over `embedding:` resources with a catalog-backed embedding cache
  keyed by `(embedder_fp, embed_text_sha)`.
- `operonx_kb.kb.KnowledgeBase`: library API (`create_collection`, `add`, `delete`, `gc`, `verify`,
  `rebuild_index`). `operonx_kb.cli`: `operonx-kb add|list|status|delete|gc|verify`.
- Resource categories registered on import: `kb_catalog:` (sqlite), `kb_blob:` (local), `kb_index:`
  (memory). Ops receive resource *keys* (strings), never objects, so traces stay JSON.

Consistency (§11.2): index writes happen before the catalog flip; hydration (K2) joins hits to
active versions, so un-flipped entries are invisible and a crash leaves the catalog consistent.
Removed chunks are deleted from the index after the flip (`collect_garbage`).

## 4 · Phases and gates (measured; numbers recorded in `docs/bench/`)

| Phase | Scope | Gate |
|---|---|---|
| **K0** | PLAN, skeleton, pyproject + extras, test layout, model, normalisation, ids, hashes, fingerprints, span utilities, canonical serializer, fakes (`HashEmbedder`, `CountingEmbedder`) | Span invariant holds on 100% of K0 golden block fixtures and on hypothesis-generated trees |
| **K1a** | Blob store, SQLite catalog + migrations, parsers (plain, md, html, docx, pptx, xlsx, pdf via docling-parse), heuristic layout, structurer, structural + recursive chunkers, ingest graph, embedding cache, chunk diff, commit flip, delete/tombstone/purge, GC, `verify`, CLI basics, golden corpus | (a) span invariant on 100% of golden docs; (b) re-ingest of the unchanged corpus = 0 parse spans and 0 embed calls; (c) one-paragraph edit re-embeds only the changed chunks (≤ 3); (d) purge leaves 0 index entries and 0 orphan blobs for that document; (e) PDF ingest throughput recorded |
| **K1b** | `layout` extra: `docling-ibm-models` layout + TableFormer behind `LayoutModel`/`TableModel` | Element-kind accuracy and table-cell accuracy vs. the heuristic on the golden PDFs; recorded, default stays heuristic unless it wins |
| **K1c** (after upstream) | U1: index backed by operonx vector stores (`VectorUpsertOp`/`VectorDeleteOp`); U2: `operonx.resources` entry point; Postgres catalog; `rebuild` from catalog | Conformance suite passes on memory + FAISS (+ pgvector in docker); rebuild reproduces the id set |
| K2+ | track5 §18 P2-P7 (lexical, hybrid, citations, eval; Studio; enrichment; visual; graph) | track5 gates |

## 5 · Testing

`tests/unit` (pure functions), `tests/golden` (corpus + element-tree snapshots, `--update-golden`
to refresh), `tests/property` (hypothesis: span invariant, id determinism), `tests/graphs` (ingest
graph with `Operon`, asserting `"$errors" not in out`, counting parse spans from the run trace and
embed calls from `CountingEmbedder`), `tests/conformance` (Catalog, Index). No network. Golden PDFs
are generated reproducibly by `tests/golden/make_pdfs.py` (reportlab, `invariant=1`, DejaVu fonts)
and committed.
