# State + Loop refactor plan (v3)

**Status:** proposal, revised after 4-agent adversarial review.
**Version:** v3 — fixes blocker bugs found in v2 review; drops Rust concern (callbot is Python-only); keeps SCRATCH untouched.
**Supersedes:** [`LOOP_DESIGN_DISCUSSION.md`](LOOP_DESIGN_DISCUSSION.md) §7 "do nothing" recommendation.
**Related:** [`WHILE_LOOP_PRIMITIVE.md`](WHILE_LOOP_PRIMITIVE.md) (parked partial design), [`AUTO_SOFT_BRANCH_MERGE.md`](AUTO_SOFT_BRANCH_MERGE.md), [`BRANCH_INLINE_API.md`](BRANCH_INLINE_API.md).

Three coupled changes that turn operonx's shared-state + loop model into something LangGraph-shaped, without breaking the DAG scheduler, the `(op, var, ctx)` cell model, or the existing SCRATCH accessor.

---

## TL;DR

| # | Change | LOC | Kills |
|---|---|---|---|
| 1 ✅ | `PARENT.declare(**vars, reducers=...)` + reducers on shared-cell writes | ~180 shipped | Forgettable `.shared()` name + silent data loss on fan-in |
| 2 | Unified `_write_cell` funnel + `Checkpointer` + `step_id` + `InterruptOp`/`EmitOp` + 4 stream modes + `@op(exclude/include/observe_max)` filter | ~770 | No inspect / replay / HITL / streaming / observability control for iterations |
| 3 | Cyclic edges (Level 2 rewrite via Tarjan SCC) | ~340 | `with GraphOp.loop(...)` weirdness |
| | **Total** | **~1020** | |

**Explicitly out of scope** (each intentionally dropped after review):
- ~~`STATE["k"]` accessor / STATE sentinel~~ — hides flow, breaks "read the graph body → know the DAG" invariant
- ~~`SCRATCH.declare()`~~ — one declaration API (on PARENT); SCRATCH stays as-is
- ~~Rust runtime parity~~ — callbot moved off Rust; Python-only
- ~~Cross-graph state access~~ — 95% case is handled by nearest-enclosing-graph today
- ~~Full Pregel scheduler rewrite~~ — see `LOOP_DESIGN_DISCUSSION.md` §5

**Rollout:** 3 phases, each shippable to a minor version. Note real inter-phase dependency (§Migration).

---

## Visual: before / after

### Before (operonx 0.11.0 today)

```python
with GraphOp.loop(
    until="count >= 5",              # ① stringly-typed expr
    max_iterations=25,
    count=0, messages=[],            # ② kwargs mix state + config
) as g:
    inc = tick(counter=PARENT["count"],
               msgs=PARENT["messages"])
    inc["counter"]  >> PARENT["count"]    # ③ last-write-wins → manual accumulation in body
    inc["messages"] >> PARENT["messages"]
    START >> inc >> END
```

### After (post-refactor)

```python
@graph
def counter():
    PARENT.declare(count=0, messages=[],
                   reducers={"messages": add_messages})    # ① one declaration + reducers

    inc = tick(count=PARENT["count"])
    inc["count"]    >> PARENT["count"]                     # ② reducer merges automatically
    inc["messages"] >> PARENT["messages"]

    START >> inc >> if_(PARENT["count"] >= 5,              # ③ terminator = branch
                        END).else_(inc)                     # ④ back-edge = loop
```

Same semantics. **10 lines → 8.** No `GraphOp.loop` wrapper, no `until=` string, no manual accumulation. Data-flow contract (op signatures + `>>` wiring) fully preserved — you can still read the graph body and know every read/write.

---

## Architecture: how the pieces fit

```
┌─────────────────────────────────────────────────────────────┐
│  User code (Python DSL)                                     │
│    PARENT.declare(count=0, reducers={...})   ← Phase 1      │
│    inc = tick(count=PARENT["count"])         (unchanged)    │
│    inc["count"] >> PARENT["count"]           (reducer P1)   │
│    inc >> if_(...).else_(inc)                ← Phase 3      │
│                                                             │
│  SCRATCH still available (unchanged, ambient dict)          │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  Build-time passes (graph_op.py)                            │
│    1. cyclic_to_loop        ← Phase 3 (Tarjan SCC)         │
│    2. auto_soft_edges       (shipped 0.11.0) — runs on     │
│                              post-rewrite DAG               │
│    3. reducer_bind          ← Phase 1                       │
│    4. validate DAG          (existing)                      │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  Runtime scheduler (task_scheduler.py)                      │
│    - _write_cell funnel: single mutation path   ← Phase 2 │
│      → Runtime dispatches downstream            │           │
│      → Reducer applies (if declared)            ← Phase 1  │
│      → Checkpointer captures every write        ← Phase 2 │
│      → Tracer enriches op span                  (existing) │
│    - step_id: monotonic counter per write batch  ← Phase 2 │
│    - InterruptOp suspends; resume via run.resume ← P2      │
│    - EmitOp pushes to custom stream channel      ← P2      │
│    - Stream modes: values / updates / frames / custom ← P2 │
└─────────────────────────────────────────────────────────────┘
```

Each phase is Python-side, additive, backward-compatible.

---

## Phase 1 — `PARENT.declare()` + reducers ✅ SHIPPED

**Status:** implemented on `feature/phase-1-declare-reducers`.
Landed pieces:
- `operonx/reducers.py` — `add_messages`, `dict_merge`, `RemoveMessage`, `REMOVE_ALL_MESSAGES`
- `PARENT.declare(**vars, reducers={...})` in `operonx/core/ops/_edges.py`
- `MemoryState._write_cell` funnel in `operonx/core/states/state.py` — routes both `__setitem__` and push-ref hop (B1 fix)
- `ReducerError` exception wrapping `(idx, old, new, cause)`
- `schema._reducers: Dict[int, Reducer]` slot; `_register_shared_vars` binds reducers by shared idx
- `PARENT.shared()` kept as `DeprecationWarning`-emitting alias for one minor version

Test coverage: 46 new tests (23 reducer unit + 23 integration), 1026 pass total, zero regressions.



### API

```python
@graph
def agent():
    PARENT.declare(
        count=0,
        messages=[],
        reducers={
            "messages": add_messages,     # upsert-by-id + RemoveMessage support
            "count": operator.add,
        },
    )
    ...
```

