# Loop primitive redesign — discussion notes

**Status:** exploratory. No code changes. `GraphOp.loop` stays as-is for now.
**Related docs:** [`WHILE_LOOP_PRIMITIVE.md`](WHILE_LOOP_PRIMITIVE.md) (parked full design), [`AUTO_SOFT_BRANCH_MERGE.md`](AUTO_SOFT_BRANCH_MERGE.md), [`BRANCH_INLINE_API.md`](BRANCH_INLINE_API.md).

This memo captures a long design conversation about whether/how to replace or rework `GraphOp.loop`. Grounded in real LangGraph source-code inspection (Pregel scheduler, `_loop.py`, channel reducers, Send API). Written to preserve context after conversation compaction.

---

## TL;DR

- `GraphOp.loop` feels syntactically weird but is **functionally fine**. All redesign paths add real cost for cosmetic gain.
- **LangGraph's Pregel + cyclic edges** is a genuinely different scheduler model — not a drop-in improvement, and rebuilding operonx to match would take ~1000+ LOC + break Rust runtime parity.
- **The one thing worth actually stealing from LangGraph is reducers** — `Annotated[list, add_messages]`-style declarative state accumulation. Fixes multi-source-fan-in conflict cases beyond just loops. Highest value/effort ratio.
- **Operonx's for-loop model (generator ops + per-yield ctx + `.parallel()` / `.collect()`) is already cleaner than LangGraph's Send API.** No changes needed there.
- **Trace bloat from long-running loops** is a real problem in both frameworks, and it's orthogonal to loop-primitive design. The fix is compaction primitives (already in `AGENT_EXTENSION_PLAN.md`).
- **Recommendation:** park loop redesign. Start P0 of the agent framework. Revisit if concrete pain emerges.

---

## 1. The pain points that started the discussion

`GraphOp.loop` today has four ergonomic warts:

```python
with GraphOp.loop(
    until="count >= 5",              # (1) stringly-typed expression
    max_iterations=25,
    count=0, messages=[],            # (2) **kwargs mixed with config
) as g:
    inc = tick(counter=PARENT["count"])
    inc["counter"] >> PARENT["count"]  # (3) per-var feedback line
    inc["messages"] >> PARENT["messages"]
    START >> inc >> END              # (4) classmethod-returned context manager
```

1. `until="expr"` string — unusual in Python
2. `**initial_state` kwargs — can't tell state from config at a glance
3. `op["k"] >> PARENT["k"]` feedback — one line per state var
4. `with GraphOp.loop(...)` — reads as a classmethod-returning-context-manager, not a first-class primitive

None of these are bugs. All are cosmetic.

## 2. Design ideas explored (in order)

### 2.1 · `GraphOp.scan(step, initial, until)` — REJECTED

Higher-order combinator that turns a `state → {next_state, done}` graph into a self-looping generator op. User called it "even messier" than `GraphOp.loop`. Retracted.

### 2.2 · `while_(**state)` primitive — DESIGNED THEN PARKED

Full replacement with:
- `with while_(count=0):` context manager or `@while_(count=0)` decorator form
- Termination via `if_(cond, END).else_(PARENT)` at the loop tail
- Delete `until=` entirely — termination is a branch

See [`WHILE_LOOP_PRIMITIVE.md`](WHILE_LOOP_PRIMITIVE.md) for full spec including 9 build-time error cases, multi-source fan-in conflict handling, `.of(max_iterations=...)` config surface, migration story.

Total scope: ~800 LOC + Rust serialization question + 5 open design decisions.

**User verdict after seeing the full design doc: "are we overdoing it?"** Correct read — the scope/benefit ratio was bad. Parked.

### 2.3 · Yield-with-`__loop__`-metadata — DEFERRED

Attach loop-control metadata to a generator's yielded frames:
```python
yield {"state": next_state, "__loop__": {"done": bool, "feedback": {...}}}
```

Clever but pushes complexity into an out-of-band marker. Requires scheduler patch. Not obviously better than branch-based termination. Not pursued.

### 2.4 · Cyclic if-back edges (LangGraph-style) — RESEARCHED, TOO EXPENSIVE

User asked "can we make operonx support cyclic graphs like LangGraph?" Deep research on Pregel model (see §3). Verdict: real work (~1000+ LOC + Rust parity break) for questionable ROI vs the alternatives.

### 2.5 · `.shared()` + branch-to-PARENT — MOST PROMISING

User reminded me operonx already has `PARENT.shared(**vars)` — cells that persist across all stream contexts including loop iterations. Combined with `if_(cond, END).else_(PARENT)` terminator, we could collapse the loop primitive:

