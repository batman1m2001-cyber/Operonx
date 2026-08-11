# Operonx → Agent Framework — Extension Plan (v2, post-1.0.0)

**Status:** rewrite of the pre-1.0.0 draft (preserved as
`AGENT_EXTENSION_PLAN.md.v1.bak`). This version composes on top of the
primitives that shipped in operonx 1.0.0 (2026-08-10): back-edge loops,
`PARENT.declare(reducers={...})`, `Checkpointer`, `InterruptOp`,
`engine.stream(mode=…)`, `LLMOp.of(fields=…, max_retries=…)`, filtered
observability via `@op(exclude=…, include=…, observe_max=…)`, and the
`operonx.reducers` module (`add_messages`, `dict_merge`).

**TL;DR** — 1.0.0 turned operonx into a real agent substrate. What v1 had
to build defensively (message accumulation, HITL branches, loop scaffolding,
retry supervisors, tracing filters) now ships in-framework. This plan adds
**one small package (`operonx/agents/`)**: a `@tool` decorator, a per-call
dispatch subgraph, a ReAct-loop factory, a sub-agent factory, and a handful
of pure-Python helpers. **~13 files, most under 200 LOC**, and the reference
harness (`operonx-code`) built on top. Nothing invents a "framework layer" —
we compose primitives already blessed by 1.0.0.

---

## 1 · Why now (what 1.0.0 changed)

The pre-1.0.0 plan had to work around six missing primitives. All six shipped.
The table below is the load-bearing delta — every row erased a v1 workaround.

| Concern | v1 workaround | 1.0.0 primitive | Effect on this plan |
|---|---|---|---|
| Message accumulation across turns | Custom `append_messages` op with hand-written LangGraph-style id-upsert | `PARENT.declare(messages=[], reducers={"messages": add_messages})` | Delete custom merger. Turn output writes with `>> PARENT["messages"]`; framework merges. |
| Loop control | `with GraphOp.loop(name=…, until="expr", **initial_state) as loop:` | Back-edge inside `@graph` → Phase 3 synthesizes hidden `_GraphLoop`. `strict_dag=True` opts out. | ReAct loop is a plain `@graph` with `if_(done, END).else_(llm)`. No imperative scaffolding. |
| HITL / permission on destructive tools | Runtime `if_` branch reading a mode flag; hoped a human polled a queue somewhere | `InterruptOp(payload=…, timeout=…)` — emits `InterruptEvent` on state's bus, awaits `state._interrupt_responses[id]`. ~~`engine.stream()` auto-subscribes a listener; `handle.resume(id, value)` resolves.~~ 🔴 **that wiring does not exist — §15.1 V3** | `permission_gate` becomes a `Wait(InterruptOp) → if_ approve.else_ block` pattern. Real preempt, real resume. |
| Structured LLM output + retry | Separate ParserOp + custom retry loop in `ask()`; hallucinated a fallback trigger on parse errors | `LLMOp.of(fields=…, parser=…, validators=…, max_retries=…, retry_hint=True)` — inline parse + validate + Instructor-style error-guided retry on the same resource. Fallback narrowed to structural refusals only. | Router/classifier ops disappear into LLMOp calls with `fields=`. Retry taxonomy is honest: parse/validate → `max_retries`, refusal → `fallback`, transport → SDK. |
| Cross-turn state visibility / replay | Ad-hoc SCRATCH scraping | `Checkpointer` protocol + `InMemoryCheckpointer` — per-step delta store; `get_state(step)`, `get_updates(step)`, `list_steps()`. Zero overhead when unbound. | Sessions become "bind a checkpointer, replay the graph." Nothing custom in the agent layer. |
| Progress streaming to consumer | Peek into `ExecutionHandle._queue` | `engine.stream(mode="custom", channels=[…])` + `EmitOp` — fire-and-forget custom events with channel filtering. `mode="updates"` for per-op deltas; `mode="values"` for full-state snapshots (needs checkpointer). | Progress events are `emit(channel="tool_start", …)` — no framework changes. |
| Observability shaping | Hand-filtered per-op | `@op(exclude=…, include=…, observe_max=…)` — polymorphic list-or-dict; `ObserveBudgetExceeded` circuit-breaks runaway generators. | Prompt-cache defense, chunk-heavy streams, and tool-output truncation all shape at emission source. No tracer-side filter code. |

Two other cleanups that ripple through the plan:

- **`PARENT.shared(**vars)` was renamed to `PARENT.declare(**vars, reducers={…})`.** All the v1 shared-state examples get one line simpler and gain a merge policy.
- **`ask()` was removed.** `LLMOp.of(fields=…)` is the one canonical LLM shorthand. Nothing to teach twice.

**Net effect on scope:** v1 estimated 3–4 weeks compressed to 2–3. This
revision compresses further, to **~2 weeks for P0–P3**, because five of the
seven load-bearing scaffolds (message merge, loop, HITL, retry, streaming)
are framework code we no longer write.

---

## 2 · What operonx 1.0.0 primitives you compose on

These are the substrate. Every op-native construct in this plan resolves
back to one of these.

> **Read §15.2 before depending on a row.** This table was written from
> docstrings and design docs, not from running code. Two rows have been
> probed since; one was wrong. §15.2 tracks per-row verification status —
> probe the row when the phase that needs it starts, not all of them now.

| Concern | Primitive | Evidence |
|---|---|---|
| Turn loop | Back-edge inside `@graph` — Phase 3 build-time rewrite synthesizes `_GraphLoop`; `max_iterations=1000` default cap; `strict_dag=True` opts out | `graph_op.py` · `cycle_rewrite.py` · [docs/design/STATE_LOOP_REFACTOR_PLAN.md](docs/design/STATE_LOOP_REFACTOR_PLAN.md) |
| LLM streaming | `LLMOp.of(stream=True, …)` yields per token chunk; frames forwarded to `ExecutionHandle._queue` | `providers/ops/llm.py:_generate_core` · `engine.py:start` |
| Structured LLM output | `LLMOp.of(fields=[…], parser="json"\|"xml"\|"yaml", validators={…}, max_retries=N, retry_hint=True)` — parse + validate + semantic retry on same resource | `providers/ops/llm.py:_structured_generate` |
| Refusal vs parse failure | Structurally-detected `LLMRefusalError` (finish_reason ∈ {content_filter, safety} or non-empty `extras.refusal`) triggers `fallback=`; parse/validator failures use `max_retries` on the primary | `providers/ops/llm.py:_is_refusal` · MIGRATION.md §Runtime |
| Sub-agent isolation | Nested `@graph` — child ops live at deeper `ctx` tuple; parent refs hermetic (validated at build); nested trace spans auto-nest | `graph_op.py` · [docs/architecture/state-model.md](docs/architecture/state-model.md) |
| Shared cells + reducers | `PARENT.declare(**vars, reducers={var: fn(old,new)→merged})` — cell semantics + optional fan-in merge | `_edges.py:PARENTAccessor.declare` |
| Message accumulation | `operonx.reducers.add_messages` — LangGraph-compatible id-upsert + `RemoveMessage` / `REMOVE_ALL_MESSAGES` sentinels | `operonx/reducers.py` |
| Tool fan-out | Generator op yielding per tool call + `Ref.parallel(max=N)` on consumer | `ref.py:parallel` · `task_scheduler.py` |
| Ordered gather | `Ref.collect()` — buffered, flushed at EOF in yield-index order | `task_scheduler.py:_on_eof` · `ref.py:collect` |
| Preemptive cancel | `yield Interrupt(ctx_to_cancel=…)` from any op — scheduler drains pumps at that ctx prefix | `_events.py` · `task_scheduler.py` |
| **HITL suspend/resume** | **`InterruptOp(payload=…, timeout=…)` — emits `InterruptEvent`, awaits `state._interrupt_responses[id]`; outputs `response`, `timed_out`, `interrupt_id`.** 🔴 The op works, but the **engine is not wired to the bus** — see §15.1 V3 for the path that does work | `core/ops/flow/interrupt_op.py` |
| **Cross-run persistence** | **`Checkpointer` protocol + `InMemoryCheckpointer` (Phase 2). Bind at `Operon(g, checkpointer=…)`. `handle.get_state(step)`, `handle.list_steps()`. Zero overhead when unbound** | `checkpoint/base.py` · `checkpoint/memory.py` |
| **Custom progress events** | **`EmitOp(channel=…, payload=…)` + `engine.stream(mode="custom", channels=[…])` — fire-and-forget filterable event bus** | `core/ops/flow/emit_op.py` · `engine.py:stream` |
| **Observability shaping** | **`@op(exclude=[…], include=[…], observe_max=N)` — polymorphic filter at emission source; `ObserveBudgetExceeded` on runaway generators** | `core/ops/base.py` |
| Async I/O dispatch | `@op(bound="io"\|"cpu"\|"sync")` — auto thread-pool routing | `core/ops/base.py` |
| Config + secrets | `ResourceHub` — singleton, lazy, `resources.yaml`, `${VAR}` interpolation, 5-branch diagnostic errors | `core/registry/resource_hub.py` |
| Per-run scratchpad | `SCRATCH[key]` — free-form dict on `MemoryState._scratch`; mutations flow through the observer bus (Phase 2b3 B1) | `core/ops/_edges.py:ScratchAccessor` |