- Method on `PARENT` — same shape as today's `PARENT.shared()`, just renamed to `.declare()`
- Vars without a reducer keep today's `LastValue` semantics (overwrite)
- `PARENT.shared(...)` stays as **deprecated alias** for one minor version
- Reads unchanged: `PARENT["count"]`
- Writes unchanged: `op["counter"] >> PARENT["count"]`
- **No `SCRATCH.declare()`** — SCRATCH stays as-is (see §"On SCRATCH" below)

### Reducer semantics (fixed from v2)

**Critical fix from review**: reducers apply at the **cell-level write**, not in `__setitem__`.

Why: `state.py:118-120` push-ref path bypasses `MemoryState.__setitem__` and writes cells directly:

```python
self._cells[push_ref.idx][ctx_key] = push_ref._fn(value)   # bypasses __setitem__
```

100% of shared-cell writes come via push-ref. If the reducer branch lived in `__setitem__` only, it would silently skip every real shared-cell write.

**Fix**: introduce `_write_cell(idx, ctx, value)` helper that applies reducer when `idx in schema._shared_indices`. Both direct sets and push-ref writes call it. `__setitem__` also routes through it.

### Reducer input-dict semantic (was ambiguous)

`graph.run(inputs={...})` treats inputs as **initial values**, not reducer merges. Rationale: matches operonx's current behavior; reducers apply to *subsequent* writes only. Users who want input-merge semantics call the reducer explicitly at run-site.

### Common reducers ship with operonx

| Reducer | Behavior | Notes |
|---|---|---|
| `operator.add` | `a + b` | Lists, numbers, strings |
| `operator.or_` | `a or b` | OR-latch — replaces callbot's hand-rolled `is_final`/`should_transfer` latch |
| `add_messages` | Upsert-by-id, honors `RemoveMessage(id=…)`, `REMOVE_ALL_MESSAGES` sentinel | **Must match LangGraph contract fully** — else rename to `append_messages` to avoid silent breakage of ported code |
| `dict_merge` | Recursive key-wise merge | Aggregating structured worker results |
| `max`, `min` | Extreme value | Best-score tracking across branches |
| Custom `(a, b) -> merged` | Anything | User-defined |
| **(none)** | LastValue (overwrite) | Today's default |

### Error cases

