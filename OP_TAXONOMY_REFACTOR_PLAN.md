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
| `VectorSearchOp` | 4/4 | ✅ ship (1.1.0) | Completes the text/RAG stack (LLM+Embedding+Rerank+VectorSearch). Stable core: `query vector → top-K docs`. Every RAG framework has one. |
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
better served by bare `@op` around a helper** is. Under that rule,
VectorSearchOp is the only new op this plan ships.

**Why VectorSearchOp does earn a slot but STT/TTS/etc don't:**

- **VectorSearchOp** completes the text/RAG stack that operonx already
  ships partially (LLMOp + EmbeddingOp + RerankOp). Its absence forces
  every RAG user to write 30-50 LOC of vector-store client + bare `@op`
  themselves. The composability payoff is real:
  `EmbeddingOp → VectorSearchOp → RerankOp → LLMOp` is the canonical
  RAG pipeline in one graph.
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
| `VectorSearchOp` | **NEW** | query vector → top-K docs | `providers/vector_stores/` (qdrant, faiss day-one) | 1.1.0 |
| `OnnxOp` | **DEPRECATE (1.1.0) → DELETE (2.0.0)** | (was: MLP/attention head over embeddings) | — | Users write bare `@op` around `providers/_utils/onnx.py::load_onnx_session`. |
| `TritonOp` | **DEPRECATE (1.1.0) → DELETE (2.0.0)** | (was: generic Triton client) | — | Users write bare `@op` around new `providers/triton/client.py` helper. Callbot migrates its one STT call site. |

**Net:** +1 semantic op, –2 deletions, 3 unchanged. See §3 for every
rejected candidate and why.

---

## 5 · `VectorSearchOp` sketch

```python
# operonx/providers/ops/vector_search.py
class VectorSearchOp(BaseOp):
    """Vector similarity search. query vector → top-K docs.

    Core contract stable across all backends. Optional capabilities
    (filter dialect, namespace, distance metric override) are per-backend
    and default to backend-native behavior.
    """

    type: OpType = "vector-search"

    inputs = {
        "query_vector": Param(type=list, required=True),           # list[float]
        "top_k":        Param(type=int,  required=False, default=10),
        "filter":       Param(type=dict, required=False, default=None),  # backend-native metadata filter
        "namespace":    Param(type=str,  required=False, default=None),
    }
    outputs = {
        "docs":   Param(type=list),   # list[dict] — id + content + metadata
        "scores": Param(type=list),   # list[float] — similarity per doc
    }
```

**Backends day-one:** Qdrant (HTTP client), FAISS (in-memory reference).
**Backends soon:** pgvector, Milvus, Chroma — via user PR following the
`embeddings/factory.py` pattern.

**Composes with the existing text stack:**

```python
@graph
def rag(query):
    q_emb  = EmbeddingOp(resource="bge-m3", texts=[query])
    hits   = VectorSearchOp(resource="qdrant:docs",
                             query_vector=q_emb["embeddings"][0], top_k=5)
    ranked = RerankOp(resource="bge-m3-reranker",
                       docs=hits["docs"], query=query)
    llm    = LLMOp.of(resource="claude-haiku",
                       prompt="Context: {ctx}\n\nQ: {q}",
                       ctx=ranked["docs"], q=query)
    START >> q_emb >> hits >> ranked >> llm >> END
```

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
├── vector_stores/ {qdrant, faiss}.py + base, config, factory       ← NEW
├── triton/        client.py, decode.py, dtypes.py                  ← NEW (low-level helper for user @ops)
├── onnx/          backend.py, config.py, factory.py                (unchanged — used by embeddings/onnx.py, rerankers/onnx.py, and user @ops)
├── ops/
│   ├── llm.py
│   ├── embedding.py
│   ├── rerank.py
│   └── vector_search.py                                            ← NEW
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

- Ship `VectorSearchOp` + `providers/vector_stores/` package (base +
  config + factory + Qdrant + FAISS backends).
- Ship `providers/triton/{client,decode,dtypes}.py` — extract from
  today's `providers/ops/triton.py`:
  - `client.py` — `get_triton_client(url)` with module-global cache
    (inherit `_triton_clients` verbatim); numpy→Triton dtype conversion.
  - `decode.py` — bytes/str output decoding + len==1 scalar flattening
    (extracted from `triton.py:266-284`).
  - `dtypes.py` — `_DTYPE_MAP` + coercion helpers.
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
Week 1  P0 · P1        providers/triton/ helper + VectorSearchOp + 2 backends
Week 1       P2        Deprecation warnings on OnnxOp/TritonOp
                                                  ← Ship 1.1.0

