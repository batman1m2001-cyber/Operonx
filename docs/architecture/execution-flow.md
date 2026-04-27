# Execution flow

This page walks one full call to `Operon(graph).run(...)` end-to-end.

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

Branch ops emit frames on `>>~` (soft) edges only when their condition
selects that branch. Generator ops yield once per item; downstream ops
run once per yield (streaming default — see [Streaming](streaming.md)).

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
