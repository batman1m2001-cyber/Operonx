# Auto-soft branch-merge edges

**Status:** implemented (see [`_auto_soften_edges`](../../operonx/core/ops/graph/graph_op.py#L294) in `graph_op.py`).
**Feature flag:** `GraphOp(auto_soft=True)` (default). Per-edge opt-out: `add_edge(..., hard=True)`.

## Problem

`BranchOp` routes execution to exactly one of N outgoing branches at runtime. Downstream ops on the branches that were **not** selected never fire — the scheduler simply skips them ([`task_scheduler.py:302-313`](../../operonx/core/ops/graph/task_scheduler.py#L302)).

If two of those branch chains fan back into a common merge op M, and the edges into M are the default **hard** kind, M's ready-count never reaches zero — it deadlocks waiting for the branch that never ran. To make M fire on either branch's completion, users must manually mark the incoming edges as soft:

```python
router >> a >> ~m
router >> b >> ~m       # ~ tells scheduler: any one soft pred unblocks m
```

Forgetting `~` is a **silent deadlock** at runtime, not a build error. It's a common bug:
- callbot has 5 such `~` marks — every one is boilerplate that must be right
- Every ahamove_hr / base_agent workflow file repeats the same pattern

## Solution

At graph `build()` time, walk the DAG and auto-flip `edge.soft = True` for any incoming edge into M whose source and at least one sibling source **trace back to a common `BranchOp` ancestor via disjoint first-hop children**.

Both properties are structurally derivable from the graph shape — no runtime magic, no user opt-in required for the common case.

## Semantics

For every op M with 2+ predecessors, compute per predecessor P the **branch signature**:

> `sig(P) = { B : set of B's direct successors from which P is reachable, for every BranchOp B upstream of P }`

Then for each hard incoming edge P→M, if there exists a sibling predecessor Q of M such that:

> `∃ B ∈ sig(P) ∩ sig(Q) : sig(P)[B] ∩ sig(Q)[B] = ∅`

flip P→M to soft. (Both P→M and Q→M end up soft in a symmetric run over the pair.)

**Ancestor walk crosses both hard and soft edges** — branch attribution is a topological fact; a soft edge upstream does not change which branch decides a downstream node.

**Signatures are computed against the original edge structure BEFORE any softening is applied.** Flipping edges mid-analysis would erase branch attribution for downstream merges (soft edges are skipped by design; a fresh sig would lose the ancestor).

## Interactions with the rest of the engine

| Concern | Impact |
|---|---|
| Ready counting ([`_build`](../../operonx/core/ops/graph/graph_op.py#L406)) | Reads `edge.soft` after our pass — no change to `_build` itself |
| Scheduler dispatch | Unchanged — reads `edge.soft` at runtime the same way |
| Nested subgraphs | Analysis is per-graph. Each `GraphOp` runs its own pass via the existing `child.build()` recursion. PARENT refs cross graph boundaries; edges do not. |
| Loops (`GraphOp.loop`) | Static build-time analysis. Same soft flags apply to every iteration; no per-iteration work. |
| Generator ops / streaming | `.parallel()` / `.collect()` are per-var policies on `Ref`, not edges. Streaming edges have one source; `len(preds) < 2` skips them. |
| Rust runtime interop | `EdgeConfig.soft` is serialized verbatim ([`graph_op.py:670`](../../operonx/core/ops/graph/graph_op.py#L670)). Our pass mutates `soft` **before** serialization — Rust sees the post-transform value. **Zero Rust-side change.** |
| Backward compatibility | Grep of `tests/` + `examples/` + callbot: **zero cases** of hard-fan-in from branch alternatives (would already deadlock today). Manual `~` continues to work — the pass skips already-soft edges. |

## API additions

- `GraphOp(auto_soft: bool = True)` — per-graph kill switch. Default on.
- `add_edge(..., hard: bool = False)` — pin an edge as hard, opt out of auto-softening for the rare "sneak path" case (see limitations).
- `EdgeConfig.auto_soft: bool` (debug flag, not serialized) — True on edges the auto-pass flipped, False on user-written soft edges. Useful for tooling and log analysis.

## Logging

- One `LOGGER.debug` line per auto-softened edge naming the merge, the pred, the sibling pred, and the branch ancestor.
- One `LOGGER.info` summary at build end: `"Graph X: auto-softened N edges across K merge points"`.

## Known limitations

1. **Sneak paths not detected.** If a predecessor P is reachable from a non-branch root that bypasses B entirely, P is not truly mutually exclusive with its siblings under B — the pass will over-soften. Concrete example:

   ```
   br  → a  → M
   ext → x  → M    # x also fires when ext fires — independent of br
   br  → c  → M
   ```

   `sig(x)` may still list `br` as an ancestor via some hard path, but `x` can fire when `br` did not choose the branch leading to `x`. In that case both `x` and `c` can fire simultaneously — softening `x→M` and `c→M` may cause M to fire on `x` alone and miss `c`.

   **Mitigation:** flip the specific edge with `add_edge(..., hard=True)`, or disable the whole pass with `GraphOp(auto_soft=False)`.

   In practice this shape does not appear in agent / callbot graphs — branch chains are self-contained. But it is theoretically possible and a v2 refinement could add post-dominance analysis to eliminate the false positive.

2. **Type-carrying edges** (`type == "condition"` from BranchOp) are treated as normal edges for softening purposes — the `type` field is scheduler-informational only. Confirmed safe: `_route` treats them uniformly.

3. **Anchor override in BranchOp** ([`branch_op.py:120-122`](../../operonx/core/ops/flow/branch_op.py#L120)) can force any target at runtime. Auto-softening still holds — the runtime picks one target, others get skipped, downstream soft-merge fires correctly regardless.

## Empirical validation

- **Unit tests** — 9 shapes covered in [`test_auto_soft_edge.py`](../../tests/internal/core/ops/graph/test_auto_soft_edge.py):
  1. 2-way branch, both tails softened
  2. 2 branch preds + external hard pred (external stays hard)
  3. Manual `~` + auto-soft coexist (no double-flip)
  4. 3-way branch, all three softened
  5. Diamond WITHOUT branch (no softening — correct)
  6. Per-graph opt-out (`auto_soft=False`)
  7. Nested subgraph
  8. Per-edge opt-out (`hard=True`)
  9. Nested branches (deep ancestor chain)

- **Regression** — 971 passed, 23 skipped, 1 pre-existing deselect (unrelated `test_audio_input` 404). Zero regressions.

- **End-to-end on callbot** — deleted **all 5 manual `~` marks** from [`src/callbot/graph.py:424-437`](../../../educa-reminder-agent/src/callbot/graph.py#L424) (which drive the STT / workflow / oc / turn / spoken merge points). 204 callbot tests pass. The auto-soft pass restored every softening the pipeline needs, silently.

## Implementation surface

- `operonx/core/configs/edge_config.py` — 2 new fields on `EdgeConfig`: `auto_soft`, `pinned_hard`.
- `operonx/core/ops/graph/graph_op.py`:
  - New `auto_soft: bool = True` kwarg on `__init__`.
  - New `hard: bool = False` kwarg on `add_edge`.
  - New method `_auto_soften_edges()` (~80 LOC).
  - Hook: single call `self._auto_soften_edges()` inserted between `validate()` and `_build()` in `build()`.
- `tests/internal/core/ops/graph/test_auto_soft_edge.py` — 9 tests.

Total diff: ~120 lines added, 0 removed. Rust runtime: 0 lines touched.

## Migration

None required. Existing graphs with manual `~` continue to work — the pass skips already-soft edges. Users may incrementally delete manual `~` marks as they touch the code. A future `strict` mode could warn when a manual `~` is redundant (the pass would have flipped it anyway), but is not part of MVP.
