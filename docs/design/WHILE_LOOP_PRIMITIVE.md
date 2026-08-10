# `while_` — replace `GraphOp.loop` with a data-flow-native loop primitive

**Status:** proposed. Design phase. No code yet.
**Breaking:** yes — deletes `GraphOp.loop` and `@graph.loop`. Bump operonx to `0.11.0`.

## 1 · Motivation

Today's `GraphOp.loop` has four ergonomic pain points:

1. **Ugly declaration** — `with GraphOp.loop(...) as g:` reads as a classmethod-returning-context-manager, not as a first-class loop primitive.
2. **`until="expr"` string** — a Python expression as a string is unusual in Python code. The callable form works but the string exists for Rust serialization, and it stays in every example.
3. **`**initial_state` kwargs mixed with config** — you can't tell `messages=[]` (state) from `max_iterations=25` (config) at a glance.
4. **`op["k"] >> PARENT["k"]` feedback lines** — one line per state variable, reads as imperative assignment, easy to forget one.

Together these make loops feel like the least operonx-native construct in the framework.

The `while_` primitive:
- Reads like Python's `while` (word choice mirrors `if_`)
- Termination is a **branch inside the graph** routing to `END` or `PARENT`, not an `until=` string
- State feedback is **automatic via wildcard match** when the branch routes to `PARENT`, no per-var lines
- Both decorator and context-manager forms — same primitive, different call sites

## 2 · API

### 2.1 · Decorator form (recommended when the loop has a name)

```python
from operonx import while_, if_, START, END, PARENT

@while_(count=0)
def counter_loop():
    inc = tick(counter=PARENT["count"])
    START >> inc >> if_(inc["counter"] >= 5, END).else_(PARENT)
```

`counter_loop` is a `GraphOp` (loop-flavored) — usable as an op node inside any outer `@graph`.

### 2.2 · Context manager form (recommended for inline builders)

```python
with while_(count=0) as g:
    inc = tick(counter=PARENT["count"])
    START >> inc >> if_(inc["counter"] >= 5, END).else_(PARENT)
```

Same semantics. Ops auto-register into `g` via ContextVar exactly like `with GraphOp(...) as g`.

### 2.3 · `while_.of(...)` for config overrides

`while_` state kwargs are user data — they cannot collide with framework config. Config lives on `.of(...)`:

```python
@while_.of(max_iterations=500)(count=0)
def bigger_loop():
    ...

with while_.of(max_iterations=500)(count=0) as g:
    ...
```

Default `max_iterations=100` (unchanged from `GraphOp.loop`).

### 2.4 · Termination semantics

**`if_(cond, END).else_(PARENT)`** placed at the tail of the loop body:
- **route `END`** → loop terminates. The data flowing into the branch (via `>> if_(...)`) becomes the loop's outputs (wildcard match to graph outputs, same as `>> END` in a normal graph).
- **route `PARENT`** → iterate again. The data flowing into the branch becomes the next iteration's state (wildcard match to state slots by name).

Complex termination composes with existing branch primitives:

```python
if_(err, END).if_(done, END).else_(PARENT)             # multi-exit
if_(iter >= 25, END).else_(PARENT)                     # user-owned counter
```

## 3 · Semantics — state, feedback, iteration

### 3.1 · State slots

Every `while_(**state)` kwarg becomes a **loop state slot** — read via `PARENT["name"]` from any op in the loop body. On iteration 1, seeded from the kwarg value. On subsequent iterations, seeded from the branch's incoming data (wildcard match by name).

### 3.2 · Wildcard feedback