**You will build the agent by writing `@op`s and `@graph`s that plug into
this substrate. You will not re-implement any of the above.**

---

## 3 · The mental-model shift (unchanged from v1, sharper now)

Every "class" a hermes-style agent framework would demand either
**dissolves into an operonx primitive** or **shrinks to a thin
pure-Python helper**. 1.0.0 dissolves five more that v1 kept as ops.

| A hermes-style class | Op-native form | Notes |
|---|---|---|
| `TurnController` | Back-edge inside `@graph` — Phase 3 rewrite | v1 had `GraphOp.loop`; 1.0.0 collapses to DAG-native. |
| `LLMClient` | `LLMOp.of` | Simple + structured mode in one class. |
| `ToolDispatcher` | Subgraph (§7.2) | 5 ops + 2 branches per call. |
| `SubAgent` | Nested `@graph` (§7.4) | Free ctx isolation + trace nesting. |
| `Permission` engine | **`InterruptOp` → `if_(response.approved).else_(blocked)`** | v1 had a runtime branch on a stashed mode flag; 1.0.0 gives real HITL. |
| `MessageStore` / accumulator | **`PARENT.declare(messages=[], reducers={"messages": add_messages})`** | v1 wrote a custom merger op; framework ships one. |
| `SessionStore` | **`Checkpointer` binding** | v1 was a resource-registered SQLite class; 1.0.0 gives replay for free. |
| `ProgressStream` / eventer | **`EmitOp` + `engine.stream(mode="custom")`** | v1 sniffed the frame queue; 1.0.0 has a bus. |
| `RetrySupervisor` (parse errors) | **`LLMOp.of(max_retries=N, retry_hint=True)`** | v1 was a ~40-LOC ask() loop; 1.0.0 has Instructor-style semantic retry inline. |
| `Tracer` filter | **`@op(exclude=…, include=…, observe_max=…)`** | v1 filtered downstream; 1.0.0 filters at emission source. |
| `PromptAssembler` | `build_system_prompt` op | Pure fn wrapped in an op. |
| `Compactor` (algo) | `compact` subgraph + `if_` gate | Data-flow rewrite of messages. |
| `ToolRegistry` | Python dict `{name: op_factory}` | Built at import time. |
| `ErrorClassifier` | Pure function `(exc) → ErrorKind` + `if_` | No I/O, no state. |
| `MemoryProvider` ABC + backends | Classes in `ResourceHub`; methods wrapped as ops | Same. |
| `SkillLoader` (YAML parse) | Pure function at agent init | One-shot. |
| `Agent` composition root | ~30-LOC factory building the top-level `@graph` | Not a class. |

**Net effect:** the "12-module decomposition" from v1 collapses further. Ten
of the boxes above are now zero-LOC on our side — 1.0.0 handles them. What
remains is ~13 small files, most under 200 LOC.

---

## 4 · Module layout

```
operonx/agents/                    (NEW · blessed primitives · in-tree)
├── __init__.py                    # public surface: Tool, TOOL_REGISTRY, build_react_agent, subagent
├── CONTRIBUTING.md                # Footprint Ladder governance
│
├── tool.py                        # @tool decorator, TOOL_REGISTRY dict
├── errors.py                      # ClassifiedError + pure classify()
├── memory.py                      # MemoryProvider ABC + LocalMarkdownMemory
│
├── ops/
│   ├── memory_ops.py              # memory_prefetch (generator+fan-out), memory_sync, memory_write
│   ├── permission_ops.py          # request_approval (InterruptOp wrapper); permission_check (policy)
│   ├── compact_ops.py             # count_tokens, compact_messages
│   ├── prompt_ops.py              # build_system_prompt, apply_cache_control, assemble_api_messages
│   ├── skill_ops.py               # inject_skills_as_user_msg
│   └── progress_ops.py            # emit_progress helpers (thin EmitOp wrappers with typed payloads)
│
├── graphs/
│   ├── dispatch.py                # per-tool subgraph + all-tools fan-out
│   ├── react.py                   # ReAct back-edge loop factory
│   └── subagent.py                # sub-agent nested-@graph factory
│
└── skills/
    └── loader.py                  # SKILL.md YAML frontmatter parser
```

Also in tree:

```
operonx/cli/                        (renamed from operonx/tools/ — namespace fix)
```

Rename is still valid — `operonx/tools/` currently holds `operonx-pack`
(a Rust-spec serializer CLI). Freeing the `tools` name for agent tooling
avoids a permanent semantic clash.

**Done (P0, 1.2.0).** No back-compat shim: a shim would keep `tools`
occupied, which is the whole point of the move. The `operonx-pack`
console script is unchanged; only `from operonx.tools.pack import …`
breaks, and `scripts/regen_fixture.py` was its one in-repo consumer.

The rename surfaced a dead `operonx = "operonx.cli:main"` script entry,
carried since the April 2026 Hush→Operon migration and pointing at a
scaffolding CLI that the same migration deleted — `operonx --help` had
raised `ModuleNotFoundError` in every published release. **Removed
rather than implemented.** Operonx is a library; an umbrella dispatcher
with nothing to dispatch to fails criterion 3 of the op-worthy bar
(zero concrete demand) that this plan applies to everything else.
`tests/internal/cli/test_entry_points.py` now imports every declared
`[project.scripts]` target the way the generated wrapper does, so the
next dangling entry fails in CI instead of on a user's first install.

Out of tree (sibling PyPI package, iterates independently):

```
operonx-code/                       # reference coding-agent harness
```

---

## 5 · The agent as a graph — end-to-end shape

