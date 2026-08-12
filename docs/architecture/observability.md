# Observability

Two independent channels watch a run, and every op can shape what reaches
each one.

| Channel | Consumer | Bound by |
|---|---|---|
| `"trace"` | `handle.trace` and every [telemetry consumer](../api/telemetry.md) built on it — Langfuse, the local HTML view, your own | always |
| `"checkpoint"` | the [`Checkpointer`](../api/core.md), for replay and durable audit | `engine.start(checkpointer=…)` |

`EmitOp` payloads (`stream(mode="custom")`) and `InterruptOp` prompts
honour the same filters under their own channel names.

## Filtering what is observed

```python
@op(exclude=["tokens"])                 # both channels
@op(exclude={"trace": ["api_key"]})     # trace only
@op(include=["status"])                 # allowlist — everything else hidden
@op(include=[])                         # silence the op entirely
```

`exclude=` and `include=` are mutually exclusive; passing both raises at
decoration time, where the op is declared rather than where it runs. The
list form applies to every channel; the dict form splits per channel.

Both filters apply to an op's **inputs and outputs**, and to every record —
a generator emits one trace record per yield, and each is filtered.

!!! warning "This is the mechanism for keeping a credential out of a trace"
    A trace is an artifact that leaves the process: it is written to disk,
    posted to Langfuse, and rendered in a browser. `exclude={"trace": […]}`
    is what keeps a value out of it.

    This did not work until August 2026 — the filter reached the
    checkpoint bus only, so an excluded value was absent from the durable
    log and printed in the trace. If you are pinned below that, do not
    rely on it.

## Capping a runaway op

```python
@op(observe_max=10_000)
def tokens(prompt: str):
    ...
```

If the op emits more than `observe_max` observable events in a single run,
`ObserveBudgetExceeded` is raised and the run halts. It is a
`BaseException`, so it bypasses the ordinary `except Exception` handling
that records an op error into state — a circuit breaker is not a result.

Three properties:

- **Enforced on every run**, with or without a checkpointer. It was once
  counted inside the checkpointer's closure, which made it a no-op under a
  plain `engine.run()` — the cheap path a runaway generator is most likely
  to be in.
- **Counted per op for the whole run**, so a loop accumulates across
  iterations. That is what a per-run budget means.
- **Filtered vars are free.** A variable no observer sees costs nothing
  against the budget, which is why the error message can honestly suggest
  `@op(exclude=[…])` as a remedy.

Nothing is subscribed when no op in the graph declares a budget.

## What a trace record holds

```python
handle = engine.start(inputs={...})
await handle.result()

for node in handle.trace.nodes:
    print(node.op_full_name, node.ctx, node.status, node.duration_ms)
    print(node.inputs, node.outputs)      # already filtered
```

One `OpExecution` per invocation for a batch op, and **one per yield** for
a generator — so `op_id` is unique per yield and a downstream consumer's
`UpstreamRef` points at the exact yield it consumed. A cancelled or errored
op appends a final record carrying the failure, so the attempt is visible
rather than merely absent.