When the terminator branch routes to `PARENT`:
- The data feeding INTO the branch (i.e., outputs of the op(s) immediately upstream of the branch) are forwarded to state slots by name match
- Unmatched op outputs are silently dropped (same as `>> END`'s wildcard behavior today)
- Unmatched state slots retain their previous iteration's value (unchanged since PARENT never overwrote them)

### 3.3 · Explicit per-var feedback (escape hatch)

When op output names don't match state slot names, add explicit lines before the branch:

```python
with while_(iter=0, messages=[]) as g:
    turn = do_turn(messages=PARENT["messages"])   # outputs "messages", "done"
    tick = increment(count=PARENT["iter"])
    tick["count"] >> PARENT["iter"]               # explicit — name mismatch
    [turn, tick] >> if_(turn["done"], END).else_(PARENT)
```

Wildcard covers matching keys (`messages`). Explicit lines cover mismatches (`iter` ← `tick["count"]`). They compose.

### 3.4 · Iteration mechanics

Under the hood, `while_` compiles to the same scheduler primitive as today's `GraphOp.loop`:
- New `ctx + ("iter_N",)` tuple per iteration → automatic state isolation
- Ops re-fire per iteration in that ctx
- Max-iterations cap prevents runaway (default 100, override via `.of()`)

The user never sees this — it's an implementation detail.

## 4 · Errors and conflicts

**This is the load-bearing section.** Every error must be caught at build time when possible, with a message that names the specific fix. Silent misbehavior at runtime is the anti-pattern we're eliminating from `GraphOp.loop`.

### 4.1 · Build-time errors (`while_`-specific)

| # | Condition | Error message |
|---|---|---|
| E1 | `while_(**state)` with no terminator branch routing to `END` | `"Loop 'X' has no terminating branch. Add a branch routing to END, e.g. if_(cond, END).else_(PARENT)."` |
| E2 | `while_(**state)` with no branch routing to `PARENT` | `"Loop 'X' can only exit — no branch routes to PARENT. Did you mean a straight-line graph?"` |
| E3 | Terminator branch has an `END` case but no `PARENT` case (or vice-versa) | `"Loop 'X' terminator branch must include both END and PARENT targets to be a valid loop."` |
| E4 | Multiple sources fan into the terminator branch AND produce overlapping output keys mapped to same state slot | See §4.4 below (main conflict case) |
| E5 | State slot has no producer feeding it (op outputs don't match, no explicit `>> PARENT["k"]`) | `"Loop 'X': state slot 'iter' has no producer. Either name an op output 'iter' or add explicit `op[...] >> PARENT["iter"]`."` — non-fatal warning (slot retains initial value forever) |
| E6 | `PARENT` used as branch target outside a `while_` graph | `"PARENT as branch target requires a while_() context. Found in graph 'X' which is not a loop."` |
| E7 | `while_(name=...)` or `while_(until=...)` — collides with reserved kwargs | `"'name'/'until' cannot be used as a state slot name. Use while_.of(...) for config."` — actually **name/until are reserved and rejected** because there are no such config params on `while_` (config lives on `.of()`) |
| E8 | `until=` passed to `while_` | Same as E7 — `until` is dead. Explicit error rather than silent ignore. |
| E9 | Loop body has NO ops (empty `with while_(...):`) | `"Loop 'X' body is empty."` |

### 4.2 · Runtime errors

| # | Condition | Behavior |
|---|---|---|
| R1 | `max_iterations` exceeded | Raise `LoopIterationLimitError(loop_name, max_iterations)`. Same behavior as `GraphOp.loop` today. |
| R2 | Terminator branch's condition raises an exception | Standard operonx op-error handling (`state[branch, "error", ctx]` populated, downstream ops receive `None`). No special loop treatment. |

### 4.3 · Deprecation errors (migration period)

| # | Condition | Behavior |
|---|---|---|
| D1 | `GraphOp.loop(...)` called anywhere | `DeprecationWarning("GraphOp.loop is removed in operonx 0.11; use while_() — see docs/design/WHILE_LOOP_PRIMITIVE.md")` — raises after grace period |
| D2 | `@graph.loop` decorator used | Same as D1 |

Grace period: one minor release (`0.11.x` warns, `0.12.0` deletes).

### 4.4 · **The main conflict case — multi-source fan-in to a loop terminator**

When multiple ops feed the terminator branch, they must not both write the same state slot on the `PARENT` path — else FIFO decides silently and the user has a bug.

**Example — CONFLICT:**
```python
with while_(messages=[]) as g:
    turn = do_turn(messages=PARENT["messages"])         # outputs 'messages', 'done'
    error_handler = do_error(messages=PARENT["messages"])  # ALSO outputs 'messages'
    [turn, error_handler] >> if_(turn["done"], END).else_(PARENT)
```

Both `turn` and `error_handler` produce a `messages` key. On the `PARENT` route, both would land in state slot `messages`. FIFO order (fan-in resolution) picks whichever fires first — silent, non-deterministic if concurrent.

**Build-time error (E4):**
```
Loop 'g': terminator branch has an ambiguous state feed.
  - turn produces 'messages' → PARENT["messages"]
  - error_handler produces 'messages' → PARENT["messages"]
Resolve by one of:
  (a) explicit winner:  turn["messages"] >> PARENT["messages"]
  (b) rename one output so it doesn't match a state slot
  (c) different targets: if_(err, END).else_(if_(is_turn, turn_branch).else_(error_branch))
```

**Detection:** at build, walk fan-in edges into the terminator branch. For each PARENT-route case, compute the union of output names across sources. If any output name appears in ≥2 sources AND matches a state slot, error.

### 4.5 · Edge cases + design decisions

| Case | Decision |
|---|---|
| Loop with 0 iterations (initial branch immediately routes to `END`) | Legal. Returns initial state. |
| Loop nested inside another loop — `PARENT` in inner branch | Refers to **innermost** `while_` (lexical scope), like Python's `break`. Build-time validation: `PARENT` as branch target resolves to the nearest enclosing `while_`. |
| Nested loops — outer wants to `PARENT`-continue from inner's exit | Inner routes to `END` (exits inner). Outer body then routes to `PARENT` at its own terminator. No cross-loop `PARENT` targeting. |
| Branch routes to `PARENT` but has no data upstream (dead-end branch) | Loop iterates with unchanged state → likely infinite loop → hit `max_iterations`. Not caught at build; document. |
| Two separate terminator branches in the same loop | Legal. Both must route to `END`/`PARENT`. Fan-in of exits handled by FIFO — first exit wins, subsequent exits ignored (log warning at DEBUG level). |
| `if_` with only `.build()` (no default) at the terminator | Illegal — E3 (no `PARENT` case → cannot iterate). |
| State kwarg named `_max_iterations` or reserved-underscore name | Reject at build (E7-style). Reserved prefix `_` for future config expansion. |
| User uses `while_` outside a `@graph` context AND without a `with` block | `while_(...)` returns a `GraphOp` regardless — usable standalone. The context manager form just installs ContextVar for auto-registration; equivalent to `GraphOp()` usage. |
| Callable `until=` passed to `while_` (from muscle memory) | E8 — explicit error, name the migration. |

### 4.6 · Non-errors (things that look wrong but aren't)

| Case | Why not an error |
|---|---|
| Op output that doesn't match any state slot | Wildcard silently ignores unmatched outputs. Same as `>> END` today. Consistent. |
| Multiple `while_` in one function | Legal. Each returns a distinct `GraphOp`. Independent contexts. |
| `while_` body that never reads any `PARENT["k"]` | Legal (stateless loop iterating side-effects). |
| `if_(cond, END).else_(PARENT)` with same op feeding both END and PARENT paths | Legal — data flow is identical, only routing differs. |

## 5 · Migration story

### 5.1 · From `GraphOp.loop(...)`

```python
# BEFORE
with GraphOp.loop(until="count >= 5", count=0, messages=[]) as g:
    inc = tick(counter=PARENT["count"])
    inc["counter"] >> PARENT["count"]
    START >> inc >> END

# AFTER
with while_(count=0, messages=[]) as g:
    inc = tick(counter=PARENT["count"])
    START >> inc >> if_(inc["counter"] >= 5, END).else_(PARENT)
```

Mechanical rewrite:
1. `GraphOp.loop(...)` → `while_(...)` (drop `until=`, drop `max_iterations=` unless non-default → move to `.of()`)
2. Delete `op["k"] >> PARENT["k"]` feedback lines (assuming output names match state slots — else keep them as escape hatch)
3. Replace terminal `>> END` with `>> if_(<until-expr>, END).else_(PARENT)`

### 5.2 · From `@graph.loop`

```python
# BEFORE
@graph.loop(until="count >= 5")
def counter(count=0):
    inc = tick(counter=PARENT["count"])
    inc["counter"] >> PARENT["count"]
    START >> inc >> END

# AFTER
@while_(count=0)
def counter():
    inc = tick(counter=PARENT["count"])
    START >> inc >> if_(inc["counter"] >= 5, END).else_(PARENT)
```

### 5.3 · In-tree call sites to update

Grep of operonx repo shows `GraphOp.loop` / `@graph.loop` in ~15-25 places (tests + examples + docs). All mechanical rewrites. Callbot uses zero loops — no downstream migration.

### 5.4 · Deprecation timeline

- `operonx 0.11.0` — ships `while_` + `GraphOp.loop` DeprecationWarning + docs
- `operonx 0.11.x` — bug fixes, keep the warning
- `operonx 0.12.0` — delete `GraphOp.loop` + `@graph.loop`

## 6 · Implementation surface

### 6.1 · New code

| Component | LOC est. | File |
|---|---|---|
| `while_()` factory + `.of()` factory | ~40 | `operonx/core/ops/graph/while_op.py` (new) |
| Decorator machinery (`@while_(...)`) | ~30 | same |
| Context manager delegation to underlying loop GraphOp | ~20 | same |
| Branch-to-`PARENT` scheduler support (recognize routing decision at end-of-iter) | ~40 | `operonx/core/ops/graph/task_scheduler.py` (patch) |
| Wildcard feedback: branch's upstream data → PARENT slots on `PARENT` route | ~30 | `operonx/core/ops/graph/task_scheduler.py` |
| Build-time validation E1–E9 | ~80 | `operonx/core/ops/graph/validation.py` (patch) + `while_op.py` |
| Public exports (`operonx.while_`) | ~5 | `operonx/__init__.py`, `operonx/core/__init__.py` |
| `LoopIterationLimitError` exception class | ~10 | `operonx/core/exceptions.py` |

**Total new/patched code: ~255 LOC**

### 6.2 · Deletion (staged over 2 releases)

| Component | LOC removed | When |
|---|---|---|
| `GraphOp.loop` classmethod | ~35 | 0.12.0 |
| `@graph.loop` decorator | ~50 | 0.12.0 |
| `LoopConfig` (if unused after `while_op.py` refactor) | ~20 | 0.12.0 |
| `until=` handling in scheduler | ~25 | 0.12.0 |
| Deprecation warnings | ~10 | 0.12.0 |

### 6.3 · Tests

| Category | Count | LOC |
|---|---|---|
| Happy-path shapes (basic, decorator, `.of()`, nested, multi-exit) | 8 | ~200 |
| Error cases E1–E9 (each with a `pytest.raises`) | 9 | ~150 |
| Migration validation (each old-style `GraphOp.loop` shape works before deletion) | 5 | ~80 |

**Test total: ~430 LOC**

### 6.4 · Migration edits (mechanical)

`GraphOp.loop` → `while_` rewrite across ~15-25 test/example sites. ~100 LOC of edits.

## 7 · Test matrix

### 7.1 · Happy paths

| # | Shape | Expected |
|---|---|---|
| H1 | Counter — decorator form | `while_(count=0)` with `if_(count >= 5, END).else_(PARENT)` — 5 iterations, returns `{"count": 5}` |
| H2 | Counter — context manager form | Same as H1, `with while_(...)` |
| H3 | `.of(max_iterations=3)` — hits cap | Raises `LoopIterationLimitError` |
| H4 | Multi-var state — all outputs match slot names | Wildcard feeds all correctly |
| H5 | Multi-var state — one output doesn't match, explicit `>>` line covers it | Wildcard + explicit compose |
| H6 | Multi-exit — `if_(err, END).if_(done, END).else_(PARENT)` | Exits on either condition |
| H7 | Nested `while_` inside `while_` — inner exits, outer continues | Inner's `PARENT`/`END` targets nearest enclosing loop only |
| H8 | Loop with zero iterations (immediately routes to `END`) | Returns initial state as outputs |

### 7.2 · Error cases

| # | Case | Test |
|---|---|---|
| E1 | Loop with no `END` route | `pytest.raises(GraphValidationError, match="no terminating branch")` |
| E2 | Loop with no `PARENT` route | `pytest.raises(GraphValidationError, match="only exit")` |
| E3 | Terminator missing one of `{END, PARENT}` | Same |
| E4 | Ambiguous state feed (two sources same key) | `pytest.raises(GraphValidationError, match="ambiguous state feed")` |
| E5 | State slot with no producer | Warning at build, slot stays at initial value (verify via runtime state read) |
| E6 | `PARENT` as branch target outside a `while_` | `pytest.raises(BranchError, match="requires a while_")` |
| E7 | `while_(name=...)` (reserved) | `pytest.raises(TypeError, match="reserved")` |
| E8 | `while_(until=...)` | Same as E7, name the migration |
| E9 | Empty loop body | `pytest.raises(GraphValidationError, match="body is empty")` |

### 7.3 · Migration parity

For each of the 15-25 in-tree `GraphOp.loop` sites, verify equivalent `while_` version produces the same outputs on the same inputs.

## 8 · Rust runtime impact

`while_` is a Python-side construct that compiles to the same `LoopConfig`-shaped payload the Rust runtime already understands (or will, once we serialize the new form similarly). The scheduler-level branch-to-`PARENT` detection is Python-only for MVP; **Rust support is out of scope for this design doc** and would be a follow-up.

For MVP, `while_` graphs that need Rust execution serialize back to the current `GraphOp.loop` shape (with a compiled `until` expression synthesized from the branch condition — or refuse serialization if the branch is too complex). Alternative: `while_` is Python-runtime-only initially, `operonx-pack` warns if serializing.

**Decision needed with the Rust team before implementation.** Document as an open question in this doc.

## 9 · Open questions

1. **Rust serialization** (§8) — synthesize `until=` from branch, refuse serialization, or extend Rust runtime? Blocking decision before MVP.
2. **`.of()` vs alternative config surface** — `while_.of(max_iterations=200)(count=0)` is nested. Alternatives: `while_(state={"count": 0}, config={"max_iterations": 200})` (dict split), `while_.max(200)(count=0)` (fluent). Bikeshed; pick before shipping.
3. **Warn on redundant `>> PARENT["k"]`** — when wildcard would have covered it, log DEBUG? Nice-to-have for cleanup mode; not MVP.
4. **`break` / `continue` sugar** — Python has both; do we want `BREAK = END` alias and `CONTINUE = PARENT` alias for readability? Minor.
5. **Multi-terminator ordering** — when two separate terminator branches in one loop both fire, FIFO wins. Document explicitly + log at DEBUG when this happens.

## 10 · Non-goals for MVP

- No `for_(items)` sibling — generator ops + fan-out already cover for/map cleanly (see [streaming.md](../architecture/streaming.md))
- No async iteration semantics beyond what `GraphOp.loop` supports today
- No Rust runtime support in MVP (see §8)
- No auto-conversion of `GraphOp.loop` call sites — user does mechanical rewrite guided by DeprecationWarning message

## 11 · Sign-off checklist

Before implementation starts:
- [ ] User (@thanglq) confirms API shape (§2) and error catalog (§4)
- [ ] Rust question (§8) resolved — decision documented in doc
- [ ] `.of()` config surface (§9.2) picked
- [ ] Migration timeline (§5.4) confirmed