```mermaid
flowchart TD
    START --> LOAD[load_session<br/>reads Checkpointer if present]
    LOAD --> BUILD[build_system_prompt<br/>date-only · cache-safe · once per session]
    BUILD --> LOOP[/back-edge loop body/]

    subgraph LOOP_BODY [" "]
        PREFETCH[memory_prefetch<br/>generator+fan-out<br/>bounded 8s]
        PREFETCH --> ASSEMBLE[assemble_api_messages<br/>+ api_content sidecar<br/>+ apply_cache_control LAST]
        ASSEMBLE --> COUNT[count_tokens]
        COUNT --> GATE1{if_ tokens ≥ 75%}
        GATE1 -->|yes| COMPACT[compact subgraph]
        GATE1 -->|no| LLM
        COMPACT --> LLM[LLMOp.of stream=True]
        LLM --> ROUTER{if_ finish_reason == tool_calls}
        ROUTER -->|no| DONE[mark_done]
        ROUTER -->|yes| DISPATCH[dispatch_all_tools<br/>generator+fan-out]
        DISPATCH --> SYNC[memory_sync]
    end

    LOOP_BODY --> BACK{{"if_ done, END<br/>else back to PREFETCH"}}
    BACK -->|done| END
    BACK -->|not done| PREFETCH
```

Every box is either an `@op` we write (~10–100 LOC each) or an existing
operonx op (`LLMOp`, `if_`, `EmitOp`, `InterruptOp`). No god-class. The
loop back-edge `BACK -->|not done| PREFETCH` is what the Phase 3 rewrite
turns into a synthesized `_GraphLoop` at build time.

---

## 6 · Design rules

### Rule 1 — Yield + fan-out beats imperative iteration

From operonx's own history: the classic `ForOp` / `MapOp` / `WhileOp`
classes were replaced by `yield` + downstream fan-out because it collapses
`for`, `map`, `while`, and *streaming* into a single primitive. **Never
write a `for` loop inside an op if you can yield instead.** A generator op
+ `Ref.parallel(max=N)` downstream gives you per-item concurrency, per-item
trace spans, per-item ctx isolation, and streaming-to-caller — all four for
free.

**Before** (imperative, hides parallelism, breaks streaming):

```python
@op
async def prefetch_all(query, providers):
    results = []
    for p in providers:
        results.append(await p.prefetch(query))   # sequential I/O
    return {"contexts": results}                  # batched result
```

**After** (yields, downstream fans out, streams naturally):

```python
@op
def each_provider(providers: list):
    for p in providers:
        yield {"provider": p}

@op
async def one_prefetch(query: str, provider):
    return {"context": await provider.prefetch(query)}

# In the graph:
gen    = each_provider(providers=PARENT["memory_providers"])
one    = one_prefetch(query=PARENT["query"], provider=gen["provider"].parallel(max=4))
merged = merge_contexts(items=one["context"].collect())   # ordered gather at EOF
```

Apply everywhere iteration is *independent* — tool dispatch (§7.2), memory
prefetch (§7.5), skill matching (§7.5), sub-agent fan-out (§7.5), LLM token
consumers (§7.5). The one place iteration is *dependent* is the outer ReAct
loop (turn N+1's LLM input depends on turn N's tool results) — that's what
back-edges are for.

### Rule 2 — Loops go through back-edges, never imperative wrappers

The v1 plan hedged that `GraphOp.loop` was the framework's "one imperative
primitive." 1.0.0 fixed that. **Every loop in the agent — outer ReAct,
inner retry, sub-agent turns — is a back-edge inside a `@graph`.** The
Phase 3 rewrite compiles it into a hidden `_GraphLoop` at build time; you
never touch the loop wrapper.

### Rule 3 — Reducers own accumulation, not custom ops

If a cell accumulates across turns (messages, cost, tool-call log), declare
it with a reducer:

```python
import operator
from operonx.reducers import add_messages, dict_merge

PARENT.declare(
    messages=[],
    cost_usd=0.0,
    tool_stats={},
    reducers={
        "messages": add_messages,      # LangGraph id-upsert + RemoveMessage sentinels
        "cost_usd": operator.add,
        "tool_stats": dict_merge,
    },
)
```

No `append_messages` op. No manual merge. Each turn's op writes with
`>> PARENT["messages"]`; the framework merges under a bounded lock.

### Rule 4 — Retry taxonomy is honest

Three failure modes, three primitives. Don't cross the streams.

| Failure mode | Symptom | Handler | Where |
|---|---|---|---|
| Transport | 429, 5xx, connection reset, timeout | SDK's own retry (litellm / openai / anthropic) | Under LLMOp, invisible |
| Parse / validate | JSON malformed, field missing, validator rejects | `LLMOp.of(max_retries=N, retry_hint=True)` — retries on **same resource** with the error injected into the next prompt | LLMOp inner loop |
| Refusal / content-filter | `finish_reason ∈ {content_filter, safety}` or non-empty `extras.refusal` | `LLMOp.of(fallback=[…])` — tries next model | LLMOp fallback chain |
| Semantic ("bad" answer that parsed fine) | Answer is correctly formed but wrong for the task | Agent-level: another loop iteration or self-critique | ReAct loop body |

**No transport-retry knob on LLMOp** — the SDK is battle-tested. A wrapping
retry would just double the exponential backoff.

---

## 7 · Core sketches

### 7.1 · `@tool` — a tool IS an `@op` with metadata

```python
# operonx/agents/tool.py
from operonx import op

TOOL_REGISTRY: dict[str, callable] = {}

def tool(
    *, name, description, schema,
    readonly=False, concurrency_safe=False, destructive=False,
    check_fn=None, max_result_chars=100_000, dynamic_schema_overrides=None,
):
    """Register a function as both an @op and an LLM-callable tool.

    The op_factory returned is the *same object* stored in TOOL_REGISTRY —
    dispatch calls `.core(**args)` to reuse the op's own execution path
    (free tracing, timing, cancellation, bound routing).
    """
    def wrap(fn):
        fn._tool_meta = dict(
            name=name, description=description, schema=schema,
            readonly=readonly, concurrency_safe=concurrency_safe,
            destructive=destructive, check_fn=check_fn,
            max_result_chars=max_result_chars,
            dynamic_schema_overrides=dynamic_schema_overrides,
        )
        op_factory = op(fn)                # reuse operonx @op
        TOOL_REGISTRY[name] = op_factory
        return op_factory
    return wrap

@tool(
    name="edit",
    description="str_replace edit",
    schema={...},
    destructive=True,                     # → triggers HITL approval in dispatch
)
async def edit_tool(path: str, old: str, new: str):
    ...
    return {"result": diff}
```

**Two schemas per tool are unavoidable:**

- operonx `Param` — signature-parsed, drives `>>` wiring at build time
- LLM JSON Schema — hand-authored, drives the model's `tools=[…]` payload

Keep them side-by-side per tool. Wrap common patterns (`ExtractField`-style
mini-DSL for the JSON Schema) in helpers if the pain grows.

### 7.2 · Tool dispatch — shipped shape

> **Built.** `operonx/agents/graphs/dispatch.py`. The sketch this replaces
> had three defects (§15.1 V1, V2, V5); what follows is what runs.

```
parse ─▶ route ─▶ approve (InterruptOp) ─▶ normalize ─┐
             └──▶ auto_ok ───────────────────────────┴─▶ execute
```

Four ops and one branch per call — fewer than the sketch, because the two
approval arms converge on a single `execute` through `PARENT["decision"]`
instead of each carrying its own copy of the execution path.

**Every call returns exactly one tool message.** Providers reject a
conversation in which an assistant `tool_call` has no matching result, so
unknown tool, unparseable arguments, an exception inside the tool, a
timeout and a human denial all come back as messages the model can act
on. `execute` catches everything *itself* — operonx records op errors
into state and returns a partial result rather than raising, so an
uncaught error would emit no message at all and surface a turn later as
a provider 400.

**`parse_call` assembles the approval payload** and hands `InterruptOp` a
single bare ref. A Ref nested in a dict is rejected at construction now;
before that check existed, the human approving a destructive call was
shown `Ref` objects instead of the tool name and arguments.

