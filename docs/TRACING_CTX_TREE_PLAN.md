# Tracing: flat records, ctx tree for Langfuse

Status: plan, 2026-09-20; rules verified on recorded runs (see the two
tree visuals in the session notes); phase 1 in progress. Scope: operonx engine + Langfuse consumer,
then the studio. The local consumer keeps its format.

## 1. What is wrong today (measured on real callbot traces)

- **Span ids collide across runs.** `op_id = full_name#ctx` is the same
  string in every call (`engine.init_state#main`). Langfuse ids are
  unique per project, so each new call takes those spans from the
  previous one: a 39-record run kept 21 spans.
- **Timestamps are 1970.** `_iso()` turns `perf_counter()` into a date.
- **The tree is guessed and broken.** `first_upstream` keeps one of
  several edges (90 of 123 records had more than one) and often points
  at the root graph or at a transient yield that was never sent.
- **Errors are silent.** Langfuse's 207 per-event errors are discarded.
- **LLM calls are plain spans**, so no model, usage or cost in Langfuse.

## 2. Design

1. **The record is flat and complete.** One `OpExecution` per
   execution, with `ctx` and every upstream edge. No tree in the record.
2. **Each viewer builds its own grouping from `ctx`.** `ctx` is the
   scheduler's dispatch path: `main.[3].[1]` = consumers of yield 1 of
   the stream dispatched by yield 3 at the level above. A generator's
   yield is itself a record carrying the ctx it dispatched, so the
   yield record is the container of everything below it. Depth costs
   nothing: three nested generators nest three deep by the same rule.
3. **Parent of a record, first hit wins** (verified on a recorded
   123-record call and a 24-record nested-generator run):
   1. ctx `main` (session-long ops): the trace itself.
   2. a level-1 yield (ctx `main.[i]`, generator op, no upstream that
      resolves to a record): the trace itself. Refs to the root
      graph's inputs point at `engine#main`, which is never a record.
   3. the upstream producer whose ctx is a prefix of, or equal to,
      this ctx (and started before it).
   4. the yield record at this ctx (same-ctx generator), then at each
      shorter prefix down to depth 2. This catches ops whose edges stop
      at a GraphOp boundary.
   5. else a **stand-in** span for the unrecorded yield `main.[i]`,
      named after the root-level transient stream (`audio_in [357]`).
      Transient streams write no per-item record, so this is the only
      synthetic node a call needs.
4. **GraphOp members** (`engine.agent_turn.*`) nest under one container
   span per (graph, ctx), placed where the first member's parent is.
5. **Names are op names.** A yield record is `synthesize [2]`: op name
   plus its own index. No "yield", no "turn" vocabulary; ctx, upstream
   edges and `is_yield` ride in metadata. Langfuse groups latency and
   counts by name, so the plain op name is the grouping key.
6. **Only two Langfuse types.** SPAN for every record, container and
   stand-in; GENERATION for `LLMOp` records (model, usage, cost).
7. **Ids are run-scoped, clocks are wall time.**

## 3. Changes

### operonx engine (`workflow_trace.py`, `base.py`, `engine.py`)
- `OpExecution` gains `wall_start: float` (epoch seconds); `WorkflowTrace`
  gains `wall_started_at`. perf fields stay for durations.
- `WorkflowTrace.run_id` (= trace_id). Consumers derive external ids as
  `f"{run_id}/{op_id}"`; `op_id` itself is unchanged so upstream refs
  still match by construction.

### Langfuse consumer (`consumers/langfuse.py`)
- Span id `"{run_id}/{op_id}"`; times from wall clock.
- Parent by the §2.3 rules; container spans per §2.4; names per §2.5.
- `LLMOp` records → `generation-create` with `model`, `usage`,
  `input`/`output`; everything else `span-create`.
- Log the 207 body when `errors` is non-empty.
- Drop `parent_strategy`; the config keeps `workflow_name`,
  `media_threshold`, `media_dir`.

### Local consumer (`consumers/local.py`)
- Write `wall_start` into `nodes.jsonl` and `wall_started_at` into
  `meta.json`. Nothing else. (Done in phase 1: same field, two lines.)

### Studio
- Traces tab: group executions by top-level ctx (turns) with the
  existing per-op drill-down inside; read `wall_start` when present.

## 4. Phases and gates

| phase | work | gate |
|---|---|---|
| 1 | engine fields + run_id; tests for wall time and id derivation | operonx suite green |
| 2 | Langfuse consumer rewrite; unit tests build the ctx tree from a recorded `nodes.jsonl` fixture (callbot run) and assert parents, ids, generation type; one integration test against Edupia Langfuse behind the existing marker | tree has no dangling parent, ids unique across two runs, dates current |
| 3 | studio grouping by level-1 yield (turns), reading `wall_start` | studio suite green; a live callbot run shows turns in both Langfuse and the studio |

Ships with operonx 1.6.0. The callbot needs no code change beyond the
bump: `resources.yaml` loses `parent_strategy`.

## 5. Out of scope

Live streaming of spans during the call (still one batch post-call),
media upload to Langfuse (refs stay local), the V2 "explicit event()"
plan in `TRACING_V2_PLAN.md` (superseded by this one).
