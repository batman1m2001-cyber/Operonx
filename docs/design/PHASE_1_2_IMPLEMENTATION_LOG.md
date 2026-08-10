# Phase 1 + Phase 2 implementation log

**Status:** shipped on branch `feature/phase-2-checkpointer-hitl-stream` (off `dev`); **not yet pushed / merged**.
**Written:** 2026-08-09, before context compaction.
**Related plan:** [`STATE_LOOP_REFACTOR_PLAN.md`](STATE_LOOP_REFACTOR_PLAN.md) (v3.3.4). This log records what actually got built + adversarial-review-fixed on top of that plan.

---

## Snapshot

- **6 commits** on `feature/phase-2-checkpointer-hitl-stream` (from `dev` at `6876765`).
- **176 net-new tests** — 1156 pass total, 1 pre-existing unrelated failure (`test_llm_op.py::TestLLMOpAudio::test_audio_input` — external OpenAI 404), 23 skipped, **zero regressions across all six commits**.
- **Rust runtime: 0 lines touched** across the entire refactor (Python-only).
- **Callbot compatibility: unaffected today** — callbot pins `operonx>=0.11.0`, doesn't call `PARENT.shared()`, doesn't bind a checkpointer. Requires zero migration changes when it upgrades.

## Commit ladder

| Commit | Phase | Content | LOC delta |
|---|---|---|---|
| `b44ad27` | **1** | `PARENT.declare(**vars, reducers={...})` + reducers on shared-cell writes via extracted `_write_cell` funnel; `ReducerError`; stdlib reducers (`add_messages`, `dict_merge`, `RemoveMessage`, `REMOVE_ALL_MESSAGES`); `.shared()` deprecated alias | +2333 / -15 |
| `f6a08ca` | **2a** | `operonx/checkpoint/` package (Checkpointer Protocol + `InMemoryCheckpointer` + event types); `state.subscribe_writes/unsubscribe_writes`; `bind_checkpointer(state, cp, step_id_getter, op_registry=…)` bridge | 36 new tests |
| `39f6926` | **2b1** | `@op(exclude=/include=/observe_max=)` filter kwargs (polymorphic list-or-dict; mutually exclusive); `should_emit_for_channel` helper; `ObserveBudgetExceeded` circuit breaker via `op_registry`-honouring bridge | 37 new tests |
| `f5e22a7` | **2b2** | `EmitOp` (fire-and-forget custom events); `InterruptOp` (HITL suspend/resume via state futures); `state.advance_step()` called from `BaseOp.store_result`; `engine.invoke()` alias; `engine.stream(mode="updates"\|"values"\|"frames"\|"custom", channels=…)` | 36 new tests |
| `28ecbba` | **2b3 fix** | Fixed 3 real bugs + 3 hazards + 2 wire-ups (see §"Adversarial review outcomes") | — |
| `27208d6` | **2b3 test** | 21 regression tests locking in each 2b3 fix | 21 new tests |

## Key files added / modified

**New:**
- `operonx/reducers.py`
- `operonx/checkpoint/{__init__,base,memory,bridge}.py`
- `operonx/core/ops/flow/{emit_op,interrupt_op}.py`
- `tests/internal/test_reducers.py`
- `tests/internal/core/states/{test_declare_and_reducers,test_write_observer}.py`
- `tests/internal/checkpoint/{test_memory_checkpointer,test_bridge,test_bridge_filter,test_scratch_bus,test_phase_2b3_hardening}.py`
- `tests/internal/core/ops/test_op_observability_filter.py`
- `tests/internal/core/ops/flow/{test_emit_op,test_interrupt_op}.py`
- `tests/internal/core/engine/test_engine_checkpointer.py`