**`normalize_decision` keeps expiry distinct from refusal.** `InterruptOp`
reports a timeout on a separate output; writing only `response` made "no
one answered" indistinguishable from "a human said no", and the model was
told it had been declined.

`execute`'s parameter is `tool_name`, not `name` — `name` is in
`_BASE_INIT_KEYS` and operonx warns it may be read as an op constructor
argument rather than an input.

### 7.3 · ReAct loop — shipped shape

> **Built.** `operonx/agents/graphs/react.py`. The v2 sketch did not
> compile: `@graph(max_iterations=…)` does not exist. Running it produced
> §15.1 V7–V10.

```
START ─▶ seed ─▶ count_turn ─▶ call_model ─▶ decide ─┬─▶ END
                     ▲                                │
                     └── gather ◀── dispatch ◀────────┘
```

`call_model` is **injected**, not constructed here — the loop is testable
without a provider and callers choose their own `LLMOp.of(...)`.

**The turn budget lives in the agent layer, not the graph.** A
graph-level cap cuts mid-flight, tells the model nothing, and leaves
partial state. `count_turn` injects a notice at the limit and lets the
model take one final turn, so exhaustion exits the way success does.
Keeping it out of `call_model` matters too: a budget the caller could
forget to implement is not a budget.

**`decide` folds the three exit conditions** — model finished, budget
spent, no tools requested — into one boolean, so the branch stays a
Ref-vs-literal comparison, the only form `if_` evaluates correctly.

**There is no terminal `finish` op.** An op wired after a loop that
contains a generator never becomes ready and is skipped silently (V9), so
`agent_result(result, agent)` reads the merged cells directly. That is
also why it takes the graph.

**`gather_tool_messages` exists because `collect()` hands over a single
message dict per call** while `add_messages` requires lists on both
sides. Writing raw frames raised inside the reducer, and since operonx
records op errors rather than propagating them, the run ended quietly
with a partial conversation.

### 7.4 · Sub-agent = nested `@graph`

```python
# operonx/agents/graphs/subagent.py
from operonx import op, graph, PARENT, START, END
from operonx.reducers import add_messages
import operator

DELEGATE_BLOCKED_TOOLS = frozenset(["delegate", "memory", "clarify", "send_message"])

@graph
def subagent(task: str, *, parent_tools: dict, max_iterations: int = 10):
    """Nested agent — its own loop, own state, restricted toolset.

    - Nested ctx tuple auto-isolates state.
    - Nested @graph auto-nests trace spans (V3 tracing).
    - Cost + final message bubble up via explicit `>> PARENT[…]` writes.
    - No sub-sub-delegation by default (blocklist enforced at construction).
    - Cancellation propagates automatically: parent `yield Interrupt(ctx_to_cancel=…)`
      drains all child ops at once.
    """
    child_tools = {n: t for n, t in parent_tools.items()
                   if n not in DELEGATE_BLOCKED_TOOLS}

    # Sub-agent's own accumulator — reducers apply within this nested scope.
    PARENT.declare(
        cost_usd=0.0,
        final_message="",
        reducers={"cost_usd": operator.add},
    )

    react = build_react_agent(
        model="claude-haiku-4-5",           # cheaper for sub-tasks
        tool_schemas=[t._tool_meta["schema"] for t in child_tools.values()],
        max_iterations=max_iterations,
    )()

    # Inject the parent task as the initial user message.
    react["messages"] >> PARENT["messages"]      # simplified; wrap task in a user-role msg

    react["cost_usd"]      >> PARENT["cost_usd"]
    react["final_message"] >> PARENT["final_message"]
    START >> react >> END
```

No HMAC capability tokens at v1 — enforce tool-subset at construction site.
If we ship a plugin surface in v2, add the HMAC layer then.

### 7.5 · Where the yield + fan-out pattern reappears

Four places where an imperative op would be a mistake. Same pattern, same
free wins (per-item concurrency, spans, ctx, streaming).

**Memory prefetch across N providers** — bounded 8s deadline as `.parallel(max=N, timeout=8.0)`:

```python
@op
def each_provider(providers: list):
    for p in providers:
        yield {"provider": p}

gen = each_provider(providers=PARENT["memory_providers"])
one = provider_prefetch(query=PARENT["query"], provider=gen["provider"].parallel(max=4))
ctx = merge_contexts(items=one["context"].collect())   # <memory-context>…</memory-context>
```

**Skill matching + injection** — each matching skill yields; downstream renders in parallel; ordered `collect()` concatenates:

```python
@op
def each_matching_skill(query: str, skills: list):
    for s in skills:
        if s.matches(query):
            yield {"skill": s}

gen      = each_matching_skill(query=PARENT["query"], skills=PARENT["skills"])
rendered = render_skill(skill=gen["skill"].parallel(max=8))
user_msg = concat_as_user_msg(items=rendered["text"].collect())    # per hermes cache trick
```

**Sub-agent orchestration (parallel sub-tasks)** — parent yields tasks, subagent graph invoked per yield in parallel, results gather in order:

```python
@op
def each_subtask(plan: dict):
    for task in plan["subtasks"]:
        yield {"task": task}

gen  = each_subtask(plan=orchestrator["plan"])
subs = subagent(task=gen["task"].parallel(max=3), parent_tools=PARENT["tools"])
merged = merge_subagent_results(items=subs["final_message"].collect(),
                                costs=subs["cost_usd"].collect())
```

**LLM stream → multiple downstream consumers** — `LLMOp.of(stream=True)` yields per token chunk; fan-out means each consumer sees every chunk in parallel:

```python
llm       = LLMOp.of(resource="claude-sonnet", stream=True, prompt=..., tools=...)
moderator = check_content(chunk=llm["content"].parallel(max=1))     # sequential guard
display   = stream_to_stdout(chunk=llm["content"].parallel(max=1))
storage   = append_to_session(chunk=llm["content"].parallel(max=1))
assembler = accumulate_tool_calls(delta=llm["tool_calls"].collect())  # buffered until EOF
```

Every case above would have been a `for` loop + `asyncio.gather` in a
hermes-style codebase. Here it's a generator + `.parallel()` — same intent,
less to maintain, streaming for free.

---

## 8 · Load-bearing invariants

These are hard invariants stolen from hermes's ~3000 LOC of prompt-cache
defense and tool-safety logic. They now live inside specific ops, not
scattered across a god-class.

### 8.1 · Prompt cache — invariants in `prompt_ops.py`

| # | Invariant | Where enforced |
|---|-----------|----------------|
| 1 | System prompt is **date-only**, never minute-precision | `build_system_prompt` op |
| 2 | System prompt built ONCE per session, cached, replayed verbatim | `PARENT.declare(system_prompt=None)` — set once by `build_system_prompt`, read every turn |
| 3 | `api_content` sidecar = exact bytes previously sent → byte-stable retries | Stored in checkpointer state via `save_turn` op |
| 4 | Whitespace strip BEFORE `apply_cache_control` (marker rewrites str→list) | Ordered inside `assemble_api_messages` op |
| 5 | 4 breakpoints TTL-shared (5m/1h): static prefix + system tail + last 2 msgs | `apply_cache_control` helper (pure fn) |
| 6 | Plugin hooks inject into USER msg, NEVER system | `inject_skills_as_user_msg` op |
| 7 | Ephemeral system prompt APPENDED after cached string | `assemble_api_messages` op |
| 8 | OpenRouter: `role:tool` + top-level `cache_control` → silent hang | Special-case in `apply_cache_control` |

Ship a first-class metric: `cache_hit_rate = cache_read_tokens / (cache_read + cache_write)`
— thread through V3 tracing via `EmitOp(channel="cache_metrics", …)`. Hermes has the raw
sums but not the ratio; we do better.

