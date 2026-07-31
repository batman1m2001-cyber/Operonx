# Inline `if_/else_` branch API

**Status:** implemented (see `Branch._build` in `branch_op.py`).
**Feature flag:** none — additive; string-target form still supported for forward references.

## Problem

Today's `BranchOp` API forces users to state each branch target **twice**:

```python
stt_route = if_(cond, "asr").else_("skip_stt")     # (1) target names
# ... later ...
START >> source >> stt_route
stt_route >> asr >> denoise >> picker              # (2) target op refs
stt_route >> skip_stt >> picker                    # (2)
```

- If targets are string names, they must match the actual op names — silent rename hazard
- Even when targets are op instances, the reference appears twice (once in `if_/else_`, once in `route >> target` wiring)
- Requires a standalone `stt_route = ...` line just to hold the branch — visually disconnected from where the routing happens in the flow

## Solution

`if_(cond, target).else_(target)` now:

1. Accepts **op instances** as targets (previously accepted only op names as strings; op-instance names were extracted immediately, losing the reference)
2. **Auto-wires** `branch >> target` edges for every op-instance target found in cases and default at build time — one line per target vanishes
3. **Auto-names** the branch: LHS via `auto_name()` (for the `route = if_(...)` form), then falls back to a per-graph `route_1`, `route_2` counter when there's no LHS (inline usage)
4. Can drop directly into a `>>` chain — no standalone declaration needed

```python
# Before                                    # After
stt_route = if_(cond, asr).else_(skip_stt)
START >> source >> stt_route                START >> source >> if_(cond, asr).else_(skip_stt)
stt_route >> asr >> denoise >> picker       asr >> denoise >> picker
stt_route >> skip_stt >> picker             skip_stt >> picker
```

3 lines → 3 lines, but 1 dead `stt_route = ...` line removed and every target is named once. Combined with [`auto_soft_branch_merge`](AUTO_SOFT_BRANCH_MERGE.md), the entire branch/merge pattern is:

```python
START >> source >> if_(cond, asr).else_(skip_stt)
asr >> denoise >> picker
skip_stt >> picker
```

Zero `stt_route =`, zero `route >> target`, zero manual `~` — the DAG shape is exactly what you read.

## API

**Inline (op-instance targets, auto-wired):**
```python
START >> source >> if_(source["kind"] == "audio", asr).else_(skip_stt)
```
- Branch auto-named `route_1` (or `route_N` for the Nth inline branch in the graph)
- Auto-adds edges `branch → asr` (condition) and `branch → skip_stt` (condition)

**LHS-assigned (op-instance targets, auto-wired, custom name):**
```python
stt_route = if_(source["kind"] == "audio", asr).else_(skip_stt)
START >> source >> stt_route
# still auto-wires branch → asr and branch → skip_stt
```
- Branch named `stt_route` via `auto_name()` LHS detection

**String-name targets (backward-compat, no auto-wiring):**
```python
route = if_(PARENT["score"] >= 90, "excellent").else_("fail")
route >> excellent >> merge                 # user wires manually
route >> fail      >> merge
```
- Use when targets are forward references not yet defined
- Use when the branch is a first-class named entity referenced across multiple wiring statements

**Mixed:**
```python
route = if_(cond, some_op).else_("forward_ref")
# some_op auto-wired; "forward_ref" edge added manually by user
route >> forward_ref >> merge
```

## Name resolution rules

Precedence in `Branch._build()`:
1. **Explicit** `name=` kwarg → wins
2. `auto_name()` → LHS variable if the branch is on the RHS of `x = if_(...)`
3. **False-positive guard**: if `auto_name()` returns a name that already exists as an op in the current graph, reject it (source-parser lookback fell back onto a nearby `m = ...` line) and fall through
4. `route_N` per-graph counter where N = `1 + existing branch ops in graph`

Step 3 is the fix for a real bug: `auto_name()`'s source parser walks back 6 lines looking for the first `name = ...`, so `source >> if_(...).else_(...)` with a nearby `m = _mk(...)` line would incorrectly grab `m` as the branch name — colliding with the actual op `m` and corrupting the graph.

## Backward compatibility

**Zero breaking changes.**

- All existing string-target patterns work unchanged (`if_(cond, "name")` → no auto-wiring, name stored as-is)
- All existing op-instance-target patterns work identically (were already `.name`-extracted; now the instance itself is also kept for auto-wiring)
- All 80 pre-existing flow tests pass unchanged
- All 971 pre-existing operonx tests pass unchanged

## Empirical validation

- **Unit tests** — 9 shapes in `test_branch_inline.py`:
  1. Inline op-instance targets auto-wire branch→target
  2. LHS assignment uses the variable name (`route`)
  3. Inline no-LHS falls back to `route_1`
  4. Multiple inline branches get sequential `route_1`, `route_2`
  5. String targets: no auto-wiring (backward compat)
  6. Mixed op-instance + string: per-case behavior
  7. Auto-soften still fires on inline branch merges
  8. Three-way inline branch
  9. `.build()` (no default) also auto-wires

- **Regression** — 980 passed (971 previous + 9 new), 23 skipped, 1 pre-existing deselect. Zero regressions.

- **End-to-end on callbot** — all 4 branches in `callbot/graph.py` inlined:
  - `stt_route` — gone; inline `if_(source["kind"] == "audio", asr).else_(skip_stt)`
  - `oc_route` — gone; inline `if_(oc_should["should_classify"] == True, oc_ctx).else_(oc_skip)`
  - `turn_route` — gone; inline `if_(stashed["transcript"] != "", agent_turn).else_(skip_turn)`
  - `workflow_route` — gone; inline `if_(source["kind"] == "greeting", skip_workflow).else_(decider)`

  Net **–18 lines** (36 removed, 18 added). 204 callbot tests pass.

## Implementation surface

- `operonx/core/ops/flow/branch_op.py`:
  - Added imports: `auto_name`, `get_current`
  - `Branch.__init__`: `_cases` and `_default` types widened from `str` to `Any` (accept op instances or strings)
  - `Branch.if_()`: stops eagerly extracting `.name`; keeps the original target
  - `Branch.else_()`: same
  - `Branch._build()`: rewritten
    - Extract string names for `BranchOp` constructor
    - Resolve `self._name` via explicit → `auto_name` (with false-positive guard) → `route_N` counter
    - After constructing `BranchOp`, walk `_cases` and `_default`; auto-add condition edges for op-instance targets via `current_graph.add_edge`
  - Updated docstrings + module `if_()` docstring
- `tests/internal/core/ops/flow/test_branch_inline.py` — 9 tests.

Total diff: ~90 lines added, ~15 removed. Rust runtime: 0 lines touched.

## Interaction with auto-soft-edge

The two features compose cleanly:
- Inline `if_/else_` handles the **branch → target** edge duplication
- Auto-soft-edge handles the **target → merge** edge softening

Together they eliminate every piece of branch/merge boilerplate. Empirically demonstrated on callbot — from 4 branch declarations + 5 manual `~` marks + 8 `route >> target` lines down to 4 inline `if_/else_` calls in the flow, nothing else.

## Migration

None required. Existing code keeps working. When you next touch a branch, consider inlining if the targets are already op instances.