Later       P3        Delete OnnxOp/TritonOp + OpType cleanup
                                                  ← Ship 2.0.0
```

| # | Phase | Deliverable | Size |
|---|---|---|---|
| **P0** | Scaffolding | `providers/triton/{client,decode,dtypes}.py` (extracted from `providers/ops/triton.py`, preserving `_triton_clients` module-global cache). Empty `providers/vector_stores/{base,config,factory}.py` skeleton. | 1d |
| **P1** | VectorSearch | Op class (`providers/ops/vector_search.py`) + 2 backends (Qdrant HTTP client, FAISS in-memory). Tests + docs + a RAG example (`ex16_rag_pipeline`) composing EmbeddingOp → VectorSearchOp → RerankOp → LLMOp. | 2–3d |
| **P2** | Deprecation | `DeprecationWarning` on `OnnxOp.__init__` / `TritonOp.__init__` with the wording from §9. `CHANGELOG.md` entry. Ship 1.1.0. | 0.5d |
| **P3** | Delete + enum | Remove `operonx/providers/ops/{onnx,triton}.py`. Trim + extend `OpType` per §8. Retag `ParserError.op_type`. `MIGRATION.md` §Op-taxonomy. Ship 2.0.0. | 1d |

**1.1.0 total: ~4 days of focused work.** 2.0.0 delta is trivial once
callbot has migrated.

---

## 11 · Honest gaps

The refactor is clean but not free.

1. **Loss of `TritonOp`'s escape-hatch role.** Users with exotic Triton
   models pay ~15 LOC of `@op` around `providers/triton/client.py`.
   Real cost, deliberately taken (§2). Competitors (BentoML, KServe,
   Ray Serve) keep a generic invoke op; operonx does not on principle.

2. **Vector store backend coverage will be incomplete on day one.** Two
   backends (Qdrant + FAISS) is the minimum viable set. Users likely
   need pgvector, Milvus, Chroma soon. Mitigation: the factory pattern
   makes adding a backend a small isolated PR. Document the recipe in
   `providers/vector_stores/README.md`.

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

6. **ADR items open for P1:**
   - Resource-key convention: `<op>:<name>` (`vector-search:docs`,
     `triton:stt`)? Recommend yes — matches `EmbeddingType.ONNX = "onnx"`
     inside factories.
   - `VectorSearchOp` filter dialect: accept backend-native dict for
     v1; consider a `Filter(...)` DSL only if users hit backend-lock-in
     pain.
   - Optional-deps naming: `operonx[qdrant]` vs `operonx[vector-qdrant]`.
     Recommend the shorter form (backend name only) — matches
     `operonx[onnx]` today.

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

1. **P0 · ~1 day** — extract `providers/triton/{client,decode,dtypes}.py`
   from today's `providers/ops/triton.py`. Preserve `_triton_clients`
   module-global cache verbatim (critical for real-time latency).
   Scaffold `providers/vector_stores/{base,config,factory}.py`.

2. **1-page ADR before P1 code** — cover:
   - `VectorSearchOp` Param shapes (§5 is indicative, not final).
   - `resource:` string convention — recommend `<op>:<name>` matching
     `EmbeddingType.ONNX = "onnx"` factory idiom.
   - `filter` dialect for v1 — backend-native dict, escape hatch to
     backend-specific behavior. Filter DSL is v2+.
   - Optional-deps naming — `operonx[qdrant]` (backend name only).
   - `providers/triton/client.py` cache ownership — module-global
     `_triton_clients: dict[str, InferenceServerClient]` (inherited
     from today's `triton.py:49-57`), NOT per-op-instance.
   - Where the bytes/str output-decoding heuristic lives —
     `providers/triton/decode.py::decode_infer_output()`, called by
     user `@op`s explicitly.
   - **Confirm: no STT / TTS / Classifier / OCR / VAD ops shipping**
     (§3 table). Future contributors proposing them must present
     concrete demand + explain why bare `@op` around the low-level
     helpers is insufficient.

3. **Then P1 — build in this order:**
   1. `providers/vector_stores/{base,config,factory}.py`
   2. `providers/vector_stores/faiss.py` — start with FAISS (no
      network dep, simplest test path)
   3. `providers/ops/vector_search.py` — `VectorSearchOp` class
   4. `providers/vector_stores/qdrant.py` — second backend to prove
      the factory
   5. Unit tests: FAISS in-memory smoke test; Qdrant against a
      docker-compose'd instance in CI
   6. `examples/python/ex16_rag_pipeline/` — full
      EmbeddingOp → VectorSearchOp → RerankOp → LLMOp composition
   7. Doc: `docs/guide/08-vector-search.md` +
      `docs/api/providers.md` update

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