### 8.2 · Compaction — 75% threshold, proactive + reactive, anti-thrash

Lives in `compact_ops.py` + gated by an `if_` branch in the ReAct loop.
No `Compactor` class — just:

- `count_tokens` op (pure, ~10 LOC)
- `compact_messages` op — inside it, an `LLMOp.of(fields=[…], parser="json")` summarizes, then re-injects the sentinels
- Anti-thrash state via `PARENT.declare(last_compact_iter=-999)`; branch reads iteration delta from the synthesized loop's iter counter

Sentinels stolen verbatim from hermes:

- End marker: `--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---`
- Skill re-injection: `[SKILL_PRUNED: content lost in compression; reload with skill_view(name='X')]`
- Continuation sentinel when compacted window has no user turn
- Summary prefix MUST include `"tools remain fully active — keep calling them normally"` + `"MEMORY.md is still authoritative"`

### 8.3 · Error handling — pure `errors.py` + `if_` branches

```python
# operonx/agents/errors.py
@dataclass(frozen=True)
class ClassifiedError:
    reason: str            # "timeout" | "rate_limit" | "context_overflow" | "auth" | ...
    retryable: bool
    should_compress: bool
    should_rotate_credential: bool
    should_fallback: bool

def classify(exc: Exception) -> ClassifiedError:
    """Pure function. Start with 6 reasons; add as we hit them (Footprint Ladder)."""
    ...
```

Consumed by an `error_classifier` op that reads `llm["error"]` (operonx
already captures errors into `state[op, "error", ctx]`) and routes via `if_`.
**Parse errors don't reach this classifier** — they're absorbed by
`LLMOp.of(max_retries=N)` inside the LLM call. Refusals don't reach it
either — they trigger `fallback=` structurally. What's left here is
context overflow, auth failure, rate-limit exhaustion after SDK's own
retries — genuine failure modes.

### 8.4 · HITL for destructive tools — invariants in `permission_ops.py`

New in v2 — 1.0.0 makes this a real primitive.

| # | Invariant | Where enforced |
|---|-----------|----------------|
| 1 | Every destructive tool call requires human approval by default | `dispatch_one` — `is_destructive` gate → `InterruptOp` |
| 2 | Approval decision is per-call, never batched (else one "yes" approves N deletes) | `dispatch_one` runs per call; each call gets its own InterruptOp |
| 3 | Timeout defaults to 5 minutes; on timeout the call is auto-declined | `InterruptOp(timeout=300.0)` + `timed_out` output routes to `blocked_result` |
| 4 | Rejection produces a `role:tool` message the LLM sees ("blocked: denied by human") | `blocked_result` op |
| 5 | Rejection does not tear down the turn; agent continues with the block message | Back-edge continues to next iteration; the block message accumulates via `add_messages` |
| 6 | Approval mode can be pre-authorized session-wide (`--yes-mode`) — bypasses InterruptOp via a policy op that short-circuits before dispatch | `permission_check` op reads a session flag from `PARENT["approval_mode"]` |
| 7 | Approval events are recorded in the checkpointer as `InterruptEvent`s → resume-safe | Framework — the InterruptOp fires `state._notify_interrupt(…)` |

---

## 9 · Phase roadmap

Compressed further vs v1 because five load-bearing scaffolds landed in 1.0.0.

```
Week 1   P0  P1                Tools + ReAct + HITL
Week 2       P2                Memory + compaction + cache invariants
Week 3            P3           Sub-agents + skills + policy modes
Week 4                 P4      Reference harness (operonx-code)
Later                       P5 Learning-loop pattern doc (defer)
```

| # | Phase | Deliverable | Size |
|---|-------|-------------|------|
| **0** ✅ | Namespace + governance | Rename `operonx/tools/` → `operonx/cli/`; scaffold `operonx/agents/`; write Footprint Ladder into `CONTRIBUTING.md` | 0.5d |
| **1** | Tool + ReAct + HITL | `@tool` + `TOOL_REGISTRY`; `dispatch_one`/`dispatch_all_tools` graphs (incl. destructive→InterruptOp branch); `build_react_agent` back-edge factory; `permission_check` op; rewrite `docs/guide/05-agents.md` example (should be ~25 lines) — **plus §14.3 rows 1–6**: tool-error→tool-message, unknown-tool handling, history invariant, concurrent-HITL gate, repeat cap, state injection, and the `wrap_*` closure fold (§14.2) | 5–7d |
| **2** | Context lifecycle | `MemoryProvider` ABC + `LocalMarkdownMemory`; `memory_prefetch/sync` ops (generator+fan-out); prompt-cache invariants in `prompt_ops.py`; sessions via `Checkpointer` binding (no custom SessionStore). **Compaction is a subsystem, not one op** (§14.3) — trigger policy, what to summarise, what to drop, what to keep verbatim | 5–7d |
| **3** | Safety + sub-agents + skills | Policy modes for `permission_check` (deny / ask / allow); **redaction at the same trust boundary** (§14.3); `subagent` `@graph` factory + delegate blocklist; `SkillLoader` + `inject_skills_as_user_msg` op; YAML prompt-file loader | 4–5d |
| **4** | Reference harness | Sibling `operonx-code` package — bash/read/edit/patch/glob/grep/webfetch tools, persistent shell resource, CLI entrypoint | 5–7d |
| **5** | Deferred | Learning-loop pattern doc (LLM-writes-SKILL.md fork) · MCP client · Heartbeat scheduler | — |

**Estimate**: **P0–P3 in ~3 weeks**, P4 in another week.

Revised upward from ~2 weeks after the §14 audit. The loop estimate was
right; the tool-execution and context-lifecycle estimates were not.
LangChain spends ~9,400 LOC on middleware against ~2,000 on the loop, and
this plan had the ratio inverted. We still come out far below both
references — 1.0.0 shipped the substrate (reducers, back-edges,
checkpointer, HITL, structured LLMOp) that v1 and langgraph both had to
build — but "the agent is just a loop" was the wrong lesson to draw from
that.

---

## 10 · Honest gaps (v1 gap #7 resolved; new gaps identified)

> **See also §14** — a cross-implementation audit against hermes-agent,
> langgraph and langchain (11 Aug 2026) found nine further gaps, six of
> which belong in P1. The list below predates it.

The op-native form is elegant but not lossless. Six real gaps remain.

1. **Two schemas per tool** — operonx `Param` (build-time wiring) vs LLM
   JSON Schema (runtime payload). Duplication is inherent to the two
   consumers. Keep them side-by-side per tool; wrap common patterns in
   helpers.

2. **HITL requires a caller loop** — `InterruptOp` is a real primitive,
   but the caller (CLI, HTTP server, whatever) must `async for event in
   engine.stream(mode="custom")`, filter for `InterruptEvent`, prompt the
   human, and call `handle.resume(id, {"approved": …})`. That's a
   ~30-LOC harness per surface (CLI, TUI, HTTP). Not framework work —
   integration work.

3. **`SCRATCH` reads require an `@op`** — branch conditions eval on state
   cells, not scratch dict. For cross-iteration state read from a branch,
   use a `PARENT.declare(x=…)` cell instead. (Unchanged from v1; still
   annoying.)

4. **HMAC capability tokens have no operonx equivalent** — enforce
   sub-agent tool-subset at graph construction site. Add HMAC in v2 only
   if we ship a plugin surface.

5. **Backpressure** — `ExecutionHandle._queue` is unbounded. Fine for
   typical sessions; long token-heavy sessions with a slow CLI consumer
   could exhaust memory. Would need an operonx-core change
   (`asyncio.Queue(maxsize=N)`). Defer to a later operonx release unless
   we hit it.

