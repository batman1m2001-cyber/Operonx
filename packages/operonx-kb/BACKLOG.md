# Backlog — outstanding work

Items deliberately deferred. Each one is **not done**: pick it up when its precondition is met.

## ⚠️ K5 · Visual page retrieval — OUTSTANDING (needs a GPU machine)

**Status:** not started. Deferred 2026-10-05 because the dev machine has no GPU; to be done on a
GPU PC. K6 (graph) was done first instead. Design: `../../docs/roadmap/track5_knowledge.md`
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

- Other track5 P7 items on demand: crawl connector, Qdrant hybrid, LanceDB, wiki export. (MCP, S3
  and Drive shipped in 0.2.3.)

- K4 gate (optional — K4 is built and opt-in; this only decides default-on). Decided 2026-10-06: run
  it on the **in-house model** (`google/gemma-4-E2B-it`, the callbot gateway), not OpenAI. Probed: plain
  calls ~1 s, JSON navigator answers correct in 0.2 s, ~130 calls/min. A run was started and stopped by
  choice at ~10% (1 123 of ~10 500 calls; ETA was 2-2.5 h). To run it, with this `llm.yaml`:

  ```yaml
  llm:inhouse:
    api_type: openai
    api_key: ${LLM_API_KEY}
    base_url: ${LLM_API_URL}
    model: ${LLM_MODEL_NAME}
    cost_per_input_token: 0.0
    cost_per_output_token: 0.0
  ```
  ```
  OMP_NUM_THREADS=8 uv run python scripts/bench_k4.py WORK --llm inhouse --llm-resources llm.yaml \
      --env ../educa-reminder-agent/.env
  ```
  Reusing the same `WORK` folder resumes for free (answers are cached in its catalog). `--cases N --docs M`
  samples it (both collections of a set get the same sample; the 56 documents already ingested are
  kept, since they are free). Measured by the text still to process, against the full run:
  `--cases 100 --docs 150` 57%, `100/100` 49%, `60/80` 46%, `30/40` 38%. It about halves the run,
  not more: xquad's 48 documents are all cited, and the cited legal PDFs are the long ones. Run it when
  nobody is testing the callbot: it shares that gateway.
- OCR on a real scanned corpus (0.2.3 shipped it opt-in; D5 had no scans: `docs/bench/ocr.md`).