```python
@graph
def counter():
    PARENT.shared(count=0)
    inc = tick(count=PARENT["count"])
    inc["count"] >> PARENT["count"]   # writes to shared cell — persists across iters
    START >> inc >> if_(inc["count"] >= 5, END).else_(PARENT)
```

Where `.else_(PARENT)` = "re-dispatch the loop". Scope: ~50-100 LOC scheduler patch.

BUT — user then correctly noted the "when the loop happens, does context reset?" question, which led to the LangGraph state-model deep-dive (see §3.3).

### 2.6 · Final position — DO NOTHING

After the full journey: `GraphOp.loop` is fine. All the alternatives either add complexity for marginal gain OR are equivalent under the hood. The one actually valuable idea (reducers on `.shared()`) is orthogonal to the loop primitive and worth its own design pass.

## 3. What we learned about LangGraph (from actual code inspection)

### 3.1 · Cyclic scheduler — Pregel super-steps

From [`libs/langgraph/langgraph/pregel/_loop.py`](https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/langgraph/pregel/_loop.py):

Each super-step has 3 phases:
1. **Plan** — which nodes have unread messages on incoming channels?
2. **Execute** — run all active nodes in parallel, updates invisible until phase 3
3. **Update** — apply outputs to channels

Cycles are **not validated at build time**. Any graph shape allowed. Cycles work because the scheduler is activation-based (fire on new input), not ready-count based.

### 3.2 · Infinite-loop protection — one integer

```python
def tick(self) -> bool:
    if self.step > self.stop:
        self.status = "out_of_steps"
        return False   # → GraphRecursionError
```

`self.stop` = `config["recursion_limit"]`. Default **1000** as of v1.0.6 (was 25 historically). One counter, checked once per super-step. That's it.

### 3.3 · State model — channels + reducers, NOT per-iteration context

Every state field is a channel with a reducer:
- `LastValue` (default) — replace semantics, `InvalidUpdateError` on concurrent writes
- `add_messages` — merge lists by ID
- `operator.add` — accumulate
- Custom reducers — any user-defined merge function

**Channels persist across the whole run, including cycle re-visits.** No per-iteration reset. State accumulates naturally via the reducer.

Example:
```python
class State(TypedDict):
    messages: Annotated[list, add_messages]   # append
    iteration: Annotated[int, operator.add]   # accumulate
    latest_query: str                          # replace (LastValue default)
```

After 3 super-steps, `messages` contains all appended messages, `iteration` has the sum. No user code manages state persistence.

### 3.4 · Per-iteration observability — checkpointer + step counter

LangGraph doesn't have per-iteration context isolation, so tracing/inspection needs different machinery:
- **Checkpointer** (opt-in) — one full-state snapshot per super-step (`MemorySaver` / `SqliteSaver` / etc.)
- **`langgraph_step` metadata** on every task/node — tracers filter by step
- **7 stream modes** (`values` / `updates` / `messages` / `tasks` / `checkpoints` / `debug` / `custom`) — each emits per-step with step index

Accumulator channels ARE the semantic trace (reading final `messages` gives full history).

### 3.5 · For-loop pattern — `Send` API

For map-reduce or per-element fan-out:
```python
def dispatch(state):
    return [Send("worker", {"item": x}) for x in state["items"]]
```

Each `Send`:
- Becomes a distinct task with own input payload
- Runs in parallel, task ID + namespace (`ns=("worker:<task_id>",)`)
- Can trigger different downstream conditional edges → different flow per element
- Outputs land in shared channels via reducer merge

### 3.6 · Trace size in production

Both LangGraph's checkpointer + span history grow **O(N²)** when state accumulates (each checkpoint saves the entire current `messages` list). A 20-turn agent = ~1.6MB just for messages across checkpoints. Real production issue.

Mitigations in LangGraph:
- Checkpointer opt-in (no persistence = no cost)
- `durability="exit"` — save only final checkpoint
- Manual `trim_messages` nodes
- LangSmith sampling (10-20% in production)
- Custom serializers

None of these are magic. Long-running agents need compaction regardless of framework.

## 4. System-design comparison — the honest table

