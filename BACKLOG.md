# Backlog — outstanding work

Items deliberately deferred. Each one is **not done**: pick it up when its precondition is met.

## ⚠️ K5 · Visual page retrieval — OUTSTANDING (needs a GPU machine)

**Status:** not started. Deferred 2026-10-05 because the dev machine has no GPU; to be done on a
GPU PC. K6 (graph) was done first instead. Design: `../Operon/docs/roadmap/track5_knowledge.md`
§9.7 and §18 P5; phase row in `PLAN.md` §4.

**Scope (track5 P5):**
- `render_pages` (PNG at 144 dpi) at ingest, stored as blobs (`Page.image_sha` already exists).
- `PageEmbedder` (ColQwen family, e.g. `vidore/colqwen2.5-v0.2`) → multivector page embeddings in a
  `MultiVectorIndex` (Qdrant multivector MaxSim first; MUVERA FDE single-vector prefetch for pgvector).
- A page hit becomes `Hit(kind="page")`; the catalog maps the page to its elements, so reranking,
  context and citations get the page text and bbox.
- `mode="page"`, fused with hybrid by RRF; optional page images attached to the answer call for
  figure-heavy questions; VLM parser fallback and figure captions.
- Registry category `page_embedding:`; an extra `visual` (torch, transformers) — never core.

**Gate (track5):** ViDoRe v3 public subset plus a scanned/slide set (the 68% scanned legal PDFs of
`vi_public`, D5): hybrid text+page vs text-only, paired (`stats.compare_paired`); storage bytes
per page and ingest s/page recorded. Default-on only with a significant lift and no single-hop loss
(the K4/K6 rule). Results in `docs/bench/k5.md`.

**Before starting:** check `nvidia-smi`; follow `PLAN.md` and AGENTS.md; write PLAN §11 (decisions)
first, then build, then measure; keep the CPU suite green (GPU tests behind a marker).

## Other open items

- MCP server (`kb_search` / `kb_read` over MCP) — only when a client outside our code needs the KB
  (Claude Desktop, an IDE, another language). Our flows and agents use it in process (PLAN K7).
- Other track5 P7 items on demand: S3 / Drive / crawl connectors, Qdrant hybrid, LanceDB, wiki export.

- K4 gate: resume `scripts/bench_k4.py` when OpenAI credits exist (~$1.5; `docs/bench/k4.md`).
- K6 follow-up: a query router (track5 §9.8) sending relation questions to `mode="graph"`.
- Human check of the 30 answers in `docs/bench/d5_answers.jsonl`; OCR for scanned PDFs.
