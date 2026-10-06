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