**Modified:**
- `operonx/core/states/state.py` — extracted `_write_cell` funnel; added `_observers`, `_scratch_observers`, `_custom_observers`, `_interrupt_observers`, `_interrupt_responses`, `_current_step` slots + subscribe/notify/resume APIs + `advance_step()`
- `operonx/core/states/schema.py` — `_reducers: Dict[int, callable]` slot, populated from `_reducer_vars` on the graph op
- `operonx/core/ops/_edges.py` — `PARENT.declare()` method; `PARENT.shared()` now emits `DeprecationWarning`; `ScratchAccessor.__setitem__` routes through `state._notify_scratch()`
- `operonx/core/ops/base.py` — `_normalise_observability()` helper; `BaseOp` __slots__ + __init__ accept `exclude`/`include`/`observe_max`; `store_result` calls `state.advance_step()` after committing
- `operonx/core/ops/graph/graph_op.py` — `_shared_vars`, `_reducer_vars` slots + init
- `operonx/core/ops/_shortcuts.py` — added filter kwargs to `_BASE_INIT_KEYS`
- `operonx/core/ops/transform/func_op.py` — `@op` decorator accepts + forwards new filter kwargs
- `operonx/core/engine.py` — `engine.start/run/invoke` accept `checkpointer=`; new `engine.stream(mode=...)` method; `_all_ops_registry()`; scheduler task's `except BaseException` branch; `on_cancel` invoked on `CancelledError`; teardown clears interrupt bus + unsubscribes checkpointer

---

## Design decisions locked in (irreversible without revisiting)

1. **`PARENT.declare()` is the name.** Not `state()`, not `PARENT.shared()`, not `STATE(...)`. Pythonic (mirrors `nonlocal`/`global`), unambiguous, doesn't collide with "state" as domain word we might want free later.

2. **Reducers apply at cell-level write, not `__setitem__`.** Both direct writes AND push-ref hops route through `_write_cell` so reducers can never be bypassed (the B1 fix from the earlier plan review).

3. **Filter API on the op, not on the observer.** `@op(exclude=..., include=..., observe_max=...)` — polymorphic list-or-dict, mutually exclusive between exclude/include. `include=[]` silences the op. Checkpointer/tracer both honour it via `should_emit_for_channel`.

4. **`ObserveBudgetExceeded` inherits `BaseException`.** So op-body `except Exception:` cannot swallow the circuit breaker. Scheduler task's `except BaseException:` puts it on the queue for `ExecutionHandle` to re-raise to the caller.

5. **`InterruptOp` and `EmitOp` are first-class visible graph nodes** — NOT body-side callables like LangGraph's `interrupt()`/`emit()`. Preserves the "read the graph body → know the DAG" invariant.

6. **Engine API adopts LangGraph naming only at the outer surface** (`engine.invoke()`, `engine.stream(mode=...)`). Internals stay operonx-native. Explicit rejection list of borrowed-then-dropped LangGraph concepts documented in the plan.

7. **`SCRATCH` kept as-is** — the "flexible in-body access" story. Callbot's 118 SCRATCH usages continue working. `SCRATCH.declare()` explicitly rejected — one declaration API on `PARENT` only. But **SCRATCH writes DO get captured by the checkpointer** via the new scratch bus (B1 fix in 2b3).

8. **step_id semantics** — bumps per `state.advance_step()` call inside `BaseOp.store_result`. For batch ops that's one step per invocation; **for generator ops it's one step per yielded frame** (docstring in `checkpoint/base.py` updated to make this explicit).

9. **`_current_step` is on `MemoryState`**, not the scheduler — simple integer counter, no locking (Python asyncio is cooperative; no true concurrent bumps).

10. **`push_ref` forwards source's post-reducer stored value**, not the raw incoming write (H1 fix). Identical behaviour for the canonical local→shared case where source has no reducer.

## Design decisions explicitly rejected (in the plan's §Rejected)

`STATE["k"]` accessor in body; `STATE` sentinel at wiring; `SCRATCH.declare()`; Rust reducer parity; cross-graph state access; full Pregel scheduler rewrite; per-variable timestamps in schema; `Command` wrapper; `Command.goto` dynamic routing; `interrupt()` body-callable; `emit()` body-callable; LangGraph checkpointer fork tree.

---

## Adversarial review outcomes (Phase 2b3)