| Concern | Operonx today | LangGraph |
|---|---|---|
| **For-loop: per-element context** | ✓ ctx tuple per yield (`("[i]",)`) via generator ops | ✓ task ID + namespace per Send |
| **For-loop: different downstream per element** | ✓ branch per frame, each frame independent | ✓ Send can target different nodes; conditional edges post-fan-out |
| **For-loop: element results merge** | Manual — `.collect()` on consumer | Automatic via reducers |
| **For-loop: parallel concurrency** | `.parallel(max=N)` on Ref | Scheduler-configured |
| **While-loop: per-iteration context isolation** | ✓ ctx tuple per iter | ✗ single shared state |
| **While-loop: state accumulation** | Manual (`>> PARENT["k"]` or `.shared()`) | Automatic via reducers |
| **While-loop: different flow per iter** | ✓ branch inside loop body | ✓ conditional edge inside cycle |
| **While-loop: infinite-loop protection** | `max_iterations` on `GraphOp.loop` | `recursion_limit` on invocation |
| **Trace size for long loops** | Per-iter cells retained in-memory | Per-step checkpoints (persistent if enabled) |
| **Recovery/replay** | Not built — would need checkpointer | ✓ checkpointer-based |
| **Cyclic edges in graph** | ✗ DAG-only, cycles rejected at build | ✓ first-class |
| **Scheduler complexity** | Simpler (ready-count DAG) | More complex (activation-based Pregel) |
| **Rust runtime parity** | ✓ shared JSON contract | N/A |

### Key insight

**Operonx's for-loop is arguably CLEANER than LangGraph's for-loop** — generator + fan-out + per-yield ctx handles per-element context, per-element downstream, and per-element result aggregation naturally. No changes needed here.

**Operonx's while-loop is less ergonomic than LangGraph's while-loop** — mainly because we lack:
1. Reducers on shared state (automatic accumulation)
2. Native cyclic-edge syntax (loop-back is visible in the flow)

But even the while-loop gap is modest. The syntactic differences are 3-6 lines per loop.

## 5. Three levels of ambition for cyclic support

Documented from the LangGraph-inspection conversation:

### Level 1 — Full Pregel scheduler rewrite (~1000+ LOC, weeks)

Delete DAG assumption. Rewrite scheduler as activation-based super-steps. Replace `Cell` with reducer-backed channels. Global step counter. Global recursion limit.

**Breaks:** `_initial_ready`, `_stream_initial_ready`, per-context Cell addressing, generator-op streaming semantics, ctx-tuple discipline, **Rust runtime parity** (operonx-rs would need matching rewrite).

**Verdict:** REJECTED. Cost dominated by Rust parity break alone. No evidence of user pain that only this solves.

### Level 2 — Compile-time cycle-to-loop rewrite (~200-300 LOC, days)

Keep DAG scheduler. Add build-time pass:
- User writes `builder.add_edge("tools", "call_model")` style cycle
- At `build()`, detect back-edges (topological order violation)
- Extract cycle body, synthesize a nested `GraphOp.loop`
- Scheduler still sees a DAG

User gets LangGraph-style syntax. Rust runtime unaffected (sees normal `GraphOp.loop` in serialized JSON).

**Trade-offs:** build-time analysis complexity, silent behavior changes (cycle rewritten as loop node), harder to debug edge cases (multiple back-edges, nested cycles).

**Verdict:** DEFERRED. Real code path but speculative until we see users trying to write cyclic graphs.

### Level 3 — Branch-to-PARENT loop terminator (~50 LOC, hours)

Keep `GraphOp.loop`. Teach scheduler that inside a loop, `if_(cond, END).else_(PARENT)` at the tail means "END exits, PARENT re-dispatches".

```python
with GraphOp.loop(messages=[]) as g:
    call = call_model(messages=PARENT["messages"])
    tool = tool_node(tool_calls=call["tool_calls"])
    tool["messages"] >> PARENT["messages"]
    START >> call >> if_(call["tool_calls"] == None, END).else_(tool)
    tool >> PARENT   # scheduler treats "data to PARENT" in a loop as re-dispatch
```

Not identical to LangGraph's `add_edge("tools", "call_model")` back-edge — the loop wrapper stays visible — but ergonomically 90% of the way there.

**Verdict:** THE PRAGMATIC OPTION if we ever revisit. Small scope, matches if/else spirit, keeps `GraphOp.loop` as the boundary that tracing/streaming/Rust already understand.

## 6. What actually might be worth doing — reducers on `.shared()`

Independent of loop redesign, this is the highest-leverage single change:

```python
PARENT.shared(
    messages=[],
    reducer={"messages": add_messages},
)
# Or per-var:
PARENT.shared("messages", initial=[], reducer=add_messages)
```

Fixes multiple concerns at once:
- **Automatic accumulation** across loop iterations (no `>> PARENT["k"]` chain)
- **Multi-source fan-in conflict resolution** (the E4 case in `WHILE_LOOP_PRIMITIVE.md` — two ops writing same shared key → reducer merges)
- **Sub-agent result aggregation** (parallel subagents → merge results via reducer)
- **Generator + collect patterns** where "accumulate" semantics matter

