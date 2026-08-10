# Operonx → Op Taxonomy Refactor Plan

**Status:** proposed. Sequenced for **1.1.0** (add semantic ops + deprecate
backend-named ops) → **2.0.0** (delete deprecated ops + `OpType` enum cleanup).

**TL;DR** — Operonx has two mutually inconsistent op-naming patterns
today: **semantic ops** (`LLMOp`, `EmbeddingOp`, `RerankOp` — each with a
factory + swappable backends) and **backend-named ops** (`OnnxOp`,
`TritonOp` — each a runtime wrapper without a semantic). Users hit
"should I use `TritonOp` for STT or wait for `SttOp`?" and there is no
good answer. This plan **deletes both backend-named ops** (no escape
hatches — user's own `@op` is the escape hatch), **adds three semantic
ops** (`SttOp`, `TtsOp`, `VectorSearchOp`), and **cleans up the `OpType`
enum** to match. Considered and rejected a `ClassifierOp` because
classification is too heterogeneous to abstract into one primitive (§2b).
Independent of the agent-framework work
(`AGENT_EXTENSION_PLAN.md`) — one is taxonomy hygiene, the other is
building new primitives on top.

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

- Each op has a stable semantic contract (`LLMOp` = text→text (+tools),
  `EmbeddingOp` = text→vector, `RerankOp` = docs+query→scored docs).
- Backends are swappable via `resource:` key + lazy-import factory.
- New backend = one file under the backend package + one enum entry + one
  factory branch. Optional deps isolated via `pip install operonx[<extra>]`.
- Users write `LLMOp.of(resource="claude-haiku")` and never care which
  transport is used.

### Pattern B — backend-named ops (bad)

```
OnnxOp    → coupled to the ONNX runtime; semantic is actually "classifier
             head over embedding vectors" but the name says nothing about that
TritonOp  → pure transport wrapper; no semantic at all; users manually
             map Triton tensor names to op inputs/outputs via inputs_map/outputs_map
```

- The op name announces the transport, not the intent.
- Users must know the backend to pick the op — the opposite of pattern A.
- Different transports for the same semantic each get their own op
  (Triton-hosted STT via `TritonOp`; a hypothetical Whisper-local STT
  would need a new op).
- `TritonOp`'s manual `inputs_map`/`outputs_map` is exactly the wiring
  `Param` was designed to obviate — it's a workaround, not a design.

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

---

## 2 · The design principle

**One way to do things: semantic op + backend adapter.** Every op in
`operonx/providers/` after this refactor:

1. Names its **semantic intent** (`SttOp`, `TtsOp`, `VectorSearchOp`),
   never its transport.
2. Delegates transport to a **factory-selected backend** in a sibling
   package (`providers/stt/`, `providers/tts/`, …).
3. Uses **`Param` for input/output typing** — no `inputs_map` /
   `outputs_map` bags.
4. Ships with **1–2 backend adapters day one**; more added in follow-ups
   or by users via the extension surface (subclass `BaseBackend` + register
   in factory + PR).

**No escape hatches.** If a user's model doesn't fit an existing semantic
op, the escape hatch is a bare `@op` — operonx already gives them
`bound="io"`, tracing, ResourceHub, `Param`-based typing for free. That's
~30 LOC per exotic model. The framework's job is to make primitives good,
not to prebuild wrappers for every backend under the sun.

Escape hatches accumulate. Every `SomethingInvokeOp` competes with the
matching `SomethingOp`, and some fraction of users pick the escape hatch
because "more control", never move to the semantic op, and the taxonomy
stays muddled forever. Deleting them forces the good path.

---

## 2a · The 4-criteria bar for "op-worthy"

Same bar used in `AGENT_EXTENSION_PLAN.md` §7.1a. A candidate earns a
dedicated `BaseOp` subclass only when it hits **all four**:

1. **Complex I/O contract** — transport, retries, response shapes that
   users would re-implement badly if left to bare `@op`.
2. **Rich metadata for tracing** — the span carries data the framework
   knows how to render (model, cost, tokens for LLMOp; provider, dim
   for EmbeddingOp; …).
3. **Reusable shape across many use cases** — the signature is stable
   enough that a base class is a real ceiling on variance.
4. **Non-trivial code volume** — enough boilerplate that inheriting from
   the base saves real work, not just aesthetics.

Applied to this plan's candidates:

| Candidate | Bar | Verdict |
|---|---|---|
| `SttOp` | 4/4 — audio↔transcript is stable across whisper/triton/openai/deepgram | ✅ ship |
| `TtsOp` | 4/4 — text→audio (+ streaming) is stable across triton/elevenlabs/openai/coqui | ✅ ship |
| `VectorSearchOp` | 4/4 — query-vector→top-K-docs is stable across qdrant/milvus/pgvector/faiss/chroma | ✅ ship |
| `ClassifierOp` | 2/4 — shape is **heterogeneous** (see §2b) | ❌ do NOT ship |
| `RetrieveOp` (generic KV) | 2/4 — subsumed by VectorSearchOp for the semantic case; trivial `@op` for the rest | ❌ do NOT ship |
| `PlanOp` / `ReasonOp` etc. | 1/4 — too broad, no stable shape | ❌ do NOT ship |

**The bar is the plan's real discipline.** Without it, every ML task
someone thinks of grows into an op class; taxonomy becomes noise.

---

## 2b · Why NOT `ClassifierOp` (heterogeneity argument)

"Classification" is a category, not a task. Every specific classification
task has subtly different shape:

| Task | Input | Output |
|---|---|---|
| Sentiment | text | single label + score |
| Intent (fixed labels) | text | single label + score (from a predefined set) |
| Intent (dynamic labels) | text + candidate_labels | single label + score |
| Toxicity | text | multi-label (violence, sex, hate, …) |
| NLI (entailment) | (premise, hypothesis) | single label + score — pair-input, not single |
| Zero-shot | text + candidate_labels | single label + score (labels change per call) |
| Token classification (NER) | text | per-token labels |
| Image / audio classification | different modality | (varies) |

A universal `ClassifierOp` ends up either **too generic** (users write
backend-specific `**kwargs` glue and lose type safety) or **too narrow**
(missing common cases like NLI, zero-shot, multi-label). We'd spend
follow-up releases growing warts to accommodate each new classification
shape.

**Compare with the ops that DO earn a class.** `SttOp` — audio in,
transcript out, done. `TtsOp` — text in, audio out, done.
`VectorSearchOp` — vector in, top-K docs out, done. Stable I/O across
every backend.

**The two honest paths for callers who need classification:**

1. **Use `LLMOp.of(fields=[...], parser="json", max_retries=N)`.** Works
   today. Expensive but flexible. Right choice when labels change often,
   the reasoning is non-trivial, or call volume is low.

2. **Write a bespoke `@op` around a dedicated classifier model.** ~15–50
   LOC around `onnxruntime.InferenceSession` (via
   `providers/onnx/backend.py:load_onnx_session()`) or `transformers.pipeline()`.
   Fast, deterministic, cheap. Right choice when labels are fixed, the
   call path is latency-sensitive, and volume is high.

Callbot's intent classification is (2)'s natural home, in
`callbot/ops/intent_classifier.py`, not in the framework.

**If concrete demand ever appears for a NARROW well-defined
classification task** (sentiment across many apps, moderation across
many LLM apps), we ship `SentimentOp` / `ModerationOp` specifically —
each with stable shape. Never the generic `ClassifierOp` umbrella.

**Analogous rejection:** operonx doesn't have a generic `RetrievalOp`
because retrieval is heterogeneous (vector vs BM25 vs hybrid vs
cross-encoder). We're shipping `VectorSearchOp` specifically because
vector retrieval has stable shape. Same reasoning, same result.

---

## 3 · What's changing — full op inventory

| Op | Category | Semantic | Backend package | Status |
|---|---|---|---|---|
| `LLMOp` | unchanged | text → text (+ tools + structured) | `providers/llms/` | Reference pattern. No work. |
| `EmbeddingOp` | unchanged | text → vector | `providers/embeddings/` | Reference pattern. No work. |
| `RerankOp` | unchanged | (docs, query) → scored docs | `providers/rerankers/` | Reference pattern. No work. |
| `SttOp` | **NEW** | audio → transcript | `providers/stt/` (triton, whisper, openai, deepgram) | 1.1.0 |
| `TtsOp` | **NEW** | text → audio | `providers/tts/` (triton, elevenlabs, openai, coqui, azure) | 1.1.0 |
| `VectorSearchOp` | **NEW** | query vector → top-K docs | `providers/vector_stores/` (qdrant, milvus, pgvector, faiss, chroma) | 1.1.0 |
| `OnnxOp` | **DEPRECATE (1.1.0) → DELETE (2.0.0)** | (was: MLP/attention classifier over embeddings) | — | Users write bespoke `@op` around `providers/onnx/backend.py:load_onnx_session()` — the embedding-head shape is too callbot-specific to earn a framework op (§2b) |
| `TritonOp` | **DEPRECATE (1.1.0) → DELETE (2.0.0)** | (was: generic Triton client) | — | Triton is now a backend under `stt/`, `tts/`, `vector_stores/`. Exotic Triton models: users write bespoke `@op` around `providers/triton/client.py` (extracted from today's TritonOp) |

**Net:** +3 semantic ops, –2 deletions, 3 unchanged. No renames, no
escape hatches. See §2a for the "op-worthy" bar and §2b for why
`ClassifierOp` was considered and rejected.

---

## 4 · Per-op sketches

### 4.1 · `SttOp`

```python
# operonx/providers/ops/stt.py
class SttOp(BaseOp):
    """Speech-to-text. Audio bytes/samples → transcript."""

    type: OpType = "stt"

    inputs = {
        "audio": Param(type=(bytes, list, "numpy.ndarray"), required=True),
        "sample_rate": Param(type=int, required=False, default=16000),
        "language": Param(type=str, required=False, default=None),
    }
    outputs = {
        "transcript": Param(type=str),
        "confidence": Param(type=float, required=False),
        # optional per-backend extras:
        "words": Param(type=list, required=False),        # word-level timestamps
        "embedding": Param(type=list, required=False),    # some backends return speaker embedding
    }
```

**Backends day-one:** Triton (matches callbot's current usage), Whisper (local).
**Backends soon:** OpenAI Whisper API, Deepgram.

### 4.2 · `TtsOp`

```python
# operonx/providers/ops/tts.py
class TtsOp(BaseOp):
    """Text-to-speech. Text → audio bytes/samples.

    Streaming shape: yield {"audio_chunk": bytes} per chunk when backend supports.
    Batch shape: return {"audio": bytes, "sample_rate": int} once.
    """

    type: OpType = "tts"

    inputs = {
        "text": Param(type=str, required=True),
        "voice": Param(type=str, required=False, default=None),
        "speed": Param(type=float, required=False, default=1.0),
        "sample_rate": Param(type=int, required=False, default=24000),
    }
    outputs = {
        "audio": Param(type=bytes),                      # batch mode
        "audio_chunk": Param(type=bytes, required=False), # streaming mode
        "sample_rate": Param(type=int),
    }
```

**Backends day-one:** Triton, ElevenLabs (HTTP).
**Backends soon:** OpenAI TTS, Coqui XTTS, Azure Speech.

### 4.3 · `VectorSearchOp`

```python
# operonx/providers/ops/vector_search.py
class VectorSearchOp(BaseOp):
    """Vector similarity search over a store. query vector → top-K docs."""

    type: OpType = "vector-search"

    inputs = {
        "query_vector": Param(type=list, required=True),  # list[float]
        "top_k": Param(type=int, required=False, default=10),
        "filter": Param(type=dict, required=False, default=None),   # backend-specific metadata filter
        "namespace": Param(type=str, required=False, default=None),
    }
    outputs = {
        "docs": Param(type=list),                        # list[dict] — id + content + metadata
        "scores": Param(type=list),                      # list[float] — similarity per doc
    }
```

**Backends day-one:** Qdrant (HTTP client), FAISS (in-memory reference).
**Backends soon:** pgvector, Milvus, Chroma.

Composes naturally with `EmbeddingOp` for a RAG pipeline:

```python
@graph
def rag():
    q_emb  = EmbeddingOp(resource="bge-m3", texts=[PARENT["query"]])
    hits   = VectorSearchOp(resource="qdrant:docs", query_vector=q_emb["embeddings"][0], top_k=5)
    ranked = RerankOp(resource="bge-m3-reranker", docs=hits["docs"], query=PARENT["query"])
    llm    = LLMOp.of(resource="claude-haiku",
                       prompt="Context: {ctx}\n\nQ: {q}",
                       ctx=ranked["docs"], q=PARENT["query"])
    START >> q_emb >> hits >> ranked >> llm >> END
```

---

## 5 · Callbot's OnnxOp — the honest migration

Today's `OnnxOp` in callbot's ML pipeline takes **already-computed embedding
vectors** and runs an MLP or attention head → probabilities. That's a
"downstream scoring head" pattern — narrow and callbot-specific enough
that it doesn't earn a framework op (see §2b for the general "no generic
classifier" argument).

**Migration path:** callbot writes a bare `@op` wrapping the same ONNX
session helper that `providers/onnx/backend.py` already exposes. ~15 LOC:

```python
# in callbot: src/callbot/ops/classifier_head.py
from operonx.core import op
from operonx.providers.onnx.backend import load_onnx_session

_session = None  # module-level cache

@op(bound="cpu")
def classify_head(embeddings: list, role_ids: list = None, mask: list = None):
    global _session
    if _session is None:
        _session = load_onnx_session("agent-classifier.onnx")

    onnx_inputs = {"embeddings": embeddings}
    if role_ids is not None:
        onnx_inputs["role_ids"] = role_ids
    if mask is not None:
        onnx_inputs["mask"] = mask
    probs = _session.run(None, onnx_inputs)[0]
    return {"probabilities": probs.tolist()}
```

That's the whole thing. It replaces `OnnxOp(resource="agent-sentiment", …)`
with `classify_head(embeddings=…)`. Same behaviour, no framework
surface — the pattern is genuinely too callbot-specific to earn a
first-class semantic op. If a second user shows up with the same shape,
revisit as `EmbeddingScoreOp` / `ScoreHeadOp` — but not on speculation.

---

## 6 · Module layout — before / after

**Before (operonx 1.0.0):**

```
operonx/providers/
├── llms/       {anthropic, openai, gemini, azure}.py + base, config, factory, batch_coordinator
├── embeddings/ {tei, vllm, huggingface, onnx}.py    + base, config, factory
├── rerankers/  {tei, vllm, onnx}.py                 + base, config, factory
├── onnx/       backend.py, config.py, factory.py   ← standalone ONNX backend (used by OnnxOp)
├── ops/
│   ├── llm.py
│   ├── embedding.py
│   ├── rerank.py
│   ├── onnx.py       ← DELETE (no replacement op; bespoke @op instead)
│   └── triton.py     ← DELETE (Triton is a backend under stt/tts/vector_stores/)
├── _utils/     huggingface.py, onnx.py
├── auth/
└── parsing.py
```

**After (operonx 2.0.0):**

```
operonx/providers/
├── llms/          {anthropic, openai, gemini, azure}.py
├── embeddings/    {tei, vllm, huggingface, onnx}.py
├── rerankers/     {tei, vllm, onnx}.py
├── stt/           {triton, whisper, openai, deepgram}.py + base, config, factory   ← NEW
├── tts/           {triton, elevenlabs, openai, coqui, azure}.py + base, config, factory  ← NEW
├── vector_stores/ {qdrant, faiss, pgvector, milvus, chroma}.py + base, config, factory   ← NEW
├── onnx/          backend.py, config.py, factory.py    ← KEEP (used by embeddings/onnx.py, rerankers/onnx.py, and user @ops for bespoke classifiers)
├── triton/        client.py, dtypes.py                  ← NEW (extracted from today's TritonOp — low-level helper used by stt/triton.py, tts/triton.py, vector_stores/triton.py, and user @ops for exotic Triton models)
├── ops/
│   ├── llm.py
│   ├── embedding.py
│   ├── rerank.py
│   ├── stt.py            ← NEW
│   ├── tts.py            ← NEW
│   └── vector_search.py  ← NEW
├── _utils/
│   ├── huggingface.py
│   ├── onnx.py
│   └── audio.py          ← NEW (codec, resample, chunk-assembly helpers shared by stt/ + tts/)
├── auth/
└── parsing.py
```

**What's NOT in the layout:**

- **No `providers/audios/` grouping.** STT and TTS backends barely
  overlap (only `azure-speech` and `openai` show up in both, with
  different APIs). Grouping earns no code-share win. Shared audio
  utilities live at `providers/_utils/audio.py` — same tier as the
  existing `providers/_utils/onnx.py`. If callbot's roadmap actually
  introduces 5+ audio ops (VAD, wake-word, diarization, speaker-embed,
  denoise) in-framework, revisit.
- **No `providers/ops/onnx_invoke.py` / `triton_invoke.py` escape
  hatches.** See §2 for the argument.
- **No `providers/classifiers/` package or `ClassifierOp`.** Considered
  and rejected (§2b). Sentiment/intent/toxicity/NLI/etc. all have
  subtly different I/O shapes; a universal `ClassifierOp` would either
  be too generic or too narrow. Callers use `LLMOp.of(fields=…)` or
  write bespoke `@op`s. If concrete demand appears for a narrow
  well-defined task (`SentimentOp`, `ModerationOp`), ship it
  specifically then — not on speculation.

---

## 7 · `OpType` enum cleanup

Ships with 2.0.0 (breaking, but only visible to code that reads `OpType`
directly, which is rare).

| Current entry | 2.0.0 action | Reason |
|---|---|---|
| `"llm"` | keep | Matches `LLMOp` |
| `"embedding"` | keep | Matches `EmbeddingOp` |
| `"rerank"` | keep | Matches `RerankOp` |
| — | **add `"stt"`** | Matches new `SttOp` |
| — | **add `"tts"`** | Matches new `TtsOp` |
| — | **add `"vector-search"`** | Matches new `VectorSearchOp` |
| `"milvus"` | **remove** | Backend-named; no matching op; Milvus is a backend under `vector_stores/` |
| `"mongo"` | **remove** | Same |
| `"s3"` | **remove** | Same |
| `"for"` | **remove** | Replaced by generator ops + `Ref.parallel()` in 1.0.0 |
| `"while"` | **remove** | Replaced by back-edge loops (Phase 3) in 1.0.0 |
| `"stream"` | **remove** | Replaced by `LLMOp(stream=True)` / generator ops in 1.0.0 |
| `"parser"` | **remove** | `ParserOp` was removed in 1.0.0; parsing is inside `LLMOp.of(fields=…)` |
| `"onnx"` | **remove** (if present today) | `OnnxOp` deleted; ONNX is a backend under `embeddings/`, `rerankers/`, plus a helper for user `@op`s |
| `"triton"` | **remove** (if present today) | `TritonOp` deleted; Triton is a backend under `stt/`, `tts/`, `vector_stores/`, plus a helper (`providers/triton/client.py`) for user `@op`s |
| `"tool-executor"` | keep or migrate to `"tool"` | Aligns with `ToolOp` from `AGENT_EXTENSION_PLAN.md` |
| `"mcp"` | keep (reserved) | Future MCP client op |
| `"graph"`, `"branch"`, `"code"`, `"lambda"`, `"prompt"`, `"doc-processor"`, `"data"`, `"default"`, `"dummy"`, `"interrupt"`, `"emit"` | keep | Real infrastructure / control-flow types |

---

## 8 · Migration path — versioned + phased

### operonx 1.1.0 — additive

- Ship the three new semantic ops (`SttOp`, `TtsOp`, `VectorSearchOp`)
  with 1–2 backends each.
- Ship `providers/{stt,tts,vector_stores}/` packages with `base.py` +
  `config.py` + `factory.py` per the reference pattern.
- Ship `providers/triton/{client,dtypes}.py` — the low-level Triton
  helper extracted from today's `TritonOp` (used by `stt/triton.py`,
  `tts/triton.py`, `vector_stores/triton.py`, and user `@op`s for
  exotic Triton models).
- Ship `providers/_utils/audio.py`.
- `OnnxOp` and `TritonOp` **remain functional** but emit a
  `DeprecationWarning` on `__init__` pointing to the replacement:
  ```
  OnnxOp is deprecated (removed in 2.0.0). Write a bespoke @op around
  operonx.providers.onnx.backend.load_onnx_session — the framework
  no longer ships a generic classifier op. See §2b of
  OP_TAXONOMY_REFACTOR_PLAN.md for reasoning.
  ```
  ```
  TritonOp is deprecated (removed in 2.0.0). Options:
    - For STT: use SttOp(resource="triton:<model>")
    - For TTS: use TtsOp(resource="triton:<model>")
    - For vector search: use VectorSearchOp(resource="triton:<model>")
    - For exotic Triton models: write a bespoke @op around
      operonx.providers.triton.client.TritonClient
  ```
- Consumers (callbot, others) migrate on 1.1.0 at their pace.
- `OpType` enum unchanged in 1.1.0 (backward-compat).

### operonx 2.0.0 — breaking cleanup

- **Delete `OnnxOp`** (`operonx/providers/ops/onnx.py`) — one-line
  removal + `__init__.py` export cleanup.
- **Delete `TritonOp`** (`operonx/providers/ops/triton.py`) — same.
- **`OpType` enum cleanup** per §7 (remove backend-named + stale entries,
  add new semantic entries).
- **Ship `MIGRATION.md` §Op-taxonomy** with the same recipes users saw
  in the 1.1.0 deprecation warnings.

### After 2.0.0 — backfill

- Add remaining backends per op (all the "soon" entries in §4).
- Ship a `providers/tts/coqui.py`, `providers/stt/deepgram.py`,
  `providers/vector_stores/pgvector.py` etc. as demand appears — none of
  these block 2.0.0.

---

## 9 · Callbot migration in detail

Callbot uses `TritonOp` in one place (STT) and `OnnxOp` in one place
(bespoke classifier). Migration is small and can happen on 1.1.0.

### 9.1 · `TritonOp(stt) → SttOp(triton)`

Before ([`src/callbot/graph.py`](../../educa-reminder-agent/src/callbot/graph.py) STT section):

```python
stt = TritonOp(
    resource="stt",
    inputs_map={"AUDIO_SIGNAL": "speech_audio"},
    outputs_map={"TRANSCRIPT": "transcript", "EMBEDDING": "embedding"},
    inputs={"speech_audio": stt_input["speech_audio"]},
)
```

After:

```python
from operonx.providers.ops import SttOp

stt = SttOp(
    resource="stt",                          # resolves to a Triton STT backend via resources.yaml
    audio=stt_input["speech_audio"],
    sample_rate=16000,
)
# stt["transcript"], stt["embedding"] downstream unchanged
```

Same Triton model behind the scenes — `providers/stt/triton.py` handles
the `inputs_map`/`outputs_map` wiring internally, driven by the backend's
config. Users stop caring about tensor names.

### 9.2 · `OnnxOp(classifier) → bare @op`

See §5. ~15 LOC in `callbot/ops/classifier_head.py`. The pattern is
callbot-specific enough that the framework doesn't provide a semantic
op for it (see §2b).

---

## 10 · Phase roadmap

Compressed: additive work is parallelizable; deprecation + delete are
just marker commits.

```
Week 1  P0 · P1        _utils/audio.py + triton/client.py + SttOp + TtsOp
Week 2       P2        VectorSearchOp + 2 backends
Week 2            P3   Deprecation warnings on OnnxOp/TritonOp
                                                  ← Ship 1.1.0

Later       P4        Delete OnnxOp/TritonOp + OpType cleanup
                                                  ← Ship 2.0.0
```

| # | Phase | Deliverable | Size |
|---|---|---|---|
| **P0** | Scaffolding | `providers/_utils/audio.py` (codec, resample, chunk-assembly). `providers/triton/{client,dtypes}.py` (low-level helper extracted from today's TritonOp). Empty package skeletons for `stt/`, `tts/`, `vector_stores/` each with `base.py` (ABC) + `config.py` (type enum) + `factory.py` (dispatcher + lazy imports). | 1–1.5d |
| **P1** | Stt + Tts | Op classes + 2 backends each. Stt: Triton + Whisper (local). Tts: Triton + ElevenLabs. Streaming semantics for Tts. Tests + docs. | 3–4d |
| **P2** | VectorSearch | Op class + 2 backends (Qdrant, FAISS). Tests + docs + a RAG example (`ex16_rag_pipeline`). | 2–3d |
| **P3** | Deprecation | `DeprecationWarning` on `OnnxOp.__init__` / `TritonOp.__init__` pointing to replacements. `CHANGELOG.md` entry. Ship 1.1.0. | 0.5d |
| **P4** | Delete + enum | Remove `operonx/providers/ops/{onnx,triton}.py`. Trim `OpType` per §7. `MIGRATION.md` §Op-taxonomy. Ship 2.0.0. | 1d |

**1.1.0 total: ~7 days of focused work.** Down from ~9 in the previous
draft after dropping `ClassifierOp` (see §2b). 2.0.0 delta is trivial once
callbot has migrated.

---

## 11 · Honest gaps

The refactor is clean but not free.

1. **Backend coverage will be incomplete on day one.** Shipping 1–2
   backends per new op means many "your model isn't supported" moments.
   Mitigation: the factory + lazy-import + `pip install
   operonx[<extra>]` pattern makes adding a backend a small, isolated
   PR. Document the extension recipe in `CONTRIBUTING.md`.

2. **Bespoke callbot classifier stays outside operonx.** The
   embedding-head MLP/attention shape doesn't earn a semantic op — both
   because sample size is one AND because classification broadly is too
   heterogeneous for a universal op (§2b). Callbot maintains ~15 LOC of
   `@op` code and owns the ONNX runtime lifecycle for that model.

3. **No `ClassifierOp` means callers with light classification needs
   fall back to `LLMOp.of(fields=…)`.** That's more expensive
   (200–1000ms vs 5–30ms) but it works today with zero migration
   effort. Users only feel the cost if they're latency-sensitive AND
   volume-heavy — at which point a bespoke `@op` around a dedicated
   classifier model is the right answer, not a framework abstraction.

4. **Vector store integrations are heavy.** Each backend is 100–200 LOC
   + optional deps (`qdrant-client`, `pymilvus`, `faiss-cpu`,
   `pgvector`, `chromadb`). We ship 2 to start; users likely need more.
   `pip install operonx[qdrant]` etc. isolates the pain.

5. **TTS streaming semantics vary wildly by backend.** Some yield
   per-word audio, some per-sentence, some per-fixed-chunk-ms, some
   don't stream at all. `TtsOp`'s streaming shape (yield
   `{"audio_chunk": bytes}` per chunk) will need per-backend
   normalization work. First cut may only support batch mode; streaming
   ships as a follow-up.

6. **Loss of `TritonOp`'s escape-hatch role for exotic Triton models.**
   The lift to write your own `@op` around `tritonclient.grpc.aio` is
   ~30 LOC — narrowed to ~15 LOC now that `providers/triton/client.py`
   ships as a low-level helper. Acknowledged as a real cost; the
   counterargument (§2) is that the escape hatch was corrosive to the
   taxonomy, so this trade is intentional.

---

## 12 · Relationship to `AGENT_EXTENSION_PLAN.md`

Both plans are independent — one is taxonomy hygiene, the other is
building new agent primitives on top of existing ones. The overlap is
one line:

> §4 of `AGENT_EXTENSION_PLAN.md` says "RAG lives in providers/ops/, not
> agents/". This plan makes that concrete by shipping `VectorSearchOp`
> there. When the agent-plan's P2 (memory) lands, it consumes
> `VectorSearchOp` via its Ref, not by taking a dependency on the
> underlying vector store.

Nothing else in this plan depends on the agent plan or vice versa.
Ship in whichever order lands first.

---

## 13 · First concrete step

1. **P0 · ~1 day** — write `providers/_utils/audio.py` (skeleton with
   `to_pcm16(...)`, `resample(...)`, `chunk_pcm(...)`, `pcm_to_wav(...)`).
   Extract today's `TritonOp` internals into
   `providers/triton/{client,dtypes}.py` — async gRPC client pooling +
   numpy↔Triton dtype translation, no operonx-specific concepts. Scaffold
   empty `providers/{stt,tts,vector_stores}/` packages each with
   `base.py` (BaseX ABC), `config.py` (XType enum + XConfig dataclass),
   `factory.py` (dispatcher, lazy imports, ImportError messages pointing
   to `pip install operonx[<extra>]`), `__init__.py` with the
   lazy-import shim used by `embeddings/__init__.py`.

2. **1-page ADR before P1 code** — cover:
   - Exact `Param` shapes for each new op (§4 sketches are indicative,
     not final).
   - How `resource:` strings dispatch to backends. Recommend
     `<backend>:<name>` (`triton:stt`, `whisper:medium`,
     `qdrant:docs`) — the same convention `embeddings/factory.py` uses
     via `EmbeddingType` enum.
   - Streaming contract for `TtsOp` (yield-per-chunk vs batch).
   - `VectorSearchOp` filter dialect — accept a backend-native dict, or
     define a `Filter(...)` DSL that translates? (Recommend: dict for
     v1; DSL is a follow-up.)
   - Optional-deps naming: `operonx[stt-whisper]` vs `operonx[whisper]`.
     Recommend: prefix by op (`stt-whisper`, `tts-elevenlabs`) so users
     know what surface it unlocks.
   - Confirm: **no `ClassifyOp` / `ClassifierOp` shipping in this
     plan** (§2b). If a reviewer proposes adding one, the ADR points
     them to §2b's heterogeneity argument before code is written.

3. **Then P1 — build in this order:**
   1. `providers/stt/{base, config, factory, triton}.py`
   2. `providers/ops/stt.py` — `SttOp` class
   3. `providers/stt/whisper.py` — second backend to prove the factory
   4. `providers/tts/{base, config, factory, triton}.py`
   5. `providers/ops/tts.py` — `TtsOp` class
   6. `providers/tts/elevenlabs.py` — second backend
   7. Unit tests + integration test hitting the Triton stub for STT/TTS
   8. Doc: `docs/guide/08-stt-tts.md` + `docs/api/providers.md` update

Everything after P1 compounds on the scaffolding — P2 (VectorSearch)
follows the same pattern for `vector_stores/`.

---

## Sources studied

- `operonx/providers/ops/{llm,embedding,rerank,onnx,triton}.py` — current op shapes
- `operonx/providers/{llms,embeddings,rerankers}/{base,config,factory}.py` — the reference pattern
- `operonx/providers/onnx/backend.py` — the standalone ONNX backend (kept as helper for user `@op`s)
- `operonx/providers/_utils/{huggingface,onnx}.py` — shared helper pattern (`_utils/audio.py` follows this)
- `operonx/core/configs/op_config.py` — the `OpType` enum
- `/home/thanglq/educa-reminder-agent/src/callbot/graph.py` — the two concrete uses of `TritonOp` + `OnnxOp` this refactor migrates
- `MIGRATION.md` (operonx 1.0.0) — deprecation-then-remove pattern (`PARENT.shared`, `GraphOp.loop`, `ask()`, `ParserOp` shipped that way)
- LangChain / LlamaIndex / Haystack — semantic-op naming precedent for STT / TTS / VectorSearch across the ecosystem
