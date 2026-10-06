# Migrating operonx

- [To 1.17.0](#migrating-to-operonx-1170) — one `Job`: `items=` replaces sources, results
  are kept, `reduce=` and `steps=` replace sinks and `Runbook`; jobs are declared in Python only
- [To 1.16.0](#migrating-to-operonx-1160) — the built-in `operonx.agents` is removed
  (`operonx.agents` now names the operonx-agents package); variants bind a
  `@graph`'s parameters, graph factories are refused
- [`operonx.agents` → `operonx-agents`](#migrating-from-operonxagents-to-operonx-agents) —
  the name map from the old built-in agents API
- [To 1.2.0](#migrating-to-operonx-120) — `OnnxOp` and `TritonOp` removed;
  `operonx.tools` → `operonx.cli`
- [To 1.0.0](#migrating-to-operonx-100) — `PARENT.shared`, `GraphOp.loop`,
  `@graph(until=)`, `ParserOp`, `ask()` removed

---

# Migrating to operonx 1.17.0

`Runbook`, every source and sink class, stream jobs and `[[job]]` blocks are gone. There is one
`Job`; what the removed pieces did is now an argument of it.

| Before | After |
|---|---|
| `Job(source="calls.jsonl")` | `Job(items="calls.jsonl")` |
| `source=[...]`, `source=gen_fn`, `PythonSource(...)` | `items=[...]`, `items=gen_fn` (called on every run) |
| `CsvSource`, `DirSource`, `"source:x"` resources | a generator function: `items=read_csv` |
| `sink="out.jsonl"`, `PythonSink(fn)` | `output="out.jsonl"`, `output=fn(key, result)` — or just read `run.results` |
| `ListSink(lst)` | `run.results` (`{key: result}`, kept in the record's `results.jsonl`) |
| `DirSink`, `NullSink`, `"sink:x"` resources | `output=fn` writing where you want; nothing, for `NullSink` |
| `item_input="val"` | `input="val"` |
| `on_error="retry:3"` | `retry=Retry(max_attempts=4)` (`from operonx import Retry`) |
| `on_error="record"` | `on_error="skip"` (an `Eval` never fails its run on an item) |
| `item_timeout=30` | `timeout=30` |
| `session="stream"` | a graph that takes the whole list: `Job(items=None, inputs={"rows": rows})`, or `reduce=` |
| a second job reading the first one's sink to summarise | `reduce=summary_graph` on the first job (`run.reduced`) |
| `Runbook("n", a >> b >> c)` / `Sequential(a, b, c)` | `Job("n", steps=[a, b, c])` |
| `a >> [b, c]` (parallel) | `steps=[a, b, c]`, or two jobs run by the same cron line |
| `schedule="0 3 * * *"` | cron calls `operonx run <name>`; the schedule is not declared |
| `[[job]]` in `operonx.toml` (and TOML evals) | `Application(jobs=[Job(...), Eval(...)])` in `app/main.py` |
| `Eval.from_spec`, `OnlineEval.from_spec`, `Gate.from_options` | construct `Eval(...)`, `Gate(...)` directly |
| `operonx run x --source a.jsonl --sink b.jsonl` | `operonx run x --items a.jsonl` |
| `from operonx.core.jobs import ...` | `from operonx.app.jobs import ...` |

```python
# Before
nightly = Runbook("nightly", score >> summarise)
score = Job("score", graph=score_call, source="calls.jsonl", sink="out/scores.jsonl",
            key="call_id", on_error="retry:2", item_timeout=30)
summarise = Job("summarise", graph=summary_flow, source="out/scores.jsonl", session="stream")

# After
score = Job("score", graph=score_call, items="calls.jsonl", key="call_id",
            retry=Retry(max_attempts=3), timeout=30, reduce=summary)  # summary(results)
run = score.run_sync()
run.results, run.reduced
```

A graph without doors is now bound by name: a dict item fills the graph's parameters (an unknown
field is an error), a non-dict item goes to the only free parameter. Records default to
`.operonx/jobs/<job>/` at the project root (`[jobs] dir` in `operonx.toml` moves them); evals go
to `.operonx/evals/`. `record_dir=` and `on_item=` are unchanged.

---

# Migrating to operonx 1.16.0

- **The built-in `operonx.agents` is gone.** `operonx.agents` is now an alias of the separately
  installed operonx-agents package (`pip install operonx-agents`): `from operonx.agents import
  Agent, Runner, tool` is `from operonx_agents import ...`, the same modules. Code that used the
  old API (`build_react_agent`, `operonx.agents.mcp`, `.memory`, `.policy`) moves to the new one —
  the map is below. Likewise `operonx.kb` is operonx-kb (`operonx_kb`).
- **Variants bind a `@graph`'s parameters.** `Service(graph=build, variants={...})` with `build` a
  plain function returning a graph is refused. Define the graph at module level with the
  per-variant parts as parameters:

```python
# Before
def build(style, sign_off):
    @graph
    def greet(): ...
    return greet

# After
@graph
def greet(style, sign_off): ...

Service("greet", http(...), graph=greet, variants={"formal": {"style": formal, "sign_off": "Regards"}})
```

- **New:** a provider op's `resource=` may be a graph input (`LLMOp.of(resource=model, ...)` in
  `@graph def chat(model, ...)`), so a graph that serves several models or stores no longer needs
  to be generated per resource.

---

# Migrating from `operonx.agents` to `operonx-agents`

Agents moved out of operonx into their own distribution, `operonx-agents`
(imported as `operonx.agents` since 1.16, or `operonx_agents`; design:
`docs/roadmap/track3_agents.md` §4, plan: `docs/AGENTS_V2_PLAN.md`). The old
built-in module was removed in 1.16.

The new package runs the agent loop as plain async code inside one op —
not as a back-edge graph — and records every turn, model call and tool
call as a child execution, so the trace reads `agent → turn[i] →
model, <tool>`. Approvals are data a run stops on (`status="interrupted"`)
and `Runner.resume` answers them in any process, after a restart.

```python
# Before
from operonx.agents import agent_result, build_react_agent, get_tool_definitions, tool
from operonx.agents.ops.model_ops import make_llm_caller

@tool(name="add", description="Add two numbers.", readonly=True,
      schema={"type": "object", "properties": {"a": {"type": "number"},
              "b": {"type": "number"}}, "required": ["a", "b"]})
def add(a: float, b: float) -> dict:
    return {"sum": a + b}

agent = build_react_agent(call_model=make_llm_caller("assistant", tools=get_tool_definitions()),
                          max_turns=8)(messages=None)
out = await Operon(agent).run(inputs={"messages": [{"role": "user", "content": "2 + 3?"}]})
answer = agent_result(out, agent)["final"]

# After
from operonx_agents import Agent, Model, Runner, UsageLimits, tool

@tool(readonly=True)
def add(a: float, b: float) -> dict:
    """Add two numbers."""          # the schema comes from the signature
    return {"sum": a + b}

agent = Agent(name="calc", model=Model("assistant"), tools=[add],
              limits=UsageLimits(turns=8))
res = await Runner.run(agent, "2 + 3?")
answer = res.output                 # res.status: completed | limit | interrupted | blocked | failed
```

| `operonx.agents` | `operonx_agents` |
|---|---|
| `@tool(name=, description=, schema=)`, `TOOL_REGISTRY`, `get_tool_definitions` | `@tool` (schema and validation from the signature and docstring); each `Agent` owns its tools — there is no registry |
| `build_react_agent(call_model=...)`, `agent_result` | `Agent(...)` + `Runner.run` / `Runner.stream`; in a graph, `AgentOp.of(agent=..., input=...)` |
| `make_llm_caller("x", tools=...)` | `Model("x")` (fallback, a deadline over the chain, normalised `Usage`) |
| `build_dispatch` | `operonx_agents.dispatch` (one tool message per call) |
| `ToolPolicy` | `ToolPolicy` (same rules) |
| `destructive=True` + `InterruptOp`, `AgentSession.send(on_approval=)` | `@tool(approval=...)` → `res.status == "interrupted"`; `Runner.resume(agent, res.run_id, store=..., approvals={i.id: Approve()})` |
| `AgentSession` (history) | `Runner.run(..., session=InMemorySession() / RedisSession / SQLiteSession)` |
| `plan_compaction`, `apply_compaction`, `count_tokens` | `Agent(context=ContextPolicy(...))`: compaction triggered by real usage, the summary persisted |
| `assemble_api_messages`, `apply_cache_control`, `build_system_prompt` | inside the runner (a byte-stable system prefix, cache breakpoints) |
| `make_delegate_tool`, `describe_delegation` | `agent.as_tool(name=...)` (its approvals surface on the parent) |
| `Redactor` | `Redactor`; `Agent(redact=...)` scrubs exported traces by default |
| `MCPServer`, `connect_mcp`, `register_mcp_tools` | `MCPServer`, `await MCPToolset.connect(server, allow=[...])`, in `Agent(tools=[...])` |
| `Heartbeat` | the serve layer's `schedule` trigger |
| `MemoryProvider`, `LocalMarkdownMemory`, skills | not ported yet (track3 §4.6 "Later") |
| an agent behind HTTP: `ingress → build_react_agent → egress` | `agent_service(agent, http("POST", "/ask"), store=...)`: JSON or server-sent events, approvals on `POST /ask/resume`; or a websocket |

`operonx-agents` is not on PyPI yet; until it is, install it from its
repository.

---

# Migrating to operonx 1.2.0

1.2.0 removes the two **backend-named** ops. Both named their *transport*
rather than a semantic, so the op name told you the runtime instead of
the intent and every backend needed its own op. It also renames the CLI
package (§4) — a one-line import change, and only if you imported it.
Everything else in 1.1.x is unaffected.

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

## 4. `operonx.tools` → `operonx.cli`

**The `operonx-pack` command is unchanged.** If you only ever run it from
a shell or CI, there is nothing to do.

Only the import path moved:

```python
# Before
from operonx.tools.pack import pack_one

# After
from operonx.cli.pack import pack_one
```

There is **no compatibility shim**. Leaving one would keep the `tools`
name occupied, and freeing it is the entire reason for the move —
`operonx.agents` needs `tools` to mean *agent tools* (the callables an
LLM invokes), not *command-line tools*. Two meanings for one name in one
namespace is a permanent tax.

### The dead `operonx` command

`pyproject.toml` declared a second console script,
`operonx = "operonx.cli:main"`, from the April 2026 Hush→Operon migration
through 1.1.0. It pointed at a project-scaffolding CLI that the same
migration deleted, so `operonx --help` raised `ModuleNotFoundError` in
every published release. It is removed in 1.2.0.

If you have a script or Dockerfile invoking `operonx …`, it was already
failing — there is no replacement, because there was never a working
command. `operonx-pack` is the only console script operonx ships.

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