| # | Case | Behavior |
|---|---|---|
| E1 | Two writes to same shared cell, no reducer | LastValue (today's behavior) |
| E2 | Two writes to same shared cell, with reducer | Both merged via reducer; **ordering is scheduler event order** (serial dispatch, no batching, non-deterministic across runs unless reducer is commutative) |
| E3 | Reducer raises | Op fails with `ReducerError(operand_old, operand_new)`, run halts |
| E4 | Reducer key not in `PARENT.declare()` vars | Build-time: `"reducer for 'foo' but no declared var 'foo'"` |
| E5 | `PARENT.declare()` called inside `if_`/branch body | Build-time: `".declare() must be at @graph top level"` |
| E6 | Two `PARENT.declare()` calls in same graph | Build-time: `"declare() may be called at most once per graph"` |
| E7 | Push-ref to a declared cell fires during ctx-isolated stream | Reducer merges at DEFAULT_CONTEXT (shared cells are ctx-collapsed by design) |

### Migration

- `PARENT.shared(count=0)` → `PARENT.declare(count=0)` — mechanical rename, adds deprecation warning
- No behavior change unless a reducer is added
- Callbot: ~10 lines to update (only 7 shared-var sites total, per review)

### Scope

- `operonx/core/states/state.py` — `_write_cell` helper + route `__setitem__` and push-ref through it (~35 LOC)
- `operonx/core/states/schema.py` — track reducer per shared idx (`_reducers` list, add slot); update `_register`, `_register_shared_vars`, `_build` (~40 LOC)
- `operonx/core/states/cell.py` — `__slots__` addition if we cache reducer on Cell (else skip) (~5 LOC)
- `operonx/core/ops/graph/graph_op.py` — `.declare()` method (rename `.shared()`), slot addition for `_reducers` (~40 LOC)
- `operonx/reducers.py` — new module: `add_messages`, `dict_merge`, `RemoveMessage` sentinel (~50 LOC)
- Tests (~40 LOC)

**~210 LOC** (was 100 in v2, correction from review). Python-only.

---

## Phase 2 — Unified write funnel + Checkpointer + step_id + InterruptOp/EmitOp + streams

### Concept

**Unify all cell mutations behind a single `_write_cell` funnel.** Every write to `state._cells` (op outputs, push-refs, SCRATCH) routes through it. Multiple observers subscribe: runtime scheduler (existing), reducer (Phase 1), checkpointer, tracer.

```
                          ┌──────────────────────────┐
   op returns             │                          │
   {"x": 5} ──────► state._write_cell(idx, ctx, v) ──┤
   SCRATCH["k"]=v         │                          │
   push-ref hop           └───────────┬──────────────┘
                                      │ fan-out
        ┌─────────────┬───────────────┼──────────────┬──────────────┐
        ▼             ▼               ▼              ▼              ▼
   Runtime      Reducer (P1)     Checkpointer      Tracer     [Future subs]
   dispatch     merge-on-write   capture write     enrich     (custom)
```

Consequences:

- **Every cell write is captured** — no distinction between "declared" and "transient". Every mutation is a state change worth recording.
- **step_id increments per write batch** (a batch = writes committed together at the end of one op invocation). No "wave / super-step" abstraction — that's a Pregel concept operonx's event-driven scheduler doesn't have.
- **Reducers apply BEFORE capture** — checkpoint records the post-merge value, matching what readers see.
- **No `put_writes` needed** — since every write is captured atomically, crash-safety is built-in (log survives up to last committed write).

Two new first-class ops surface HITL and custom telemetry:

- **`InterruptOp`** — an op that suspends the scheduler at its dispatch, emits its payload to the caller, waits for `run.resume(value)`, completes with the answer as its output. Visible node in the graph.
- **`EmitOp`** — an op that pushes a payload to the caller's `mode="custom"` stream, then completes. Visible node, not a hidden side-effect.

### Step boundary — per write batch, not per wave

Operonx's scheduler is event-driven per-frame; there's no natural "super-step" barrier. A **write batch** = all cell writes committed at the end of a single op invocation (returns dict + push-refs it triggers + SCRATCH writes done during the body). `step_id` increments once per batch.

This means:

- One op completion = one step_id increment
- Parallel ops that complete simultaneously = separate step_ids (event ordering, not batching)
- Loop-body iterations from Phase 3 continue the outer step_id via a parent-passed counter — nested schedulers don't reset
- No "waves" or "quiescence" concept — those were LangGraph/Pregel imports that don't fit the actual scheduler

Alternative rejected: per-write step (too fine — 5-10× more steps than needed). Alternative rejected: quiescence-based (arbitrary — depends on how fast events happen to arrive, not on semantics).

### Interface

```python
from operonx.checkpoint import InMemoryCheckpointer, SqliteCheckpointer
from operonx import InterruptOp, EmitOp

cp = InMemoryCheckpointer()      # captures every emitted cell write + scratch write
run = graph.run(inputs={...}, checkpointer=cp)

# Snapshots — everything the funnel emits (no scope config)
cp.get_state(step=3)         # full snapshot at step 3 (fold deltas from step 0)
cp.get_updates(step=3)       # delta only
cp.list_steps()              # [0, 1, 2, 3, ...]

# Optional observer-specific sampling for long runs
cp = InMemoryCheckpointer(sample_every=10)   # every 10th step only

# NOTE: to filter what's observed, declare it on the op — NOT on the checkpointer.
# Two knobs: `exclude` (blocklist) and `include` (allowlist). Both accept list-or-dict.

@op(exclude=["tokens", "raw_audio"])         # both trace + checkpoint skip these vars
def transcribe(audio):
    return {"text": "hello", "tokens": [...], "raw_audio": b"..."}
    # → text is captured; tokens + raw_audio are filtered at the funnel

@op(include=[])                              # silence entire op — allowlist of nothing
def route(...): ...

@op(exclude={"trace": ["user_pii"],          # per-observer split (dict form)
             "checkpoint": ["tokens"]})
def process(): ...

@op(include=["summary"])                     # allowlist mode — only "summary" observed
def summarize(): ...

# Circuit breaker — safety net against runaway ops (e.g. frame_source at 100Hz)
@op(observe_max=10_000)                      # raise ObserveBudgetExceeded if this op emits >10k events per run
def frame_source():
    for i in range(...):
        yield {"frame": ...}

# Run-level default cap
run = graph.run(inputs=..., observe_max=100_000)   # applied to any op without its own

# Streaming
run.stream(mode="values")    # full state each step
run.stream(mode="updates")   # delta each step
run.stream(mode="frames")    # per-yield frames from generator ops (LLM tokens, streaming audio)
run.stream(mode="custom")    # events emitted by EmitOp instances in the graph

# HITL — a first-class node in the graph, not a hidden callable
@graph
def with_approval():
    call    = call_model(...)
    approve = InterruptOp(payload=call["plan"])       # ← suspends here
    proceed = act_on(plan=approve["response"])        # ← runs after resume

    START >> call >> approve >> proceed >> END

# Caller side:
for evt in run.stream(mode="updates"):
    if isinstance(evt, InterruptEvent):
        answer = ask_human(evt.payload)
        run.resume(answer)

# Custom telemetry — also a first-class node, not a hidden emit() call
tell_ui = EmitOp(payload=call["progress"], channel="ui")
```

### Design constraints

| Constraint | Reason |
|---|---|
| Opt-in — no checkpointer = zero cost | Hot path stays clean |
| Captures every cell write + every SCRATCH write | Uniform, no arbitrary "declared vs transient" split |
| Delta storage (not full snapshots) | O(cells-changed) per step vs O(all-cells·S) |
| `get_state(step)` = replay from step 0 folding deltas | Small extra CPU on read, big win on write |
| step_id monotonic across nested schedulers | Loop-body iterations continue outer step_id — fixes Phase 3 interaction |
| Reducers apply BEFORE checkpoint capture | Checkpoint records the post-merge value that readers see |
| `InterruptOp` suspends the scheduler at its dispatch, not inside another op's body | Bodies stay atomic; suspension point is a visible graph node |
| Generator ops: cell writes commit on op completion, yields surface via `mode="frames"` | Predictable steps; per-frame events are a separate stream |
| Cancelled ctxs: checkpointer receives `on_cancel(ctx)`; log survives, no rewind — the cancellation itself is a state event | Auditable; matches callbot's `Interrupt(ctx_to_cancel=…)` pattern |
| Crash-safety: log survives up to the last committed write | Since every write is captured atomically, no `put_writes` needed |
| Non-serializable state (closures, live objects) | SqliteCheckpointer raises `CheckpointSerializationError` at put time; user pins a serializer or uses `InMemory` |

### Error cases

| # | Case | Behavior |
|---|---|---|
| E1 | `get_state(step=99)` on 5-step run | `StepNotFound(step=99, max_step=5)` |
| E2 | Checkpointer raises during put | Warn + drop that snapshot, run continues |
| E3 | Multiple `InterruptOp` nodes fire in same wave (parallel branches) | Each emits its own `InterruptEvent`; caller resumes each independently by `interrupt_id` |
| E4 | `resume()` without any pending `InterruptOp` | `NoActiveInterrupt` error |
| E8 | `EmitOp` called with no `mode="custom"` subscriber | Payload dropped silently (no back-pressure — telemetry is best-effort) |
| E5 | Non-JSON-serializable value in Sqlite backend | Build-time check on `.declare()` types when Sqlite is bound; runtime `CheckpointSerializationError` else |
| E6 | Cancelled ctx that already committed to checkpointer | `on_cancel(ctx)` emits an inverse delta at current step; prior snapshots untouched (audit trail preserved) |
| E7 | Concurrent `run()`s share a checkpointer | Isolate by `thread_id` (LangGraph parity); default `thread_id="default"` for single-run use |

### Scope

- `operonx/core/states/state.py` — extract `_write_cell` funnel; route `__setitem__` + push-ref direct writes + SCRATCH writes through it; observer registration API (~90 LOC)
- `operonx/checkpoint/base.py` — `Checkpointer` protocol + `InterruptEvent`, `CustomEvent`, `StepEvent`, `CellWriteEvent` (~50 LOC)
- `operonx/checkpoint/memory.py` — `InMemoryCheckpointer` subscribes to write events (~90 LOC)
- `operonx/checkpoint/sqlite.py` — `SqliteCheckpointer` (~120 LOC — deferred to 0.13.1 if scope pressure)
- `operonx/checkpoint/filters.py` — observer-specific sampling (`sample_every`) only; op/var filtering lives on `@op` (~15 LOC)
- `operonx/core/ops/transform/func_op.py` — `@op(exclude=..., include=..., observe_max=...)` kwargs; mutual-exclusion check; dict/list normalization (~50 LOC)
- `operonx/core/states/state.py` — funnel-level filter check per observer key; observe-count tracking + `ObserveBudgetExceeded` raise (~40 LOC)
- `operonx/checkpoint/base.py` — `ObserveBudgetExceeded` exception (~5 LOC)
- `operonx/core/ops/flow/interrupt_op.py` — `InterruptOp` node + scheduler-side suspend/resume hook (~70 LOC)
- `operonx/core/ops/flow/emit_op.py` — `EmitOp` node + custom-stream push (~30 LOC)
- `operonx/core/ops/graph/task_scheduler.py` — step_id emission per write batch, checkpointer hooks, `on_cancel`, InterruptOp suspend, EmitOp dispatch (~120 LOC)
- `operonx/core/runtime/dispatcher.py` — stream modes (`values`, `updates`, `frames`, `custom`), plumb through `run()` and `resume()` (~80 LOC)
- Tests (~90 LOC — including HITL flow, cancel handling, multi-mode stream, EmitOp fan-out, filter presets)

**~770 LOC** in 0.13.0 (unified funnel + checkpointer + interrupt + streams + `@op(exclude=/include=)` filter + `observe_max` circuit breaker), **+120 LOC** in 0.13.1 (Sqlite backend).

Cost is real but pays for itself: one mutation path serves runtime + reducer + checkpointer + tracer + any future observer. No dual code paths, no partial-capture gaps.

---

## Phase 3 — Cyclic edges (Level 2 rewrite)

### The rewrite

User writes:

```python
@graph
def react_agent():
    PARENT.declare(messages=[], tool_calls=None,
                   reducers={"messages": add_messages})

    call  = call_model(messages=PARENT["messages"])
    tools = run_tools(tool_calls=call["tool_calls"])

    call["messages"]   >> PARENT["messages"]
    call["tool_calls"] >> PARENT["tool_calls"]
    tools["messages"]  >> PARENT["messages"]

    START >> call >> if_(PARENT["tool_calls"] == None, END).else_(tools)
    tools >> call         # ← back-edge; would normally fail validate()
```

Build-time pass detects the back-edge and rewrites internally:

```
    Before rewrite (as user wrote it):

    START ──► call ──► if ──END
                 ▲      │
                 │      ▼
                 └─── tools

    After rewrite (what scheduler sees):

    START ──► [GraphOp.loop(hidden_loop_0)] ──► END
                    │  (inside)
                    ▼
                    START' ──► call ──► if ──► END'
                                 ▲      │
                                 │      ▼
                                 └─── tools
```

### Detection algorithm (fixed from v2)

**Critical fix**: v2 said "topo-sort, back-edge = topo(u) > topo(v)". Topo-sort **fails** on cyclic input — the very case the pass exists to handle. Correct algorithm:

1. Use existing `find_cycles` DFS from [`operonx/core/utils/algo.py:9`](../../operonx/core/utils/algo.py) — colour-based (WHITE / GRAY / BLACK) — detects back-edges as GRAY-target edges during traversal
2. For each back-edge `(u, v)`, run **Tarjan's SCC** on the subgraph rooted at `v` to extract the loop body (~40 LOC of net-new code)
3. Verify loop body: **exactly one entry** (v), **at least one exit** via `if_(cond, END)` — algorithm now matches E-table (v2 said "exactly ONE exit" but E3 allowed multiple; reconciled)
4. Extract SCC into a hidden `GraphOp.loop(_max_iters=…)` node
5. Replace SCC nodes in outer graph's `_ops` / `_edges` / `prevs` / `nexts` with the loop node
6. **Re-cache `full_name`** on all extracted ops (post-tree-restructuring invariant — `graph_op.py:579-585`)
7. Recurse: extracted body may itself contain nested cycles

### Pass ordering (fixed from v2)

**Critical fix**: v2 had `auto_soft_edges → cyclic_to_loop`. Auto-soft walks branch ancestors and assumes acyclic input — on cyclic user graphs it computes wrong branch signatures. Correct order:

1. **`cyclic_to_loop`** — rewrite cycles to loop nodes → resulting graph is a DAG
2. **`_auto_soften_edges`** — runs on the DAG, correctness restored
3. **`reducer_bind`** — annotate cells
4. **`validate()`** — final DAG check

### Cyclic input silent-rewrite: acknowledgment

Today, cyclic input **errors at build**. Post-0.14, an accidental back-edge (typo in wiring) is **silently rewritten into a loop**. This is a behavior change dressed as backward compat.

Mitigation: `@graph(strict_dag=True)` decorator opt-in that disables the rewrite pass and preserves today's fail-fast behavior. Default off (opt-in strictness).

### Public API change

- `with GraphOp.loop(...)` → **deprecated** with warning, redirects to synthesized form
- Kept as internal primitive (rewrite pass emits `GraphOp.loop` under the hood)
- One code path in the scheduler

### Ctx-per-iteration is preserved

Level 2 rewrite is **transparent to runtime state**. The synthesized `GraphOp.loop` runs the same way as today — each iteration gets its own ctx tuple (`("main", "loop[0]")`, `("main", "loop[1]")`, …). Cells still index per iteration:

```python
state[(call, "messages", ("main", "loop[2]"))]   # iter 2's messages, direct access
```

Three ways to access per-iteration values after the refactor:

1. **Direct cell access** — unchanged from today, `state[(op, var, ctx)]`
2. **Checkpointer** — every write event carries its ctx; filter by ctx to see one iteration's writes: `cp.get_writes(ctx=("main","loop[2]"))`
3. **Reducer aggregation** — `PARENT["messages"]` at DEFAULT_CONTEXT holds the reducer-merged value across all iterations

The rewrite changes DAG shape (visible back-edge instead of `with` block), not execution semantics. Anything you can inspect today, you can still inspect — plus you get checkpointer visibility for free.

### Depends on Phase 1 (fixed claim from v2)

**Correction from review**: v2 claimed "each phase ships independently". Phase 3's canonical example (`messages` accumulating across loop iterations) requires reducers from Phase 1 — without them, `messages` is clobbered iter-to-iter and the loop is functionally broken.

Order: Phase 1 → Phase 3 (Phase 2 can slot before or after Phase 3 independently).

### Error cases

| # | Case | Behavior |
|---|---|---|
| E1 | Back-edge without terminating branch inside loop | Build-time: `"cycle {u,v,w} has no exit — would loop forever"` |
| E2 | Multiple entries into cycle body | Build-time: `"cycle body has 2+ entries; extract entry into a merge node"` |
| E3 | Multiple exits from cycle body | Allowed — synthesize multiple END' handlers in the hidden loop |
| E4 | Back-edge crosses subgraph boundary | Build-time: `"cyclic edges cannot cross @graph boundary"` |
| E5 | Nested cycles | Recurse on inner SCC first, then outer |
| E6 | Infinite iteration at runtime | `_max_iters` on synthesized loop (default 1000, configurable via `PARENT.declare(_max_iters=N)`) |
| E7 | Self-loop (`a >> a`) | Legal — loop body of one op; SCC is `{a}` |
| E8 | Two back-edges to different entry nodes | E2 triggers (multiple entries) — must merge before entering the cycle |
| E9 | Inline `if_/else_` inside loop body (uses `route_N` auto-name from 0.11.0) | Auto-generated branch is part of the SCC; its `>> END` counts as loop exit |

### Scope

- `operonx/core/ops/graph/cycle_rewrite.py` — new module: DFS back-edge detection (reuse `find_cycles`), Tarjan SCC (~40 LOC net-new), extraction, `full_name` re-cache (~200 LOC)
- `operonx/core/ops/graph/graph_op.py` — hook into `build()` **before** validate + auto_soft, slot for `_rewritten_from` (~30 LOC)
- `operonx/core/ops/graph/validation.py` — relax cycle check for input graph, keep for post-rewrite DAG (~20 LOC)
- Deprecation warnings on `GraphOp.loop` public API (~10 LOC)
- Tests (~80 LOC — 9 error cases + happy paths + nested + auto_soft interaction)

**~340 LOC** (was 250 in v2, correction from review — added Tarjan + full_name re-cache).

---

## On `Command` — deliberate omission

LangGraph's `Command` bundles four things: `resume=value` (feed answer to `interrupt()`), `update={...}` (modify state on resume), `goto="node"` (dynamic routing), `graph=Command.PARENT` (subgraph escape).

Operonx has a first-class equivalent for each — cleaner because it stays DAG-visible:

| LangGraph `Command` field | Operonx equivalent |
|---|---|
| `resume=value` | `run.resume(value)` — bare arg, no wrapper |
| `update={...}` | Wire a normal op after `InterruptOp` that reads `approve["response"]` and writes to state via push-ref (visible in graph body) |
| `goto="node"` | `BranchOp` / `if_ / else_` at wiring time (already rejected `goto` — §Rejected #12) |
| `graph=Command.PARENT` | `END` sentinel + `@graph` composition (already exists) |

Concrete comparison — "approve but edit the plan":

```python
# LangGraph — state edit hidden inside Command
run.resume(Command(resume=True, update={"tool_calls": edited_plan}))

# Operonx — state edit is a visible node
approve = InterruptOp(payload=call["tool_calls"])
edit    = apply_edit(response=approve["response"])
edit["plan"] >> PARENT["tool_calls"]

run.resume(True)
```

**Command dropped from the plan.** `run.resume(value)` takes a bare value. Future features (multi-interrupt batch resume, timeout override) can add optional kwargs to `run.resume()` without introducing a wrapper class.

---

## On SCRATCH — deliberate no-op

**SCRATCH is not being changed by this plan.** Reason:

SCRATCH already IS the "in-body flexible state" accessor we designed earlier (`STATE["k"]`) and then dropped. Adding `SCRATCH.declare()` would create two declaration APIs (`PARENT.declare` + `SCRATCH.declare`), which the plan rejected as confusing.

Two channels stay:

| | `PARENT` | `SCRATCH` |
|---|---|---|
| Declaration | ✓ via `.declare()` (P1) | ✗ ambient |
| Reducers | ✓ (P1) | ✗ hand-rolled |
| Checkpointer visibility | ✓ (P2) | ✗ invisible |
| DAG visibility | ✓ | ✗ |
| Use case | State the graph reasons about | Ambient/imperative side-channel |

Callbot's three hand-rolled SCRATCH workarounds (OR-latch on `is_final`, string concat on `pending_user_text`, retry counter on `intent_retry_counts`) become **per-callsite migration decisions**: keep as-is (works, ugly) or migrate the value to `PARENT.declare()` + reducer (safe, wired). Not a framework problem.

**No new SCRATCH API in this plan.**

---

## Migration timeline

```
┌──────────────────────────────────────────────────────────────────┐
│  operonx 0.12.0 — Phase 1                                        │
│    + PARENT.declare(**vars, reducers=...)                        │
│    + operonx.reducers module (add_messages, dict_merge, ...)     │
│    * PARENT.shared() deprecated (works, warns)                   │
│                                                                  │
│  operonx 0.13.0 — Phase 2                                        │
│    + Checkpointer protocol + InMemoryCheckpointer                │
│    + interrupt() + Command + stream modes                        │
│    + step_id emission at quiescence                              │
│                                                                  │
│  operonx 0.13.1 — Phase 2 completion                             │
│    + SqliteCheckpointer                                          │
│                                                                  │
│  operonx 0.14.0 — Phase 3 (depends on Phase 1)                   │
│    + Cyclic edges (Level 2 rewrite)                              │
│    + @graph(strict_dag=True) opt-out                             │
│    * GraphOp.loop public API deprecated (works, warns)           │
│                                                                  │
│  operonx 1.0.0 — cleanup                                         │
│    - PARENT.shared() removed                                     │
│    - GraphOp.loop.__init__ made private (_GraphLoop)             │
└──────────────────────────────────────────────────────────────────┘
```

**Phase order matters**: 1 → 2 → 3. Phase 3 depends on Phase 1 (reducers make loop-iter accumulation work). Phase 2 is independent of Phase 3 but can ship after either.

Callbot / consumers upgrade at their own pace. Nothing forces a rewrite until 1.0.0.

---

## Full end-to-end example (all three phases)

Canonical LangGraph ReAct agent, in post-refactor operonx:

```python
from operonx import graph, op, PARENT, if_, START, END, InterruptOp, EmitOp, Command
from operonx.reducers import add_messages
from operonx.checkpoint import InMemoryCheckpointer

@op
def call_model(messages):
    resp = llm.invoke(messages)
    return {"messages": [resp], "tool_calls": resp.tool_calls}

@op
def run_tools(tool_calls):
    results = [tool(**tc.args) for tc in tool_calls]
    return {"messages": [ToolMessage(r) for r in results], "tool_calls": None}

@graph
def react_agent():
    PARENT.declare(
        messages=[],
        tool_calls=None,
        reducers={"messages": add_messages},
    )

    call    = call_model(messages=PARENT["messages"])
    approve = InterruptOp(payload=call["tool_calls"])       # HITL — visible node
    telem   = EmitOp(payload=call["tool_calls"], channel="tool_plan")   # custom telemetry
    tools   = run_tools(tool_calls=approve["response"])

    call["messages"]  >> PARENT["messages"]
    tools["messages"] >> PARENT["messages"]

    START >> call >> if_(
        call["tool_calls"] == None, END
    ).else_(telem)                          # emit telemetry first
    telem >> approve                        # then wait for human
    approve >> if_(
        approve["response"] == True, tools
    ).else_(END)
    tools >> call    # back-edge — Phase 3 rewrites to loop
```

Every control-flow decision — reads, writes, branches, pauses, telemetry emissions — is a visible node or edge in the graph body. No hidden body-side magic.

Run + HITL + inspect:

```python
cp = InMemoryCheckpointer()
run = react_agent.run(
    inputs={"messages": [HumanMessage("what's the weather?")]},
    checkpointer=cp,
)

for evt in run.stream(mode="updates"):
    if isinstance(evt, InterruptEvent):
        approved = input(f"Approve plan? {evt.payload}: ") == "y"
        run.resume(Command(resume=approved))
    else:
        print(f"step {evt.step}: {evt.delta}")

# post-run inspection
state_at_3 = cp.get_state(step=3)
```

Every data path visible in the graph body. Every state cell declared once. Every loop iteration a distinct step. HITL is a one-line primitive.

---

## Rollback strategy per phase

| Phase | Rollback if regressions surface |
|---|---|
| 1 | Revert `_write_cell` reducer branch; `.declare()` becomes alias for `.shared()` |
| 2 | Default `checkpointer=None`; scheduler emits step_id as no-op; `InterruptOp` raises `NotImplementedError` unless checkpointer bound; `EmitOp` drops payload if no subscriber |
| 3 | Feature-flag: `@graph` decorator defaults to `strict_dag=True` (disables rewrite pass — today's behavior) |

All three have a clean off-switch.

---

## What this does NOT change

- **DAG scheduler** — post-rewrite graph is still a DAG
- **(op, var, ctx) cell model** — untouched. Return dicts still write to op-local outputs; shared writes still go via push-ref (now with reducer branch)
- **Tracing V3** — step_id becomes a new field; existing spans unchanged
- **ctx-tuple discipline** — still used for parallel fan-out (map/for)
- **Generator ops + yield semantics** — untouched
- **`.parallel()` / `.collect()`** — untouched
- **BranchOp, `if_/else_` inline API, auto-soft-edge** — untouched, compose cleanly (auto-soft now runs AFTER cycle rewrite)
- **`PARENT[...]` access syntax** — kept
- **SCRATCH accessor** — kept, unchanged (§On SCRATCH)

---

## Design decisions explicitly rejected

Each proposed and dropped during design review:

1. **`STATE["k"]` accessor inside FuncOp bodies.** Hides data flow — you'd have to open every op body to see what it reads/writes. Breaks operonx's "read the graph body → know the DAG" invariant. **(Note: SCRATCH already provides this pattern for cases that opt into ambient state.)**

2. **`STATE` sentinel at wiring sites** (`tick(count=STATE)`). Saves one word over `PARENT["count"]`, adds a new symbol. Not worth the surface growth.

3. **Auto-wire by name-match** (op returns `"count"` + `PARENT.declare(count=0)` → auto push_ref). Silent behavior tied to name collisions — same anti-magic reasoning as #1.

4. **`SCRATCH.declare()`.** Duplicates `PARENT.declare()` as a second declaration API. One channel gets declaration + reducers; the other stays ambient. User migrates specific values to the declared channel if they want the safety net.

5. **Full Pregel scheduler rewrite.** Rejected in `LOOP_DESIGN_DISCUSSION.md` §5. Phase 3 (Level 2 rewrite) gets us LangGraph syntax without the cost.

6. **Per-variable timestamps in schema.** Wrong axis — snapshots belong in the checkpointer, not the schema.

7. **Cross-graph state access** (nested op reading an outer graph's shared cell that's not the immediate PARENT). Nearest-enclosing-graph handles 95% of cases today.

8. **Rust runtime parity for reducers.** Callbot moved off Rust (Python-only pipeline). Rust concerns dropped from scope entirely.

9. **LangGraph checkpointer tree model** (`thread_id`, `checkpoint_id`, `parent_checkpoint_id` for fork/time-travel). Plan's `step_id` is a monotonic int. Time-travel-and-edit is out; if a user needs it, they can pin a checkpointer to a `thread_id` and manually branch runs. Full fork tree = follow-up plan.

10. **`interrupt()` callable inside op body** (LangGraph-style). Same class of magic as `STATE["k"]` — hidden control flow inside the body. Replaced by `InterruptOp` as a visible graph node.

11. **`emit(event)` callable inside op body** for `mode="custom"` streaming. Same reason. Replaced by `EmitOp` as a visible graph node.

12. **`Command(goto="node_X", update={...})` dynamic routing return-value.** Op decides its downstream target at runtime — hides control flow. Use `if_/else_` on visible conditions instead. (Static `Command(resume=...)` is fine — it's a caller-side resume payload, not an op-body magic.)

---

## Open questions for review

1. **`add_messages` contract.** Match LangGraph's full contract (id upsert + `RemoveMessage` + `REMOVE_ALL_MESSAGES`) or ship a simpler `append_messages` under a different name? **Recommendation: match LangGraph fully to avoid porting surprises.**

2. **Sqlite backend inclusion.** 0.13.0 with InMemory only, Sqlite in 0.13.1? Or ship both together in 0.13.0 (+120 LOC)? **Recommendation: split — InMemory is enough for local dev; Sqlite when persistence is a real ask.**

3. **`add_messages` for non-list types.** LangGraph reducers are typed via `Annotated`. Operonx has no annotation channel today. Ship reducer-per-key only (current plan) or add `Annotated[list, add_messages]`-style declaration too? **Recommendation: per-key only; annotations are a bigger design.**

4. **Multi-mode streaming — which modes ship in 0.13.0?** `values`, `updates`, `messages`, `custom` all in one release, or start with `updates` and add others? **Recommendation: all four — the wiring is shared, per-mode filter is trivial.**

5. **`thread_id` semantics for concurrent runs.** Default `"default"` for single-run case, required for multi-run? **Recommendation: default to `"default"`, warn on collision with active run.**

---

## Total scope + estimate

| Phase | LOC | Ships in | Depends on |
|---|---|---|---|
| 1 · `.declare()` + reducers + reducer library | ~210 | 0.12.0 | — |
| 2a · Unified `_write_cell` funnel + Checkpointer + step_id + InterruptOp + EmitOp + 4 stream modes + `@op(exclude/include/observe_max)` filter | ~770 | 0.13.0 | Phase 1 (recommended) |
| 2b · SqliteCheckpointer | ~120 | 0.13.1 | 2a |
| 3 · Cyclic edges (Tarjan SCC + full_name recache) | ~340 | 0.14.0 | **Phase 1 (required — reducers enable iter accumulation)** |
| **Total** | **~1440** | **4 minor versions** | |

(v2 estimate was 550 LOC — corrected upward after adversarial review found underestimated scope in Phase 2 wave abstraction, missing Tarjan in Phase 3, and dropped Phase 2 features required for HITL story.)

Test coverage target: >90% branch coverage per phase.

---

## Decision points for the reviewer

- [ ] Phase order 1 → 2 → 3, with Phase 3 hard-depending on Phase 1 — OK?
- [ ] `PARENT.declare()` name (confirmed prior turn) — final?
- [ ] SCRATCH untouched — confirmed?
- [ ] Fold Phase 2 to include `InterruptOp` + `EmitOp` + streams (not defer to 2.5) — OK?
- [ ] Unified `_write_cell` funnel with runtime/reducer/checkpointer/tracer as subscribers (+90 LOC vs dual-path) — OK?
- [ ] Checkpointer captures EVERY emitted cell write by default (no `scope=`); filter via `@op` — OK?
- [ ] Filter API: `@op(exclude=[list]|{dict})` + `@op(include=[list]|{dict})` + `observe_max=N` circuit breaker; dict form allows per-observer split — OK?
- [ ] `observe_max` default is disabled (None); users opt in per-op or set global run-level default — OK?
- [ ] Silence-op idiom is `@op(include=[])` (allowlist of nothing) — clear enough, or add a `@op(silent=True)` shortcut?
- [ ] `add_messages` matches LangGraph contract fully — OK?
- [ ] `@graph(strict_dag=True)` opt-out for cycle rewrite (preserves fail-fast for typo protection) — OK?
- [ ] Sqlite deferred to 0.13.1 (InMemory only in 0.13.0) — OK?
- [ ] Any of the 9 explicitly-rejected designs to reopen?

Once approved, Phase 1 ships as an isolated PR against operonx.

---

## Design invariant preserved throughout

**"Read the graph body → know the DAG."** Every control-flow decision, state read, state write, branch, pause, and telemetry emission must be a visible node or edge in the graph body — never a hidden callable inside an op body.

Applied consistently:

| Concept | Body-side (rejected) | Graph-side (adopted) |
|---|---|---|
| Shared state read | `STATE["k"]` in body | `PARENT["k"]` ref at wiring |
| Shared state write | `STATE["k"] = v` in body | `op["k"] >> PARENT["k"]` at wiring |
| Human-in-the-loop pause | `interrupt(payload)` in body | `InterruptOp(payload=...)` as node |
| Custom telemetry event | `emit(event)` in body | `EmitOp(payload=..., channel=...)` as node |
| Dynamic routing | `return Command(goto="X")` from body | `if_/else_` on visible condition |
| Loop | `with GraphOp.loop(...)` wrapper | Back-edge in graph body (Phase 3 rewrites) |
| Observability filter | Separate configs per observer (Checkpointer vs Tracer) | Single source at emission: `@op(exclude=... | include=...)`; list form = both observers, dict form = per-observer split. Runaway safeguard: `observe_max=N`. Per-observer sampling stays on the observer. |
| Step boundary | Wave / super-step / quiescence | Per-write-batch (per op invocation) — matches event-driven scheduler |

**One deliberate exception**: SCRATCH is body-side by design (§On SCRATCH). Kept as-is because (a) it exists and works, (b) migration cost is real, (c) it fills a legitimate niche for ambient/imperative state the graph doesn't need to reason about. The trade-off is acknowledged, not accidental.

## Change log vs v2

- **Fixed B1** — reducer applied at cell-level write, not `__setitem__` (would have silently skipped push-ref writes)
- **Fixed B3** — DFS back-edge detection (topo-sort was wrong; topo fails on cyclic input)
- **Fixed B4** — pass order reversed: `cyclic_to_loop` runs BEFORE `_auto_soften_edges`
- **Fixed B5** — step_id monotonic across nested schedulers (via parent-passed counter)
- **Corrected S1** — Phase 2 scope up from 200 → 520 LOC (no wave abstraction to piggyback on)
- **Corrected S2** — Phase 3 scope up from 250 → 340 LOC (Tarjan SCC + `full_name` recache)
- **Dropped** — Rust reducer parity concern (callbot moved off Rust)
- **Added** — `InterruptOp` + `EmitOp` (first-class graph nodes, not body-side callables) + `put_writes` + 4 stream modes to Phase 2 (HITL is essential, not deferrable)
- **Added** — `on_cancel(ctx)` for callbot's speculative-chain cancellation pattern
- **Added** — `add_messages` must match LangGraph contract (id upsert + `RemoveMessage`) or rename
- **Added** — `@graph(strict_dag=True)` opt-out for cycle rewrite (accidental-back-edge safety)
- **Clarified** — Phase 3 depends on Phase 1 (v2 wrongly said "phases ship independently")
- **Clarified** — SCRATCH untouched, no `.declare()`, callbot workarounds are per-callsite migration decisions
- **Added E-cases** — E5-E7 in Phase 1 (`.declare()` at top level, one call max, ctx-collapse for streams); E6-E9 in Phase 3 (self-loop, multi-entry, inline `if_` interaction); E8 in Phase 2 (EmitOp with no subscriber)

## Change log vs v3.0 (this patch)

- **Swapped** `interrupt()` callable → `InterruptOp` first-class node
- **Swapped** `emit()` callable → `EmitOp` first-class node
- **Added** §"Design invariant preserved throughout" — codifies the body-magic-is-bad rule that drove both swaps, so future proposals can be auto-rejected against the same criterion
- **Added** §Rejected #10, #11, #12 — `interrupt()` body-callable, `emit()` body-callable, `Command(goto=...)` dynamic routing
- **Full end-to-end example rewritten** to show `InterruptOp` + `EmitOp` as visible nodes

## LangGraph concepts NOT adopted (audit)

Operonx borrows real ideas from LangGraph but the model is different (event-driven scheduler, `(op, var, ctx)` cells, generator ops with `.parallel/.collect`). This audit documents concepts that got imported earlier and later dropped as bad fit:

| LangGraph concept | Why it doesn't fit operonx | Operonx alternative |
|---|---|---|
| `Command(goto=...)` dynamic routing | Op deciding downstream at runtime hides control flow | `BranchOp` / `if_ / else_` at wiring time |
| `Command` wrapper class | 4 fields; all 4 have DAG-visible operonx equivalents | Bare `run.resume(value)` + wired ops for state edits |
| `interrupt()` callable in body | Hidden control flow inside op body | `InterruptOp` as visible graph node |
| `emit()` callable in body | Same as `interrupt()` — hidden side effect | `EmitOp` as visible graph node |
| `STATE["k"]` accessor in body | Hides data flow | `PARENT["k"]` refs at wiring |
| `scope="declared"` filter on checkpointer | "Declared" is a wiring convenience, not a semantic marker of importance | Capture every cell write; filter by op patterns / sampling if size matters |
| Wave / super-step barrier | Operonx scheduler is event-driven per-frame; there's no natural wave | step_id per write batch (per op invocation) |
| `put_writes` for partial-wave persistence | Needed because LangGraph batches writes within a wave; operonx captures per-batch atomically | Not needed |
| `thread_id` for concurrent-run isolation | One `run()` = one `MemoryState` = one checkpointer in operonx | One checkpointer per run; parallel runs use separate checkpointers |
| Checkpoint tree (`parent_checkpoint_id` for time travel + forks) | Real feature but big design; not v1 material | Deferred; user pins per-`thread_id` checkpointer manually if needed |
| Stream mode name `"messages"` | Operonx isn't message-oriented; generator ops yield "frames" (audio chunks, LLM tokens, arbitrary) | Renamed to `"frames"` |
| `Annotated[list, reducer_fn]` reducers in TypedDict | Requires class-based state; operonx uses dict-form declaration | `PARENT.declare(x=[], reducers={"x": fn})` |

Concepts adopted from LangGraph that DO fit:

- Reducers on shared cells (`Phase 1`)
- Checkpointer as an opt-in observer
- HITL as a first-class primitive (via `InterruptOp`)
- Cyclic edge syntax (via `Phase 3` Level-2 rewrite)
- `add_messages`-style domain reducer (shipped but optional)

## Change log vs v3.1

- **Dropped `Command` wrapper class entirely** — `run.resume(value)` takes a bare answer
- **Added §"On Command"** — root-cause analysis of LangGraph's Command showing operonx has cleaner, DAG-visible equivalents for all 4 of its fields (`resume`, `update`, `goto`, `graph=PARENT`)
- **Small scope reduction**: `operonx/checkpoint/base.py` from ~60 → ~50 LOC (no `Command` class); Phase 2 total drops from ~520 → ~510 LOC

## Change log vs v3.2 (this patch — v3.3)

- **Unified `_write_cell` funnel** — all cell mutations route through one path; runtime, reducer, checkpointer, and tracer are subscribers to the same event stream (+90 LOC to Phase 2, replaces dual-path complexity)
- **Checkpointer captures EVERY cell write** (plus SCRATCH) — no `scope=` config, no distinction between "declared" and "transient". Every mutation is a state change.
- **Dropped `scope="declared"/"all"` toggle** — imported from LangGraph's channel model, doesn't fit operonx's uniform cell model. "Declared" is a wiring convenience, not a semantic marker.
- **Dropped "wave / super-step / quiescence" concept** — Pregel baggage; operonx scheduler is event-driven. Replaced with `step_id increments per write batch = per op invocation`.
- **Dropped `put_writes`** — subsumed by per-write atomic capture.
- **Dropped `thread_id` for concurrent-run isolation** — not v1 material; one run = one checkpointer for now.
- **Renamed stream mode `"messages"` → `"frames"`** — operonx generator ops yield frames (audio chunks, LLM tokens, arbitrary), not messages specifically.
- **Added §"LangGraph concepts NOT adopted"** — audit table documenting every LangGraph concept considered and its verdict, so future contributors don't re-import bad-fit ideas.
- **Reducers apply BEFORE checkpoint capture** — recorded value is post-merge, matches what readers see.
- **Cancellation semantics simplified** — `on_cancel(ctx)` is a state event recorded in the log (auditable trail); no "rewind" attempt, since prior writes are legitimate history.
- **Filter API** — kept minimal at checkpointer level (`sample_every` only); op/var filtering moved to `@op(observed=..., exclude_vars=[...])` — see v3.3.1 patch below.
- **Scope revision**: Phase 2 up from ~520 → ~660 LOC (funnel extraction ~90 LOC, offset by dropped complexity elsewhere). Total plan: ~1190 → ~1330 LOC.

## Change log vs v3.3 (this patch — v3.3.1)

- **Unified observability filter at emission source, not per-observer** — `@op(observed=True|False, exclude_vars=[...])` controls both trace and checkpoint uniformly.
- **Removed `Checkpointer(exclude_ops=..., include_ops=...)`** — redundant with `@op(observed=False)`. Only `sample_every` remains on the checkpointer as a per-observer runtime policy.
- **Rationale documented**: declaration lives with the op (matches "read the graph body" invariant); observer-specific policy (sampling for cost/size) stays on the observer. No divergence, no duplicate mechanisms.
- **Trade-off acknowledged**: cannot say "trace but don't checkpoint" per var; if compliance ever demands it (PII case), add `exclude_from_trace=/exclude_from_checkpoint=` as an additive change.
- **Small scope shift**: `filters.py` shrinks from ~40 → ~15 LOC (sampling only); `@op` gains ~30 LOC for `observed`/`exclude_vars`; net +5 LOC. Phase 2: 660 → 685 LOC. Total plan: ~1330 → ~1335 LOC.

## Change log vs v3.3.1 (this patch — v3.3.2)

- **Collapsed filter API to two polymorphic knobs**: `@op(exclude=[list] | {dict})` and `@op(include=[list] | {dict})`.
  - List form applies to both observers; dict form (keys `"trace"`, `"checkpoint"`) splits per observer — resolves the earlier "can't do PII skip in trace only" trade-off natively.
  - Mutual exclusion at decoration time — using both `exclude=` and `include=` raises `ConfigError`.
- **Dropped `observed=True/False`** — redundant with `include=[]` (allowlist of nothing = op silenced).
- **Dropped `exclude_vars=[...]`** — subsumed by list form of `exclude=`.
- **Added `observe_max=N` circuit breaker** — hard runaway safeguard: per-op event cap; raises `ObserveBudgetExceeded` on exceed.
  - Motivated by callbot's `frame_source` (100Hz generator); silent sampling would hide the runaway bug. Loud failure forces explicit handling.
  - Default: `None` (disabled). Per-op override + global run-level default.
- **Scope**: Phase 2 up from ~685 → ~770 LOC (+85 for polymorphic filter + circuit breaker). Total plan: ~1335 → ~1440 LOC.