6. **Adaptive turn budget** — the synthesized loop's `max_iterations` is
   static. A hermes-style budget that shrinks on tool-error rate needs
   the loop's back-edge branch to read runtime state
   (`PARENT["turn_stats"]`) via an `@op`. Doable without framework
   changes; just an idiom to document.

**Resolved from v1:** the "GraphOp.loop is the one imperative-feeling
primitive" complaint (v1 gap #7) is gone. Back-edge loops in 1.0.0 are
DAG-native. The `@fold` decorator wish is moot.

---

## 11 · Steal / Reject — abbreviated

Same list as v1 (28 steals × 20 rejects). The delta is that hermes-derived
patterns steal *conceptually* now — we don't inherit any of hermes's
plumbing. We steal the `_check_fn` TTL-cache algorithm; we don't steal the
~7k-LOC god-class it lives in. We steal the `apply_cache_control`
invariants; we don't steal the 60-parameter `__init__`. We steal the
tool-block message contract (`role:tool` with `"blocked: reason"`); we
don't steal the polling loop.

The biggest reject remains **hermes's decision to make `AIAgent` a class
at all**. That is what forced the 60-param init, the ~600 callbacks, the
fat forwarder shims, the "Phase 1 step 4 in progress" perpetual refactor.
Operonx's `@op`/`@graph` model **structurally prevents** that failure mode
— you cannot god-class your way out of a DAG.

---

## 12 · Governance — the Footprint Ladder

Unchanged from v1. Adopt on day 1 in `operonx/agents/CONTRIBUTING.md`.

```
 6. core          ██████  ← reserve for absolute universals
 5. MCP           █████
 4. plugin        ████
 3. gated tool    ███
 2. CLI + skill   ██
 1. extend op     █       ← START HERE
```

Every PR adding a `core` primitive must justify why rungs 1–5 don't work.

---

## 13 · First concrete step

1. **P0 · half-day** — rename `operonx/tools/` → `operonx/cli/` (single-file
   change: `pack.py` + one `pyproject.toml` entry). Scaffold `operonx/agents/`
   per §4. Add `CONTRIBUTING.md` with Footprint Ladder.

2. **1-page ADR before P1 code** — cover:
   - `@tool` decorator: metadata carrier only (op factory reused via `@op`).
   - `TOOL_REGISTRY` shape: `dict[str, op_factory]`, populated at import time.
   - `dispatch_one` subgraph shape: 5 ops + 2 branches + optional InterruptOp
     for destructive (see §7.2).
   - Two-schema reality: operonx `Param` for wiring vs LLM JSON Schema for
     payload.
   - Where `check_fn` TTL + failure-grace lives (pure Python helper called
     at `get_tool_definitions()` build time).
   - **New in v2:** HITL harness contract (what CLI/HTTP callers must do
     to respond to `InterruptEvent`).
   - **New (§14):** where the `wrap_*` closure fold lives, and why it is
     not a graph feature.
   - **New (§14):** the tool-failure contract — every failure mode
     (raise, timeout, cancel, unknown name, denial) produces a tool
     message, so the history invariant holds and the model can recover.
   - **New (§14):** how concurrent `InterruptOp`s are serialised under
     `.parallel()` fan-out.
   - **New (§14.5):** in-tree `operonx/agents/` vs a sibling package —
     answer it, don't assume it. LangGraph reversed this exact call.

   Two things to **verify before writing `dispatch.py`**, because both
   change its shape:
   - Can two branch arms converge on a single node, or does each arm need
     its own instance (as §7.2 currently assumes)? Nothing in
     `tests/internal/core/ops/flow/` establishes this.
   - Does an `InterruptOp` inside a `.parallel()` fan-out suspend only its
     own arm, or the whole pump?

3. **Then P1 — build in this order:**
   1. `@tool` + `TOOL_REGISTRY` (`operonx/agents/tool.py`)
   2. `dispatch_one` + `dispatch_all_tools` (`operonx/agents/graphs/dispatch.py`)
   3. `permission_check` op (`operonx/agents/ops/permission_ops.py`)
   4. `build_react_agent` factory (`operonx/agents/graphs/react.py`)
   5. Rewrite `docs/guide/05-agents.md` example — should be ~25 lines using the new factory
   6. Minimal HITL CLI harness in `examples/python/ex09_agent_workflow/` — reads InterruptEvents, prompts, calls `handle.resume`

Everything after P1 compounds on these six.

---

## 14 · Cross-implementation audit (11 Aug 2026)

Read `NousResearch/hermes-agent`, `langchain-ai/langgraph` and
`langchain-ai/langchain` at HEAD and measured them against this plan.
Two conclusions: **the skeleton is confirmed correct**, and **the plan
mistakes the loop for the work**.

### 14.1 · Sizes

| Codebase | Agent layer | Shape |
|---|---|---|
| `langgraph/libs/prebuilt` | **3,676 LOC** | `tool_node.py` 2,030 · `chat_agent_executor.py` 1,015 |
| `langchain/libs/langchain_v1/langchain/agents` | **13,702 LOC** | `middleware/` ~9,400 · `factory.py` 2,062 |
| `hermes-agent/agent` | **136,329 LOC** (~140 modules) | `conversation_loop.py` 7,757 · `run_agent.py` 8,303 |

Our estimate (~13 files, most <200 LOC) is the right order of magnitude
against langgraph's prebuilt layer. Hermes is a hosted product, not a
framework — ~45 of its modules are provider adapters, credential pools
and billing views that `ResourceHub` + `LLMOp` already own or that are
correctly out of scope.

**Hermes is not a structural teacher.** `run_conversation` is a single
**6,335-line function** — zero nested defs, 35 loops, 46 `try` blocks,
and the module exports exactly one symbol. `AIAgent` carries **260
methods**. §11's "biggest reject" is empirically confirmed. Its value
here is as a *checklist of concerns*, and those live in its small
modules, never in the loop.

### 14.2 · The one structural hole — `wrap_*` has nowhere to live

LangChain v1 exposes six middleware hooks, and they compile in two
fundamentally different ways:

| Hook | Compiles to | We have it? |
|---|---|---|
| `before_agent` · `before_model` · `after_model` · `after_agent` | A real graph node — `graph.add_node(f"{m.name}.before_model", …)` | **Yes, free.** That is just an op in the graph |
| `wrap_model_call` · `wrap_tool_call` | A **Python closure chain folded inside one node** — `_chain_model_call_handlers` / `_chain_tool_call_wrappers`, composed right-to-left by `compose_two` | **No. Nothing in this plan corresponds to it** |

The compiled LangChain agent graph is `model` + `tools` plus one node per
state-hook middleware. The wrappers are invisible to the graph.

They must be. A `wrap_*` handler receives a `handler` continuation it may
call **zero times** (cache hit, short-circuit), **once** (passthrough), or
**N times** (retry with a modified request):

```python
def wrap_tool_call(self, request, handler):
    for attempt in range(3):
        result = handler(request)           # same step, re-invoked
        if result.status != "error":
            return result
    return result
```

**A DAG edge cannot express that.** Back-edge loops give us *iteration*
across turns; they do not give *re-invocation with modified input inside
a single step*. Retry-around-a-call, response caching, and short-circuit
are all this shape.

**Resolution — rung 1, not core.** Compose wrappers as a plain Python
fold inside `exec_tool` and the LLM-call op. No operonx change, no new
primitive, no graph feature. This is precisely what LangChain does, and
it is the same conclusion the Footprint Ladder reaches independently:
start at rung 1 and climb only when it provably fails. It does not fail
here.

