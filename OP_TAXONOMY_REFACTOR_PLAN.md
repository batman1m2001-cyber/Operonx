# Operonx → Op Taxonomy Refactor Plan

**Status:** proposed. Sequenced for **1.1.0** (add semantic ops + deprecate
backend-named ops) → **2.0.0** (delete deprecated ops + `OpType` enum cleanup).

**TL;DR** — Operonx has two mutually inconsistent op-naming patterns
today: **semantic ops** (`LLMOp`, `EmbeddingOp`, `RerankOp` — each with a
factory + swappable backends) and **backend-named ops** (`OnnxOp`,
`TritonOp` — each a runtime wrapper without a semantic). Users hit
"should I use `TritonOp` for STT or wait for `SttOp`?" and there is no
good answer. This plan **deletes both backend-named ops** (no escape
hatches — user's own `@op` is the escape hatch), **adds four semantic
ops** (`ClassifyOp`, `SttOp`, `TtsOp`, `VectorSearchOp`), and **cleans up
the `OpType` enum** to match. Independent of the agent-framework work
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

1. Names its **semantic intent** (`ClassifyOp`, `SttOp`, `VectorSearchOp`),
   never its transport.
2. Delegates transport to a **factory-selected backend** in a sibling
   package (`providers/classifiers/`, `providers/stt/`, …).
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

## 3 · What's changing — full op inventory

| Op | Category | Semantic | Backend package | Status |
|---|---|---|---|---|
| `LLMOp` | unchanged | text → text (+ tools + structured) | `providers/llms/` | Reference pattern. No work. |
| `EmbeddingOp` | unchanged | text → vector | `providers/embeddings/` | Reference pattern. No work. |
| `RerankOp` | unchanged | (docs, query) → scored docs | `providers/rerankers/` | Reference pattern. No work. |
| `ClassifyOp` | **NEW** | text → (label, score) per item | `providers/classifiers/` (onnx, triton, hf, openai, tei) | 1.1.0 |
| `SttOp` | **NEW** | audio → transcript | `providers/stt/` (triton, whisper, openai, deepgram) | 1.1.0 |
| `TtsOp` | **NEW** | text → audio | `providers/tts/` (triton, elevenlabs, openai, coqui, azure) | 1.1.0 |
| `VectorSearchOp` | **NEW** | query vector → top-K docs | `providers/vector_stores/` (qdrant, milvus, pgvector, faiss, chroma) | 1.1.0 |
| `OnnxOp` | **DEPRECATE (1.1.0) → DELETE (2.0.0)** | (was: MLP/attention classifier over embeddings) | — | Users migrate to `ClassifyOp` OR bare `@op` for the embedding-head case |
| `TritonOp` | **DEPRECATE (1.1.0) → DELETE (2.0.0)** | (was: generic Triton client) | — | Users migrate to `SttOp` / `TtsOp` / `ClassifyOp` (Triton backend) OR bare `@op` for anything else |

**Net:** +4 semantic ops, –2 ops, 3 unchanged. No renames, no escape hatches.

---

## 4 · Per-op sketches

### 4.1 · `ClassifyOp`

```python
# operonx/providers/ops/classify.py
class ClassifyOp(BaseOp):
    """Classify text into one of N labels; returns per-item label + score.

    Semantic contract: text-in, labels-out. Common cases: sentiment,
    intent, toxicity, safety-check, NLI (accepts tuple[str,str] items
    for premise/hypothesis).
    """

    type: OpType = "classify"

    inputs = {
        "texts": Param(type=list, required=True),        # list[str] or list[tuple[str,str]]
    }
    outputs = {
        "labels": Param(type=list),                      # list[str] — winning label per item
        "scores": Param(type=list),                      # list[dict[str, float]] — full distribution
    }

    # Backend selection via resource="classifier:agent-sentiment" — factory
    # dispatches to ONNXClassifier / TritonClassifier / HFClassifier / …
```

**Backends day-one:** ONNX (local), HuggingFace (transformers pipeline).
**Backends soon:** Triton, OpenAI moderation, TEI.

### 4.2 · `SttOp`

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

### 4.3 · `TtsOp`

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

### 4.4 · `VectorSearchOp`

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
"downstream scoring head" pattern, NOT a text classifier. It doesn't fit
`ClassifyOp`'s text-in signature.

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
│   ├── onnx.py       ← DELETE
│   └── triton.py     ← DELETE
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
├── classifiers/   {onnx, hf, triton, openai, tei}.py + base, config, factory     ← NEW
├── stt/           {triton, whisper, openai, deepgram}.py + base, config, factory  ← NEW
├── tts/           {triton, elevenlabs, openai, coqui, azure}.py + base, config, factory  ← NEW
├── vector_stores/ {qdrant, faiss, pgvector, milvus, chroma}.py + base, config, factory   ← NEW
├── onnx/          backend.py, config.py, factory.py         ← KEEP (used by classifiers/onnx.py + user @ops)
├── ops/
│   ├── llm.py
│   ├── embedding.py
│   ├── rerank.py
│   ├── classify.py       ← NEW
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

---

## 7 · `OpType` enum cleanup

Ships with 2.0.0 (breaking, but only visible to code that reads `OpType`
directly, which is rare).

| Current entry | 2.0.0 action | Reason |
|---|---|---|
| `"llm"` | keep | Matches `LLMOp` |
| `"embedding"` | keep | Matches `EmbeddingOp` |
| `"rerank"` | keep | Matches `RerankOp` |
| — | **add `"classify"`** | Matches new `ClassifyOp` |
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
| `"onnx"` | **remove** (if present today) | `OnnxOp` deleted; ONNX is a backend under `classifiers/`, `embeddings/`, `rerankers/` |
| `"triton"` | **remove** (if present today) | `TritonOp` deleted; Triton is a backend under `stt/`, `tts/`, `classifiers/` |
| `"tool-executor"` | keep or migrate to `"tool"` | Aligns with `ToolOp` from `AGENT_EXTENSION_PLAN.md` |
| `"mcp"` | keep (reserved) | Future MCP client op |
| `"graph"`, `"branch"`, `"code"`, `"lambda"`, `"prompt"`, `"doc-processor"`, `"data"`, `"default"`, `"dummy"`, `"interrupt"`, `"emit"` | keep | Real infrastructure / control-flow types |

---

## 8 · Migration path — versioned + phased

### operonx 1.1.0 — additive

- Ship all four new semantic ops (`ClassifyOp`, `SttOp`, `TtsOp`,
  `VectorSearchOp`) with 1–2 backends each.
- Ship `providers/{classifiers,stt,tts,vector_stores}/` packages with
  `base.py` + `config.py` + `factory.py` per the reference pattern.
- Ship `providers/_utils/audio.py`.
- `OnnxOp` and `TritonOp` **remain functional** but emit a
  `DeprecationWarning` on `__init__` pointing to the replacement:
  ```
  OnnxOp is deprecated (removed in 2.0.0). See:
    - For classification: use ClassifyOp(resource="classifier:...")
    - For embedding-head scoring: write a bare @op — see
      operonx/providers/onnx/backend.py:load_onnx_session
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

See §5. ~15 LOC in `callbot/ops/classifier_head.py`.

---

## 10 · Phase roadmap

Compressed: additive work is parallelizable; deprecation + delete are
just marker commits.

```
Week 1  P0 · P1        _utils/audio.py + ClassifyOp + 2 backends
Week 2       P2        SttOp + TtsOp + 2 backends each
Week 3            P3   VectorSearchOp + 2 backends
Week 3            P4   Deprecation warnings on OnnxOp/TritonOp
                                                  ← Ship 1.1.0

Later       P5        Delete OnnxOp/TritonOp + OpType cleanup
                                                  ← Ship 2.0.0
```

| # | Phase | Deliverable | Size |
|---|---|---|---|
| **P0** | Scaffolding | `providers/_utils/audio.py` (codec, resample, chunk-assembly). Empty package skeletons for `classifiers/`, `stt/`, `tts/`, `vector_stores/` each with `base.py` (ABC) + `config.py` (type enum) + `factory.py` (dispatcher + lazy imports). | 1d |
| **P1** | ClassifyOp | Op class + 2 backends (ONNX, HuggingFace). Tests + docs. | 2–3d |
| **P2** | Stt + Tts | Op classes + 2 backends each. Stt: Triton + Whisper (local). Tts: Triton + ElevenLabs. Streaming semantics for Tts. Tests + docs. | 3–4d |
| **P3** | VectorSearch | Op class + 2 backends (Qdrant, FAISS). Tests + docs + a RAG example (`ex16_rag_pipeline`). | 2–3d |
| **P4** | Deprecation | `DeprecationWarning` on `OnnxOp.__init__` / `TritonOp.__init__` pointing to replacements. `CHANGELOG.md` entry. Ship 1.1.0. | 0.5d |
| **P5** | Delete + enum | Remove `operonx/providers/ops/{onnx,triton}.py`. Trim `OpType` per §7. `MIGRATION.md` §Op-taxonomy. Ship 2.0.0. | 1d |

**1.1.0 total: ~9 days of focused work.** 2.0.0 delta is trivial once callbot has migrated.

---

## 11 · Honest gaps

The refactor is clean but not free.

1. **Backend coverage will be incomplete on day one.** Shipping 1–2
   backends per new op means many "your model isn't supported" moments.
   Mitigation: the factory + lazy-import + `pip install
   operonx[<extra>]` pattern makes adding a backend a small, isolated
   PR. Document the extension recipe in `CONTRIBUTING.md`.

2. **Bespoke callbot classifier stays outside operonx.** The
   embedding-head MLP/attention shape doesn't earn a semantic op with a
   sample size of one. Callbot maintains ~15 LOC of `@op` code. That's
   honest, not a framework gap — but it does mean the callbot team owns
   the ONNX runtime lifecycle for that specific model.

3. **Vector store integrations are heavy.** Each backend is 100–200 LOC
   + optional deps (`qdrant-client`, `pymilvus`, `faiss-cpu`,
   `pgvector`, `chromadb`). We ship 2 to start; users likely need more.
   `pip install operonx[qdrant]` etc. isolates the pain.

4. **TTS streaming semantics vary wildly by backend.** Some yield
   per-word audio, some per-sentence, some per-fixed-chunk-ms, some
   don't stream at all. `TtsOp`'s streaming shape (yield
   `{"audio_chunk": bytes}` per chunk) will need per-backend
   normalization work. First cut may only support batch mode; streaming
   ships as a follow-up.

5. **`ClassifyOp` semantic ambiguity.** "Classify" could mean
   single-label, multi-label, sequence, per-token, zero-shot, few-shot
   in-context. Day-one shape is single-label; multi-label and
   zero-shot get added via backend capabilities (`ClassifyOp(resource="…",
   candidate_labels=[…])` triggers zero-shot when backend supports).
   Complex enough that a `ClassifyOp` `README` in `providers/classifiers/`
   is warranted.

6. **Loss of `TritonOp`'s escape-hatch role for exotic Triton models.**
   The lift to write your own `@op` around `tritonclient.grpc.aio` is
   ~30 LOC. Acknowledged as a real cost; the counterargument
   (§2) is that the escape hatch was corrosive to the taxonomy, so this
   trade is intentional.

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

1. **P0 · one day** — write `providers/_utils/audio.py` (skeleton with
   `to_pcm16(...)`, `resample(...)`, `chunk_pcm(...)`, `pcm_to_wav(...)`).
   Scaffold empty `providers/{classifiers,stt,tts,vector_stores}/`
   packages each with `base.py` (BaseX ABC), `config.py` (XType enum +
   XConfig dataclass), `factory.py` (dispatcher, lazy imports, ImportError
   messages pointing to `pip install operonx[<extra>]`), `__init__.py`
   with the lazy-import shim used by `embeddings/__init__.py`.

2. **1-page ADR before P1 code** — cover:
   - Exact `Param` shapes for each new op (§4 sketches are indicative,
     not final).
   - How `resource:` strings dispatch to backends (`classifier:<name>`
     vs `<backend>:<name>` — pick one convention and use it everywhere).
   - Streaming contract for `TtsOp` (yield-per-chunk vs batch).
   - `VectorSearchOp` filter dialect — accept a backend-native dict, or
     define a `Filter(...)` DSL that translates? (Recommend: dict for
     v1; DSL is a follow-up.)
   - Optional-deps naming: `operonx[stt-whisper]` vs `operonx[whisper]`.
     Recommend: prefix by op (`stt-whisper`, `tts-elevenlabs`) so users
     know what surface it unlocks.

3. **Then P1 — build in this order:**
   1. `providers/classifiers/{base, config, factory, onnx}.py`
   2. `providers/ops/classify.py` — `ClassifyOp` class
   3. `providers/classifiers/hf.py` — second backend to prove the factory
   4. Unit tests + integration test hitting a small HF classifier
   5. Doc: `docs/guide/08-classification.md` + `docs/api/providers.md` update

Everything after P1 compounds on the scaffolding — P2/P3 are the same
pattern for `stt/`, `tts/`, `vector_stores/`.

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