Scope estimate: ~150 LOC (schema tracks per-cell reducer, `_set_cell` applies reducer on write instead of overwrite, tests). Independent of loops — applies anywhere shared state is written from multiple places.

**Deferred until concrete evidence of pain** — agent framework will surface this if it matters.

## 7. Recommendations

### Do now
- **Nothing.** Move to P0 of the agent framework plan.

### Do if the agent framework work exposes concrete pain
- **Reducers on `.shared()`** — highest value, orthogonal to loops
- **Level 3 branch-to-PARENT** — small addition, matches LangGraph idiom, no risk to Rust parity

### Don't do
- **Level 1 Pregel rewrite** — cost/benefit is terrible
- **Level 2 cycle-detection-rewrite** — speculative, adds silent build-time transformation
- **`while_(**state)` full replacement** — too much surface for cosmetic gain

### Note but defer
- **Checkpointer infrastructure** — nice-to-have for long-running agents / crash recovery. Orthogonal to everything else here. Consider only after agent framework is live and we hit a real need for run persistence.

## 8. Open questions (not blocking anything)

1. If we add reducers to `.shared()`, does it break Rust serialization? Reducer as string identifier (`"add_messages"`, `"operator.add"`) → Rust side has a lookup table. Feasible but needs coordination.

2. Do we ever want per-op imperative access to shared cells (a `SHARED[...]` accessor mirroring `SCRATCH`)? Not needed today; ref-based access covers everything.

3. If we ship Level 3 branch-to-PARENT, does the operonx tracing V3 auto-record correctly distinguish loop iterations? Yes — the existing ctx tuple extension mechanism already handles this.

4. Would the agent framework want a checkpointer for long-running sessions? Probably yes, but design it as a general engine feature (not loop-specific). LangGraph's `MemorySaver` / `SqliteSaver` / `PostgresSaver` model is a reasonable reference.

## 9. Historical context

Two related features SHIPPED in operonx 0.11.0 (2026-07-30) during this same design conversation, extracted from the same "branch ergonomics" thread:

- **Auto-soft-edge** — build-time pass that flips branch-merge edges to soft when predecessors trace back to a common `BranchOp` ancestor via disjoint first-hop children. Kills the silent-deadlock bug from forgotten manual `~`. See [`AUTO_SOFT_BRANCH_MERGE.md`](AUTO_SOFT_BRANCH_MERGE.md).

- **Inline `if_/else_` branch API** — accepts op-instance targets, auto-wires `branch → target` edges, auto-names via LHS or `route_N` counter. Callbot's branch declarations inlined; net –18 lines. See [`BRANCH_INLINE_API.md`](BRANCH_INLINE_API.md).

Both are additive, backward-compatible, and independent of the loop question. They composed cleanly on the callbot with zero migration cost.

The loop question surfaced because these two features made everything ELSE about branches feel clean, and by contrast `GraphOp.loop` felt like the last piece of syntactic weirdness in the graph model. But after the full exploration, "clean-adjacent by contrast" is not sufficient justification for the redesign cost.

## 10. Cross-references

- [`WHILE_LOOP_PRIMITIVE.md`](WHILE_LOOP_PRIMITIVE.md) — the full `while_` design (parked; retained for future reference)
- [`AUTO_SOFT_BRANCH_MERGE.md`](AUTO_SOFT_BRANCH_MERGE.md) — shipped in 0.11.0
- [`BRANCH_INLINE_API.md`](BRANCH_INLINE_API.md) — shipped in 0.11.0
- [`../../AGENT_EXTENSION_PLAN.md`](../../AGENT_EXTENSION_PLAN.md) — op-native agent framework plan; §Compactor covers trace-size mitigation
- LangGraph references:
  - [pregel/_loop.py](https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/langgraph/pregel/_loop.py) — step counter, recursion limit
  - [pregel.md concept doc](https://github.com/langchain-ai/langgraph/blob/main/docs/docs/concepts/pregel.md) — Pregel model overview
  - [graph/message.py — add_messages reducer](https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/langgraph/graph/message.py)
  - [langchain-ai/react-agent template](https://github.com/langchain-ai/react-agent) — canonical ReAct cyclic-edge pattern
  - [Send API reference](https://reference.langchain.com/python/langgraph/types/Send)

---

## Post-compaction picking-up prompt

If a future conversation needs to continue this thread:

> "We finished a long design discussion on whether to replace `GraphOp.loop`. See `docs/design/LOOP_DESIGN_DISCUSSION.md`. Position: do nothing now. If we revisit, add reducers on `.shared()` first (highest value, orthogonal), then consider Level 3 branch-to-PARENT terminator (~50 LOC). Do NOT do Level 1 Pregel rewrite. See TL;DR + §7 recommendations."