Design note for P1: the fold belongs in `operonx/agents/graphs/dispatch.py`
as a `wrappers: list[Callable]` parameter threaded to `exec_tool`, not as
an `@op` argument — wrappers are control flow, and control flow that the
tracer sees as data flow produces spans that lie about what ran.

### 14.3 · Gaps confirmed by an independent implementation

Every gap found by reading `ToolNode` also has a shipped LangChain
middleware behind it. These are not speculation.

| Missing from this plan | Their implementation | Consequence if skipped |
|---|---|---|
| **Tool errors must become tool messages** | `tool_error.py` + `tool_retry.py` (612 LOC); langgraph `handle_tool_errors` | `exec_tool` has no error path. **The first tool that raises kills the run and the model never learns.** Highest-severity item in P1 |
| **Unknown tool name** | `_validate_tool_call` → `ToolMessage(status="error")` listing valid names | `TOOL_REGISTRY[name]` raises `KeyError` in both `parse_call` and `exec_tool` |
| **Every `tool_call` needs a matching tool message** | `_validate_chat_history` | Most providers 400 on an unmatched call. `blocked_result` covers denial; crash, timeout and cancel are uncovered |
| ~~**Concurrent HITL**~~ 🔴 **WRONG — see §15.1 V4** | `human_in_the_loop.py` (500 LOC); hermes `_ConcurrentToolAuthorizationGate` | ~~§7.2 fans out `.parallel(max=10)` and each arm can raise its own `InterruptOp` — ten simultaneous approval prompts~~ Probed: the pump serialises, one event outstanding at a time. **No gate needed.** |
| **Repeat / loop cap** | `tool_call_limit.py` (495); hermes `ToolCallSignature` + `LoopCapConfig` | Model calls the same tool with identical args forever, inside `max_iterations` |
| **State injection into tools** | `InjectedState` / `InjectedStore` / `ToolRuntime` | A tool needing session context has no channel that isn't also in the LLM schema |
| **Iteration budget** | `model_call_limit.py` (267) | Already logged as gap #6 in §10 |
| **Redaction / PII** | `pii.py` + `_redaction.py` (1,332) | Nothing in this plan. Tool output flows to the model and the tracer unfiltered |
| **Context compaction depth** | `summarization.py` + `context_editing.py` (1,208); hermes `context_compressor.py` (7,386) | P2 budgets one `compact_messages` op for what two teams treat as a subsystem |

### 14.4 · What changes in the plan

1. **Fold rows 1–6 of §14.3 into P1.** They are not polish. Without the
   first three, the first tool exception ends the run — the most common
   thing that happens to a real agent.
2. **Resolve the branch-convergence question before writing
   `dispatch.py`.** §7.2 instantiates `exec_tool` and `collect_result`
   twice, once per branch arm, because it is not established that two
   arms can converge on one node. No test in
   `tests/internal/core/ops/flow/` covers it. If they cannot converge,
   the dispatch subgraph's shape changes and the duplication grows with
   every branch.
3. **P2 grows.** Compaction is a subsystem, not an op.
4. **Add redaction to P3**, next to the permission policy modes — it is
   the same trust boundary.
5. **Leave the loop alone.** Both implementations confirm §5 and §7.3.
   The ReAct loop really is ~35 lines on our primitives; the reason
   theirs are bigger is scaffolding we genuinely do not need.

### 14.5 · One decision worth revisiting

`create_react_agent` is **deprecated in langgraph v1.0** and moved to
`langchain.agents`, along with `HumanInterrupt` and `ActionRequest`. The
team that shipped this at scale concluded the agent layer does not belong
in the graph-runtime package.

This plan puts `operonx/agents/` in-tree. That is not obviously wrong —
we have no equivalent of the langchain/langgraph split, and P4's
`operonx-code` harness is already out of tree. But it is a reversal by
the closest prior art, and worth an explicit answer in the P1 ADR rather
than an assumption.

---

## 15 · Verification ledger

**Why this exists.** §2 lists fifteen primitives as "the substrate —
everything resolves back to one of these." It was written from docstrings
and design docs, not from running code. Two rows have since been probed
and **one was wrong**: `interrupt_op.py`'s own docstring says engine
integration is *future* Phase 2b3 work, and §2 wrote it up as shipped.

Two out of two proves nothing statistically. It does establish that a
docstring is not evidence. So: **before you build on a §2 row, probe it.**
Not all of them up front — that trades a week for certainty we do not
need yet. One at a time, when the phase that needs it comes up.

Convention: 🔴 confirmed wrong · 🟡 unverified · 🟢 verified by a probe
that lives in `tests/`.

### 15.1 · Confirmed issues — fix when the owning phase starts

| # | § | Issue | Owner phase |
|---|---|---|---|
| V1 | §7.2 | ✅ **resolved in P1.** 🔴 Duplicates `exec_tool` + `collect_result` per branch arm. Unnecessary — arms **can** converge on one node by writing `PARENT` cells (`a["raw"] >> PARENT["picked"]`, `collect(raw=PARENT["picked"])`). Direct `collect(raw=a["raw"])` genuinely fails, so the sketch's caution was right; the conclusion was not. **Dispatch gets simpler, not more complex.** Built that way: both approval arms write `PARENT["decision"]` and one `execute` reads it. | P1 |
| V2 | §7.2 | ✅ **resolved in P1.** 🔴 `InterruptOp(payload={"tool": …, "args": …})` — a Ref nested in a dict was silently unresolved, so the human approved blind. Now raises (`_params._reject_nested_ref`). Until nested-ref support lands, build the payload in an upstream `@op` and pass one bare Ref — `parse_call` does. | P1 |
| V3 | §2 · §7.2 | 🔴 `engine.stream()` does **not** auto-subscribe to the interrupt bus and `handle.resume(id, value)` does **not** exist. `bind_interrupt_bus` has zero callers outside its own export. Working path today: `handle = engine.start(...)`, `bind_interrupt_bus(handle.state, sink)`, `handle.state.resume_interrupt(iid, value)` — verified end to end. Wiring it properly is a contained `engine.py` change. | P1 |
| V4 | §14.3 | 🔴 **My error.** The "concurrent HITL → N simultaneous approval prompts, needs a serialising gate" row is wrong. The pump serialises: exactly one `InterruptEvent` outstanding at a time, 2.76s wall for 3 calls. No gate needed. Delete the row when §14.3 is next edited. | — |
| V5 | new | ✅ **resolved in P1.** 🔴 **Errors never leave `Operon.run()`.** `GraphOp._process` catches everything, logs it, writes `state[full_name, "error", ctx]`, and returns a partial result. Intended (`ex08_error_handling`: "workflow doesn't crash"), but it means a raising tool produces no output, the downstream collector *also* fails, no tool message is emitted, and the provider 400s on the next turn. **`exec_tool` must catch every exception itself and always return a tool message.** Two silent failures stack otherwise. `execute` now catches everything and always returns a tool message. | P1 |
| V6 | §5 | ✅ **done — and it did not compile.** §7.3 used `@graph(max_iterations=…)`, which does not exist; the kwarg was removed with the 1.0.0 retry sugar. Four further defects came out of running it: V7–V10 below. | P1 |
| V7 | §7.3 | 🔴 **No turn cap exists at graph level.** The synthesized loop is pinned to 1000 at `cycle_rewrite.py:402`. Deliberately **not** plumbed through `@graph`: a graph-level cap cuts mid-flight, tells the model nothing and leaves partial state. `count_turn` in the agent layer injects a notice at the limit and lets the model take one final turn, so exhaustion exits the way success does. | P1 ✅ |
| V8 | core | ✅ **fixed.** A back-edge source below a generator fan-out never fired, so any loop of the ReAct shape stopped after one iteration — silently. `end_time` lands at `('main','[0]','__collect__')`, never at the iteration ctx, and the spec (`STATE_LOOP_REFACTOR_PLAN.md:518`) required an exact match. Termination now accepts any ctx below the iteration's. 14 regression tests; none of the 32 prior cycle-rewrite tests put a generator in a loop. | P1 ✅ |
| V9 | §7.3 | 🔴 **An op wired after a generator-containing loop never runs.** The loop emits at item contexts, so the downstream op never reaches ready and is skipped with no error. Kills the obvious "terminal `finish` op reads the cells and emits them" design. `agent_result(result, agent)` reads the cells directly instead, which is why it needs the graph. | P1 ✅ |
| V10 | §7.2 | 🔴 **`collect()` behind `parallel()` inside a loop invokes per item**, not per batch — measured `[[0],[2],[0],[2]]` for 2 items × 2 iterations. Consumers must tolerate a partial batch. Harmless when the target cell has a reducer, since each write merges; `gather_tool_messages` handles both shapes. | P1 ✅ |

