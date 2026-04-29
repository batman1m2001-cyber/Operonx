# operonx.core

Engine, op decorators, graph composition, state markers, and middleware.
This page is the primary public surface — everything you need to build
and run a workflow without touching providers or telemetry.

## Engine

::: operonx.core.engine.Operon

## Decorators

The two decorators that turn ordinary Python into Operonx ops:

::: operonx.core.ops.op
::: operonx.core.ops.graph

## Op types

The base classes that compose into a workflow. Most users only touch
`GraphOp` directly (via `with GraphOp(...) as g:`) — the others are
constructed by decorators or factory helpers.

::: operonx.core.ops.GraphOp
::: operonx.core.ops.BranchOp
::: operonx.core.ops.FuncOp
::: operonx.core.ops.ParserOp

## Branch helpers

::: operonx.core.ops.Branch
::: operonx.core.ops.if_

## State markers

Constants used inside `with GraphOp(...)` blocks to wire edges and
references. None of these are real instances you'd construct — they're
sentinels the graph builder recognises.

| Marker | Meaning |
|---|---|
| `START` | Entry node. Every graph's first hard edge goes from `START`. |
| `END` | Exit node. `op >> END` auto-forwards `op`'s outputs as the graph result. |
| `PARENT` | Reference root for inputs from `engine.run(inputs={...})` or the parent graph in nested contexts. Used as `PARENT["key"]`. |
| `PENDING` | Sentinel returned by ops that absorb input without producing output. |

## Middleware

Hook into engine lifecycle events — see [Tracing](../guide/07-tracing.md)
for built-in tracers and middleware patterns.

::: operonx.core.middleware.Middleware

## Top-level convenience

::: operonx.bootstrap

## Provider-neutral types

The v0.7 LLMOp converter layer will translate provider-specific types
to/from these at the provider boundary.

::: operonx.core.types.ChatMessage
::: operonx.core.types.ChatRole