Three agents ran in parallel against the Phase 1+2 code:
- **Correctness review** — 3 real bugs (B1, B2, B3), 3 hazards (H1, H2, H3), 3 overclaims
- **Test coverage audit** — 14 gaps ranked by impact (T1-T14)
- **Callbot compatibility** — zero breakage risk, zero migration required, three optional callbot improvements it now enables

Fixed in commit `28ecbba` + tested in `27208d6`:

| ID | Issue | Fix |
|---|---|---|
| B1 | SCRATCH writes bypassed the funnel — plan claimed capture, code didn't | Added `state._scratch_observers` bus + subscribe API + `_notify_scratch`; `ScratchAccessor.__setitem__` now fires it; `bind_checkpointer` subscribes a second observer that records `CellWriteEvent` under `("__scratch__", key, DEFAULT_CTX)` |
| B2 | One observer's exception truncated the notification loop for peers | Every observer runs; first `BaseException` re-raised after all peers had their turn |
| B3 | `engine.stream(mode=X)` leaked the scheduler on caller `break` — LLM calls / DB writes kept running with no consumer | Added `handle.cancel()` in `finally` of all 4 stream branches; also cancel local drainer/getter tasks in `mode="custom"` |
| H1 | Push-ref forwarded raw input value instead of source's post-reducer stored value | `stored = self._cells[idx][ctx_key]; _write_cell(push_ref.idx, ctx_key, push_ref._fn(stored))` — no-op for canonical local→shared |
| H3 | `state._interrupt_responses` dict never nulled at teardown | Engine's `finally` iterates + cancels + clears |
| T5 | `Checkpointer.on_cancel` was defined but never called from any engine path | Scheduler task's `except asyncio.CancelledError:` now calls `checkpointer.on_cancel(("main",))` |
| T7 | `bind_custom_bus` + `bind_interrupt_bus` didn't consult per-op filter | Both binders now accept `op_registry=` and consult `should_emit_for_channel` |
| H2 + overclaim | Doc said `advance_step` per invocation; reality is per yield for generators. Docstring also lied about SCRATCH capture. | Aligned both in `checkpoint/base.py` module docstring |

**Deferred (in the plan doc):**
- Full **Phase 3** — cyclic edges + Tarjan SCC extraction + `full_name` re-cache (still pending, sized ~340 LOC)
- H1's shared-cell-push-ref chain scenario has a fix; wiring at the DSL level is still architecturally unsupported (that's fine)

---

## Test suite anatomy

Grouped by directory (176 net-new tests):

```
tests/internal/
├── test_reducers.py                            23 (Phase 1)
├── core/states/
│   ├── test_declare_and_reducers.py            23 (Phase 1)
│   └── test_write_observer.py                  11 (Phase 2a)
├── checkpoint/
│   ├── test_memory_checkpointer.py             18 (Phase 2a)
│   ├── test_bridge.py                           7 (Phase 2a)
│   ├── test_bridge_filter.py                   15 (Phase 2b1)
│   ├── test_scratch_bus.py                      7 (Phase 2b3)
│   └── test_phase_2b3_hardening.py             14 (Phase 2b3)
├── core/ops/
│   ├── test_op_observability_filter.py         22 (Phase 2b1)
│   └── flow/
│       ├── test_emit_op.py                     12 (Phase 2b2)
│       └── test_interrupt_op.py                12 (Phase 2b2)
└── core/engine/
    └── test_engine_checkpointer.py             12 (Phase 2b2)
```

---

## What's shipped in the operonx public API

Users can now write:

```python
from operonx import (
    Operon, PARENT, START, END, op, graph,        # existing
    InterruptOp, EmitOp,                          # Phase 2b2 NEW
)
from operonx.reducers import (                    # Phase 1 NEW
    add_messages, dict_merge, RemoveMessage, REMOVE_ALL_MESSAGES,
)
from operonx.checkpoint import (                  # Phase 2 NEW
    Checkpointer, InMemoryCheckpointer,
    CellWriteEvent, ScratchWriteEvent, StepEvent,
    InterruptEvent, CustomEvent,
    StepNotFound, ObserveBudgetExceeded,
    bind_checkpointer, bind_custom_bus, bind_interrupt_bus,
)

@graph
def counter():
    PARENT.declare(count=0, messages=[],                       # Phase 1
                   reducers={"messages": add_messages})

    @op(exclude=["debug_tokens"], observe_max=10_000)          # Phase 2b1
    def tick(count):
        return {"count": count + 1, "messages": [f"tick {count}"]}

    t     = tick(count=PARENT["count"])
    t["count"] >> PARENT["count"]
    t["messages"] >> PARENT["messages"]

    tel   = EmitOp(payload=t["count"], channel="ui")           # Phase 2b2
    gate  = InterruptOp(payload=t["count"])                    # Phase 2b2

    START >> t >> tel >> gate >> END

# Run with observability:
cp = InMemoryCheckpointer(sample_every=None)
async for evt in Operon(counter).stream(inputs={},
                                        mode="updates",
                                        checkpointer=cp):
    print(evt)

# Post-run replay:
for s in cp.list_steps():
    print(f"step {s}:", cp.get_updates(s))
```

---

## What's NOT done (Phase 3 backlog)

From `STATE_LOOP_REFACTOR_PLAN.md`:

- **Phase 3 — Cyclic edges** (~340 LOC). Level-2 rewrite: user writes back-edges like `tools >> call_model`, build-time pass extracts SCC via Tarjan, wraps in hidden `GraphOp.loop`. Requires:
  - `cycle_rewrite.py` module with DFS back-edge detection (reusing existing `find_cycles`) + Tarjan SCC + `full_name` re-cache
  - Pass ordering: `cyclic_to_loop` → `_auto_soften_edges` (auto-soft assumes acyclic)
  - `GraphOp.loop` public API deprecated with warning; kept as internal primitive that the rewrite pass emits
  - Optional `@graph(strict_dag=True)` opt-out to preserve fail-fast for typo protection
  - Depends on Phase 1's reducers to enable iteration accumulation

- **Sqlite checkpointer** (~120 LOC) — deferred to `0.13.1`; currently only `InMemoryCheckpointer` ships.

- **Callbot migration opportunities** (all optional, not needed today):
  - `is_final`/`should_transfer` OR-latch in `src/agents/base_agent/ops/state.py:171-181` → migrate off SCRATCH to `PARENT.declare(reducers={"is_final": operator.or_})`
  - `pending_user_text` concat in `overlap_classifier.py` → same pattern
  - Formalise `print(f"PERF ...")` breadcrumbs via `EmitOp(channel="perf")` + `engine.stream(mode="custom", channels=["perf"])`

## What's NOT pushed

`feature/phase-2-checkpointer-hitl-stream` is **local-only**. Nothing pushed to `origin` yet. When we push:
- Callbot's `>=0.11.0` pin won't pick this up until we bump operonx to `0.12.0`
- Merge to `dev` first, ship a release, then a callbot pin bump (`>=0.12.0`)

---

## Post-compaction picking-up prompt

If a future conversation needs to continue this thread:

> "Phase 1 + Phase 2 of the operonx state/loop refactor are landed on `feature/phase-2-checkpointer-hitl-stream` — 6 commits, 176 new tests, 1156 pass, zero regressions, not yet pushed. See `docs/design/PHASE_1_2_IMPLEMENTATION_LOG.md` for the commit ladder + design decisions + review outcomes. Plan doc is `docs/design/STATE_LOOP_REFACTOR_PLAN.md` (v3.3.4). Callbot untouched — pins `>=0.11.0` and requires zero migration.
>
> Next possible directions:
>   1. **Push + PR**: push the branch, open MR to `dev`, ship as operonx 0.12.0
>   2. **Phase 3**: cyclic edges (Tarjan SCC + hidden `GraphOp.loop` rewrite, ~340 LOC — full spec in the plan doc)
>   3. **Sqlite checkpointer**: complete the deferred backend
>   4. **Callbot opportunistic migration**: move `is_final`/`should_transfer` OR-latch off SCRATCH to `PARENT.declare(reducers={"is_final": operator.or_})`
>
> No open blockers on any of these. Wait for user direction."