### 15.2 · §2 rows — verification status

Probe the row, then flip its light and link the test.

| § 2 row | Status | Needed by |
|---|---|---|
| **HITL suspend/resume** (`InterruptOp`) | 🔴 partly wrong — see V3 | P1 |
| Tool fan-out (generator op + `Ref.parallel(max=N)`) | 🟢 `test_loop_generator_backedge.py` — but see V8/V10 | P1 |
| Ordered gather (`Ref.collect()` — yield-index order) | 🔴 **per-item inside a loop, not per batch** — V10 | P1 |
| Turn loop (back-edge → synthesized `_GraphLoop`; termination when no back-edge source fired) | 🟢 after the V8 fix; was broken for any loop containing a generator | P1 |
| Shared cells + reducers (`PARENT.declare(reducers=)`) | 🟢 verified — the cell merges correctly; `run()`'s output dict does not (V6 F2) | P1 |
| Message accumulation (`add_messages` id-upsert, `RemoveMessage`) | 🟢 id-upsert verified end to end; **raises on a non-list**, which is why `gather_tool_messages` exists | P1 |
| Structured LLM output (`LLMOp.of(fields=, validators=, max_retries=)`) | 🟡 | P1 |
| Refusal vs parse failure (`_is_refusal` → `fallback=`) | 🟡 | P1 |
| LLM streaming (`stream=True` per-token frames) | 🟡 | P2 |
| Custom progress events (`EmitOp` + `stream(mode="custom", channels=)`) | 🟡 | P2 |
| Cross-run persistence (`Checkpointer`, `handle.get_state(step)`, `list_steps()`) | 🟡 | P2 |
| Observability shaping (`@op(exclude=, include=, observe_max=)`) | 🟡 | P2 |
| Per-run scratchpad (`SCRATCH[key]` through the observer bus) | 🟡 | P2 |
| Sub-agent isolation (nested `@graph`, hermetic parent refs, nested spans) | 🟡 | P3 |
| Preemptive cancel (`yield Interrupt(ctx_to_cancel=…)`) | 🟡 | P3 |
| Async I/O dispatch (`@op(bound=)`) | 🟢 — exercised throughout the existing suite | — |
| Config + secrets (`ResourceHub`) | 🟢 — exercised throughout the existing suite | — |

### 15.3 · Core work surfaced, not scheduled

| # | Item | Status |
|---|---|---|
| C1 | Reject Refs nested in containers | **done** — `_params._find_nested_ref`, PR #29 |
| C2 | *Support* nested Refs (hoist each buried ref into its own cell, reassemble at read time). Needs a decision first: teach operonx-rs the new `serialize()` form, or refuse to serialize graphs using it as synthetic loops already do. | open — blocks nothing in P1 given V2's workaround |
| C3 | Wire `engine.stream()` + `handle.resume()` to the interrupt bus (V3) | open — ~½ day, contained to `engine.py` |
| C4 | Loop termination below a generator fan-out (V8) | **done** — `task_scheduler._ctxs_within` |
| C5 | An op after a generator-containing loop never becomes ready (V9). Worked around in the agent layer; the underlying readiness rule is untouched. | open — no owner |

### 15.4 · The rule

A row stays 🟡 until a **committed test** exercises it — not a scratch
probe, not a docstring, not this plan. When a phase starts, promote only
the rows that phase needs. Anything found wrong gets a line in §15.1 and
a fix in the same PR that needed it.

---

## Sources studied

- [openclaw/openclaw](https://github.com/openclaw/openclaw) — heartbeat scheduler, SKILL.md frontmatter, serialized session lane
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — deep-inspected across core loop, memory, tools, subagents, learning loop (see v1 for full evidence trail); re-measured 11 Aug 2026 (§14)
- [opencode-ai/opencode](https://github.com/opencode-ai/opencode) — canonical tool set, persistent shell, read-before-edit invariant
- [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) — `libs/prebuilt`: `ToolNode` error handling, tool-call validation, state injection, `tools_condition` (§14)
- [langchain-ai/langchain](https://github.com/langchain-ai/langchain) — `libs/langchain_v1/langchain/agents`: the middleware stack, and the `wrap_*`-as-closure-chain finding that §14.2 turns on
- [BA-CalderonMorales/agent-harness](https://github.com/BA-CalderonMorales/agent-harness) — auto-compaction with headroom, layered permission rules, capability flags
- [huggingface/smolagents](https://github.com/huggingface/smolagents) — prompts-as-YAML
- [LangGraph](https://langchain-ai.github.io/langgraph/) — `add_messages` reducer semantics (RemoveMessage + REMOVE_ALL_MESSAGES sentinels are LangGraph-compatible in operonx 1.0.0), stream modes taxonomy
- [Instructor](https://python.useinstructor.com/) — semantic retry with error-guided prompts (mirrored in `LLMOp.of(retry_hint=True)`)

Operonx internals studied to ground the recast:

- `operonx/core/ops/base.py` — `BaseOp` lifecycle, ContextVars, tracing hooks, `@op(exclude=…, include=…, observe_max=…)`
- `operonx/core/ops/graph/graph_op.py` — `GraphOp`, nesting, `strict_dag=` opt-out
- `operonx/core/ops/graph/cycle_rewrite.py` — Phase 3 back-edge → `_GraphLoop` rewrite
- `operonx/core/ops/graph/task_scheduler.py` — streaming, `_on_eof` loop re-dispatch, `Ref.parallel()`/`.collect()`, `_sweep_ctx` interrupt handling
- `operonx/core/ops/flow/branch_op.py` — `if_(…).else_()`, soft edges, back-edge classification
- `operonx/core/ops/flow/interrupt_op.py` — `InterruptOp` HITL primitive
- `operonx/core/ops/flow/emit_op.py` — `EmitOp` custom event bus
- `operonx/core/states/*` — `Ref`, `Cell`, `MemoryState`, per-context isolation, `_notify_scratch` observer bus
- `operonx/core/states/parent.py` + `_edges.py` — `PARENT.declare(reducers=…)`
- `operonx/reducers.py` — `add_messages`, `dict_merge`, `RemoveMessage`, `REMOVE_ALL_MESSAGES`
- `operonx/checkpoint/{base,memory,bridge}.py` — Checkpointer protocol, `CellWriteEvent`, `ScratchWriteEvent`, `StepEvent`, `InterruptEvent`, `CustomEvent`
- `operonx/core/registry/resource_hub.py` — singleton, lazy, `${VAR}` interpolation
- `operonx/core/workflow_trace.py` — V3 auto-record
- `operonx/providers/ops/llm.py` — real op example with streaming + fallback + tools passthrough + `LLMOp.of` structured mode
- `operonx/providers/parsing.py` — pure text parsing without an LLM call
