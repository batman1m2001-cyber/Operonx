# Execution flow

This page walks one full call to `Operon(graph).run(...)` end-to-end.

## Sequence — a 3-op linear graph

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant G as GraphOp
    participant E as Operon
    participant S as Scheduler
    participant A as op_a
    participant B as op_b
    participant C as op_c
    participant T as Tracer

    rect rgb(237, 231, 246)
    Note over U,G: Construction (untimed)
    U->>G: with GraphOp(...) as g:
    U->>G: a = op_a(...); b = op_b(...); c = op_c(...)
    U->>G: START >> a >> b >> c >> END
    G->>G: __exit__: build() → resolve refs, freeze schema
    end

    rect rgb(224, 242, 241)
    Note over U,T: Init
    U->>E: engine = Operon(graph, tracer=t)
    E->>E: eager warmup (resource resolution)
    end

    rect rgb(255, 243, 224)
    Note over U,T: Run
    U->>E: await engine.run(inputs={"x": 5})
    E->>S: seed state, START → ready queue
    S->>A: dispatch (inputs from PARENT)
    A->>T: span_start(a)
    A-->>S: Frame(outputs)
    S->>S: write a-outputs to state
    A->>T: span_end(a)
    S->>B: dispatch (reads a["..."])
    B-->>S: Frame
    S->>C: dispatch (reads b["..."])
    C-->>S: Frame, EOF
    S->>S: collect + auto-forward via >> END
    E-->>U: result
    end
```

Three observations:

- **Construction is build-time, not run-time.** Reference resolution
  happens once at `__exit__`; the engine just executes against a frozen
  schema.
- **Eager warmup at init.** Resource lookups, `#[op]` registry checks,
  and any expensive schema validation are paid before `engine.run` is
  ever called. Run-time errors are caller-data errors.
- **The scheduler is the only thing that holds state across ops.** Ops
  themselves are stateless functions — if you need shared state, write
  it to PARENT or pipe through siblings.

## Phase 1 — Graph construction

`with GraphOp(name="g") as graph:` enters a build context. Op constructors
register themselves with the active graph; `>>` edges are recorded as a
list of `(src, dst)` pairs.

When the context exits, `GraphOp.__exit__` calls `build()`, which:

1. Resolves every `PARENT["k"]` and `op["k"]` reference against op schemas.
2. Validates that every edge endpoint exists.
3. Computes auto-forwarding for `>> END` (the last op's outputs become
   the graph's outputs).
4. Freezes the schema — runtime no longer needs to inspect Python AST.

If any reference is unresolved, build raises before the engine ever sees
the graph.

## Phase 2 — Engine init

```python
engine = Operon(graph)
```

The engine:

1. Stores `graph` and optional `tracer`.
2. **Eager warmup** — walks the graph, calls each op's `warmup()` hook.
   Provider ops resolve their `resource="..."` against the
   [`ResourceHub`](resource-hub.md) here. If the hub is missing or the key
   isn't registered, you get a fix-pointing error at `Operon(graph)`, not
   at first run.

`Operon.__init__` does **not** load `.env` or `resources.yaml`. Resource
setup is the caller's responsibility — see [Resource hub](resource-hub.md).

## Phase 3 — Run

```python
result = await engine.run(inputs={"x": 5})
```

The scheduler:

1. Seeds the root state with `inputs` (resolves all `PARENT["k"]` refs).
2. Adds `START` to the ready queue.
3. Loops: pop a ready op, run it, write its outputs into state, propagate
   to downstream ops, mark them ready when all hard-edge predecessors have
   completed.
4. When `END` is reached (all outgoing paths completed), returns the
   forwarded result.

Branch ops emit frames on **soft** edges only when their condition
selects that branch. `build()` softens branch-merge edges automatically
(`auto_soft=True`), because forgetting the manual `~` was a silent
run-time deadlock rather than a build error. Generator ops yield once per item; downstream ops
run once per yield (streaming default — see [Streaming](streaming.md)).

## Contexts

Every op invocation runs at a **context** — a tuple of path segments
identifying *which* invocation it is:

| Context | Meaning |
|---|---|
| `("main",)` | the root run |
| `("main", "[2]")` | the branch handling a generator's third yield |
| `("main", "[2]", "__collect__")` | a `collect()` consumer under that branch |
| `("main", "g.__loop_0__#1")` | iteration 1 of a synthesized loop |

Two properties the rest of the system rests on:

- **Containment is a tuple-prefix test.** `("main", "[2]")` is inside
  `("main",)`. This is why an empty prefix matches every context, and why
  cancellation targets have to be explicit (below).
- **Loop iterations are siblings, not nesting.** Iteration 1 is
  `("main", "g.__loop_0__#1")`, not a child of iteration 0. A stale
  iteration is therefore never "inside" the current one, which is what
  makes termination decidable.

## Cancellation

An op cancels in-flight work by returning or yielding an `Interrupt`. The
scheduler drops queued frames at the target context, cancels its in-flight
tasks, and clears the bookkeeping.

```python
from operonx.core import Interrupt

@op
def guard(score: float):
    if score < 0:
        return Interrupt(reason="negative score")   # cancels this branch
    return {"ok": score}
```

`ctx_to_cancel` defaults to `Interrupt.SELF`, which the scheduler resolves
to the emitter's own context — the *yield's* context for a generator, not
the whole op. Cancelling everything is destructive and has to be written
out:

```python
Interrupt(ctx_to_cancel=Interrupt.ALL, reason="fatal")
```

Three things worth knowing:

- **The emitter survives its own sweep.** It finishes normally, so its EOF
  and cleanup run as usual.
- **A cancelled invocation produces no result.** A subgraph whose root was
  swept yields nothing rather than the cells as they stand — otherwise the
  parent receives an all-`None` dict indistinguishable from a successful
  null answer.
- **The cancellation is reported.** A `("__interrupt__", ctx, …)` record
  reaches `handle.interrupts` even when the sweep happened inside a nested
  subgraph.

`Interrupt.SELF` is a sentinel, not a tuple. If a code path ever fails to
resolve it, the containment test raises `TypeError` rather than silently
matching every context — see
[Failure modes §8](failure-modes.md#8-an-empty-prefix-matches-everything).

## Phase 4 — Tracing

If a tracer was passed to `Operon(graph, tracer=...)`, every op start/end
is recorded with timing, inputs, outputs, and the parent op span. Tracers
are pluggable; see [`operonx.telemetry`](../api/telemetry.md).

## Failure points

| Phase | What can fail | Surfaced as |
|---|---|---|
| Construction | Bad PARENT/op ref | `BuildError` at `with` exit |
| Engine init | Missing resource | Branch-(1)…(5) error from [`ResourceHub.get`](resource-hub.md#errors-five-disambiguated-branches) |
| Run | Op raises | `OpError` subclass with the op name and span context |
| Run | Schema mismatch | `ParserError` when an op's output doesn't match its declared shape |
