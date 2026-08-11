# Migrating operonx

- [To 1.2.0](#migrating-to-operonx-120) — `OnnxOp` and `TritonOp` removed
- [To 1.0.0](#migrating-to-operonx-100) — `PARENT.shared`, `GraphOp.loop`,
  `@graph(until=)`, `ParserOp`, `ask()` removed

---

# Migrating to operonx 1.2.0

1.2.0 removes the two **backend-named** ops. Both named their *transport*
rather than a semantic, so the op name told you the runtime instead of
the intent and every backend needed its own op. Everything else in 1.1.x
is unaffected.

> **Note on timing.** The deprecation warnings shipped in 1.1.0 said
> "removed in 2.0.0". Removal was brought forward to 1.2.0. If you are
> pinned `operonx>=1.x` and use either op, upgrading to 1.2.0 **will**
> break you — pin `operonx<1.2.0` until you have migrated. Sorry for the
> mismatch; the recipes below are unchanged from what those warnings
> described.

## 1. `TritonOp` → a bare `@op` on `TritonClient`

The useful parts — a process-cached async gRPC client, numpy↔Triton dtype
translation, and text-output decoding — ship as
`operonx.providers.triton.TritonClient`. What is left is the tensor-name
mapping, which belongs to you.

**Before**

```python
from operonx.providers.ops import TritonOp

stt = TritonOp(
    resource="stt",
    inputs_map={"AUDIO_SIGNAL": "speech_audio"},
    outputs_map={"TRANSCRIPT": "transcript", "EMBEDDING": "embedding"},
    inputs={"speech_audio": prep["speech_audio"]},
)
```

**After**

```python
from operonx.core import op
from operonx.providers.triton import TritonClient

@op(bound="io")
async def stt(speech_audio):
    client = TritonClient.get("localhost:8001")   # pooled per URL
    r = await client.infer(
        model="fastconformer_asr",
        inputs={"AUDIO_SIGNAL": speech_audio},
        outputs=["TRANSCRIPT", "EMBEDDING"],
    )
    # Must be a LITERAL dict — operonx infers an op's declared outputs by
    # AST-parsing the return statement. A comprehension declares nothing,
    # and the graph then BUILDS fine but fails at runtime with
    # "(op, var) not found in schema".
    return {"transcript": r["TRANSCRIPT"], "embedding": r["EMBEDDING"]}

# in the graph
stt_node = stt(speech_audio=prep["speech_audio"])
```

Two things worth carrying over deliberately:

- **Always reach the client via `TritonClient.get(url)`.** It caches the
  gRPC channel per URL. Constructing a client per call adds connection
  setup to every request.
- **Request every output you consume.** `infer` maps an output it cannot
  read to `None` rather than raising, so dropping one degrades silently
  downstream instead of failing loudly.

If you were resolving config from `resources.yaml`, keep doing so — read
the `triton:<name>` entry with `ResourceHub.instance().get_config(...)`
inside your op.

## 2. `OnnxOp` → a bare `@op` on `load_onnx_session`

`OnnxOp`'s shape — a classifier head over precomputed embeddings — was
too narrow to earn a framework op.

**Before**

```python
from operonx.providers.ops import OnnxOp

pred = OnnxOp.of(resource="sentiment", embeddings=emb["embeddings"])
```

**After**

```python
from operonx.core import op
from operonx.providers._utils.onnx import load_onnx_session

_session = None

@op(bound="cpu")
def classify(embeddings: list):
    global _session
    if _session is None:
        # Returns a 3-TUPLE from a directory holding model.onnx +
        # tokenizer.json — not a bare session.
        _session, _tokenizer, _device = load_onnx_session("models/sentiment")
    probs = _session.run(None, {"embeddings": embeddings})[0]
    return {"probabilities": probs.tolist()}
```

**ONNX remains a first-class backend** for `EmbeddingOp` and `RerankOp`
via `api_type: onnx` — only the standalone op is gone.

## 3. `OpType` cleanup

Only affects code that reads the `OpType` Literal directly, which is rare.

| Entry | Change | Why |
|---|---|---|
| `for`, `while`, `stream` | removed | Superseded in 1.0.0 by back-edge loops, generator ops, `Ref.parallel()` |
| `parser` | removed | `ParserOp` went in 1.0.0; parsing lives in `LLMOp(fields=...)` |
| `milvus`, `mongo`, `s3` | removed | Named backends, not semantics; never had ops behind them |
| `interrupt`, `emit` | **added** | Set by `InterruptOp` / `EmitOp` since 1.0.0 but missing from the Literal |
| `vector-search`, `doc-fetch` | added in 1.1.0 | Match `VectorSearchOp` / `DocFetchOp` |

`ParserError` now reports `op_type="code"` instead of `"parser"`.

---

# Migrating to operonx 1.0.0

1.0.0 removes four surfaces that had deprecated / alternative paths in
0.11.x. This guide covers each with a before/after recipe. Nothing else
needs migrating — Phase 1/2/3 additions are backward-compatible.

## 1. `PARENT.shared(**vars)` → `PARENT.declare(**vars)`

Same shared-cell semantics; `declare()` additionally accepts a
`reducers=` kwarg for fan-in merge logic.

**Before**
```python
@graph
def wf():
    PARENT.shared(counter=0)
    ...
```

**After**
```python
@graph
def wf():
    PARENT.declare(counter=0)
    ...
```

**Optional bonus** — add a reducer for fan-in accumulation:

```python
import operator
PARENT.declare(counter=0, log=[], reducers={"log": operator.add})
```

## 2. `GraphOp.loop(...)` → back-edge inside `@graph`

Write the loop as a regular DAG plus a back-edge. The Phase 3 rewrite
pass synthesizes a hidden `_GraphLoop` for the scheduler.

**Before**
```python
with GraphOp.loop(name="counter", until="count >= 5", count=0) as loop:
    inc = increment(counter=PARENT["count"])
    inc["counter"] >> PARENT["count"]
    START >> inc >> END
```

**After**
```python
from operonx.core.ops.flow.branch_op import if_

@graph
def counter():
    PARENT.declare(count=0)
    inc = increment(counter=PARENT["count"])
    inc["counter"] >> PARENT["count"]
    START >> inc >> if_(PARENT["count"] >= 5, END).else_(inc)
```

The `if_(...).else_(inc)` is the back-edge — else-target routes back to
an earlier op. Each iteration commits its outputs to the shared cell;
the branch reads the updated value and decides to exit or loop again.

**When you compared two `Ref`s in the old until** — for example
`until="counter >= target"` where both counter and target are graph
inputs — the back-edge `if_()` can't directly compare two Refs (only
Ref-vs-literal). Compute the boolean inside an op and branch on it:

```python
@op
def inc_and_check(counter: int, target: int):
    new_counter = counter + 1
    return {"counter": new_counter, "done": new_counter >= target}

@graph
def wf(target):
    PARENT.declare(counter=0)
    step = inc_and_check(counter=PARENT["counter"], target=target)
    step["counter"] >> PARENT["counter"]
    START >> step >> if_(step["done"] == True, END).else_(step)
```

## 3. `@graph(until=..., max_iterations=...)` → depends on intent

The retry-loop sugar on `@graph` was removed. Two replacements:

### 3a. LLM parse/validate retry — use `LLMOp(max_retries=N)`

**Before**
```python
from operonx.providers.ops import ask

a = ask(
    resource="claude-haiku",
    prompt="Classify: {text}",
    fields=["result: str"],
    parser="xml",
    validators={"result": ["CONFIRM", "DENY", "@FALLBACK"]},
    until="error == None",
    max_iterations=3,
    error="init",
    text=PARENT["text"],
)
```

**After**
```python
from operonx.providers import LLMOp

a = LLMOp.of(
    resource="claude-haiku",
    prompt="Classify: {text}",
    fields=["result: str"],
    parser="xml",
    validators={"result": ["CONFIRM", "DENY", "@FALLBACK"]},
    max_retries=2,       # semantic retries only; on parse/validator failure
    retry_hint=True,     # inject last error into next prompt (default)
    text=PARENT["text"],
)
```

Fewer moving parts, no dual-mode magic seed, `retry_hint=True` gives
you Instructor-style error-guided retry for free.

### 3b. Control-flow retry — use a back-edge (see §2)

If the loop wasn't LLM-parsing but general retry logic, express it as
a back-edge with a branch that decides whether to continue.

## 4. `ParserOp` → `LLMOp(fields=..., parser=...)` or pure functions

`ParserOp` was folded into LLMOp. For text→struct without an LLM call,
use the pure functions in `operonx.providers.parsing`.

**Before**
```python
from operonx.core.ops import ParserOp

parser = ParserOp(
    format="xml",
    extract=["result: str"],
    inputs={"text": PARENT["text"]},
)
```

**After — with LLM**
```python
llm = LLMOp.of(
    resource="gpt-4o",
    prompt="Classify: {text}",
    fields=["result: str"],
    parser="xml",
    text=PARENT["text"],
)
```

**After — without LLM (pure text)**
```python
from operonx.providers.parsing import ExtractField, parse_and_extract

result = parse_and_extract(
    text=raw_text,
    parser="xml",
    fields=[ExtractField.from_string("result: str")],
    validators={"result": ["CONFIRM", "DENY", "@FALLBACK"]},
)
# → {"result": "...", "error": None} or {"result": None, "error": "..."}
```

## Also removed

- `operonx.providers.ops.ask` — subsumed by `LLMOp.of(fields=..., ...)`.
- `operonx.core.ops.ParserOp` export — gone from `operonx`,
  `operonx.core`, `operonx.core.ops`, `operonx.core.ops.transform`.

## Runtime behaviour changes

- **Fallback trigger narrowed.** `LLMOp(fallback=[...])` used to fire on
  ANY exception from the primary call. Now it fires only on:
  - `LLMRefusalError` (finish_reason ∈ `{content_filter, safety}` or
    non-empty `extras.refusal`)
  - hard exceptions from the SDK (transport-exhausted, unexpected)
  It does NOT fire on parse or validator failures — those use
  `max_retries` on the same resource. If your code relied on
  "fallback catches parse errors too," add `max_retries=` on LLMOp.
- **Transport retries.** Delegated to the underlying provider SDK
  (litellm / openai / anthropic all have battle-tested backoff). If
  your operonx code added its own transport-retry loop on top, remove
  it and rely on the SDK's `num_retries` / equivalent.

## What did NOT change

- `PARENT.declare()`, `EmitOp`, `InterruptOp`, `Checkpointer`,
  `engine.stream(mode=)`, `@graph`, `@op`, `if_/else_`, all state /
  ref / cell APIs — unchanged.
- Per-iteration ctx (`{full_name}#{n}` for synthetic loops, or the
  classic `loop_N` for other paths) — unchanged; `state[op, var, ctx]`
  still works the same way.
- Checkpointer + observability wiring — unchanged, and now catches
  writes from ops inside a synthetic loop too (BUG 7 hardening from
  the Phase 3 review).

## If you get stuck

- Read the updated [Loops and Branches guide](docs/guide/03-loops-and-branches.md)
- The [Agents guide](docs/guide/05-agents.md) has a full react-agent example
  in the new syntax.
- Every removal ships with a comment at the old code site pointing to
  the replacement — search for `1.0.0` in a stack trace or grep for
  the removed API name.
