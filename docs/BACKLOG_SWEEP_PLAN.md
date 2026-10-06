# Plan: the Q4 backlog sweep (2026-10-06)

Everything left open after the Q4 roadmap, except K5 (visual page retrieval:
needs a GPU) and the K4 gate (in-house model run, later). Each item ships on
its own branch with its own tests and is merged when verified. Order: smallest
and least risky first; the callbot last, on `refactor/operonx-studio` only.

| # | Item | Where | Done when |
|---|---|---|---|
| B1 | meeting-prep-operonx end to end | seminar stack (`./stack.sh up` with `SEMINAR_MODEL_MODE=mock`, then `down`) | its 2 zone tests pass on operonx 1.16 + operonx-agents 0.1.2 |
| B2 | No `@graph` inside a test function | `packages/operonx-agents/tests` (and any other test still doing it) | the graphs are module level; suite green |
| B3 | Query router (track5 §9.8) | `packages/operonx-kb` | `mode="auto"` sends relation questions to `graph`, the rest to `hybrid`; measured on 2Wiki/MuSiQue + `xquad_vi`: keeps K6's multi-hop lift without its single-hop loss, or stays opt-in |
| B4 | D5 answer review + OCR | `packages/operonx-kb` | the 30 answers in `docs/bench/d5_answers.jsonl` reviewed against their sources, findings written down; scanned PDFs get text through an OCR adapter (an extra, never core) |
| B5 | KB over MCP + connectors | `packages/operonx-kb` | `operonx-kb mcp` serves `kb_search`/`kb_read` (stdio) with the caller's scope; S3 and Google Drive sources feed ingest as extras |
| B6 | E7 real-traffic gate | operonx online eval | an `OnlineEval` over the callbot's recorded runs, read in place (no copy); scores and alerts checked |
| B7 | `logprobs=True` cost in agents | `packages/operonx-agents` | measured; the default chosen on that number |
| B8 | Studio `feat/layered-layout` (2 WIP commits) | operonx-studio | finished and merged, or deleted, decided by screenshots against main |
| B9 | An agent's tools on the Studio canvas | operonx-studio | an `agent` op node lists its tools; desktop + phone screenshots |
| B10 | Callbot on operonx 1.16 | callbot `refactor/operonx-studio` | pinned to 1.16, suite green; live traces' default checked |
| B11 | Callbot's dead `qwen-turbo` resource | callbot `refactor/operonx-studio` | probed; repointed to a working model or removed |

Rules: module-level graphs only, edges written out; evidence before every fix;
never the telco gateways 9922/9926/9924; recorded calls are read in place, never
copied or replayed against a model; callbot never toward staging.

## Results (2026-10-06)

| # | Outcome | Where |
|---|---|---|
| B1 | Both zone tests pass in mock mode, along with the rest of meeting-prep's suite (155 tests). The seminar's mock model didn't stream, so agents returned empty answers. That exposed an agents bug: a stream with no reply counted as an empty success. It is now a `ModelError` after fallback. | ai-workflow-seminar #10; Operon #114 (agents 0.1.3) |
| B2 | All operonx-agents tests use module-level graphs. | #113 |
| B3 | `auto` is the default for a collection with a graph. Multi-hop R@5 +0.162 (2Wiki) and +0.068 (MuSiQue); single-hop unchanged. | #116 (kb 0.2.2), `packages/operonx-kb/docs/bench/router.md` |
| B4 | D5 answer review: 20 correct, 1 partial, 7 wrong, 2 abstained, 0 fabricated. OCR (`CollectionSpec.ocr`, extra `ocr` + the `tesseract` binary) ships opt-in. The corpus has no real scans: its 2 "scans" are blank pages. Median CER 4.2% on legal prose; diagrams and tables measure badly. | kb 0.2.3; `docs/bench/d5_review.md`, `docs/bench/ocr.md` |
| B5 | `operonx-kb mcp` serves `kb_search`/`kb_read` on stdio. The scope is the **server's**, fixed at start: an MCP client never says who it is. `S3Source` and `DriveSource` are job sources (extras `s3`, `drive`). Tested against fake clients, not live accounts. | kb 0.2.3 |
| B6 | `OnlineEval` over the 121 recorded callbot runs, read in place: 363 scores in 2.9 s, 120 of 121 runs pass. The failure is the run with cancelled ops. An alert fired past its threshold (0.0083 > 0.005). | scratchpad only (no repo change) |
| B7 | `logprobs` costs about +2.5% of p95 step latency, and nothing reads the score: it stays opt-in. | #115 |
| B8 | Deleted; main already does what it was building. Kept as the tag `archive/layered-layout`. | operonx-studio |
| B9 | An agent op's card and inspector list its tools. Also fixed: an op naming `assistant` without the `llm:` prefix now finds its resource. | operonx-studio #24 |
| B10 | Pinned to operonx 1.16, 303 tests pass. Live traces are left off (`live: false`): measuring them would mean opening the team ClickHouse with the new operonx. | callbot `refactor/operonx-studio` b41be1b |
| B11 | `qwen-turbo` was retired at the gateway; the fallback now uses `qwen3.7-flash`. | callbot `refactor/operonx-studio` 26416e6 |

Core tests: of the 186 `@graph`s defined inside test functions, 181 are now module level.
Values they closed over became module-level records (cleared per test), graph inputs, or
build-time graph parameters (`allow_race`, `parallel`, `sub`). Five stay inside `pytest.raises`:
each deliberately references an op outside the graph's scope, which only a closure can do.
The same 3442 tests are selected as on main; 3299 pass and the rest skip, as on main.
