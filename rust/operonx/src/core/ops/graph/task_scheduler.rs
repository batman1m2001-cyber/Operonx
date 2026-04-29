//! Task-based workflow scheduler.
//!
//! Mirrors Python [`operonx/core/ops/graph/task_scheduler.py`](../../../../../operonx/core/ops/graph/task_scheduler.py).
//! One scheduler per workflow execution; event-driven over a bounded mpsc
//! channel. `Frame` events carry op results forward, `Eof` events flush
//! collect buffers and advance loop iterations.
//!
//! # Scope
//! - Three dispatch modes per edge, selected by the destination op's input
//!   `RefConfig.stream_policy`:
//!   * **Sequential** (default) — one item at a time; subsequent items
//!     queued until the predecessor Eof arrives.
//!   * **Parallel** (`StreamPolicy::parallel = true`) — every incoming frame
//!     dispatches `dst` immediately; concurrency capped by the graph's
//!     `max_stream_concurrent` semaphore (Python parity).
//!   * **Collect** (`StreamPolicy::collect = true`) — frames accumulate into
//!     a buffer keyed by `(src, dst)`; on src's Eof the values are merged
//!     per-key into lists and `dst` is dispatched **once** under a new
//!     `"__collect__"` sub-context.
//! - Soft edges recognized per plan §4b (one soft predecessor unblocks).
//! - Loop re-dispatch on the top-level graph via [`OpConfig::loop_config`].
//! - Dispatches `code`-type ops through the [`OpRegistry`], provider ops
//!   through [`providers::ops::factory`](crate::providers::ops::factory).

use std::collections::{HashMap, VecDeque};
use std::sync::Arc;

use async_trait::async_trait;
use parking_lot::Mutex;
use serde_json::{Map, Value};
use tokio::sync::{mpsc, Semaphore};
use tokio_util::sync::CancellationToken;

use crate::core::configs::op_config::{CompiledLink, LoopConfig, OpBound, OpConfig, OpType};
use crate::core::engine::{FrameEvent, FrameSender, Scheduler};
use crate::core::exceptions::{OpError, OperonError};
use crate::core::middleware::MiddlewareContext;
use crate::core::ops::edges::PARENT;
use crate::core::registry::OpRegistry;
use crate::core::states::cell::{default_context, ContextId};
use crate::core::states::ref_::{RefArg, RefConfig, RefTransform, StreamPolicy};

// ── State slot key ────────────────────────────────────────────────────────

/// `(op_full_name, var_name, context)` — one slot in the runtime state map.
///
/// Parent-walks on context happen at read time (see [`RuntimeState::get`]).
type SlotKey = (String, String, ContextId);

/// Per-execution runtime state.
///
/// Phase 4 uses this instead of the full [`MemoryState`](crate::core::states::state::MemoryState).
/// Phase 7 will unify them; `MemoryState` is still empty of the concurrency
/// layer today. This scheduler-local map is intentionally dumb — one lock
/// wraps the whole thing; the per-op critical sections are tiny anyway.
#[derive(Debug, Default)]
struct RuntimeState {
    slots: HashMap<SlotKey, Value>,
}

impl RuntimeState {
    #[allow(dead_code)]
    fn new() -> Self {
        Self::default()
    }

    /// Pre-size the slot map to roughly fit the graph. Avoids the
    /// log(n) resize cycle that happens when ops `set()` slot keys
    /// during execution.
    fn with_capacity(cap: usize) -> Self {
        Self {
            slots: HashMap::with_capacity(cap),
        }
    }

    /// Store a value at `(op, var, ctx)`.
    fn set(&mut self, op: &str, var: &str, ctx: &ContextId, value: Value) {
        self.slots
            .insert((op.to_string(), var.to_string(), ctx.clone()), value);
    }

    /// Read at `(op, var, ctx)` with a parent-walk up the context tuple.
    fn get(&self, op: &str, var: &str, ctx: &ContextId) -> Option<&Value> {
        let mut probe = ctx.clone();
        loop {
            if let Some(v) = self
                .slots
                .get(&(op.to_string(), var.to_string(), probe.clone()))
            {
                return Some(v);
            }
            if probe.is_empty() {
                return None;
            }
            probe.pop();
        }
    }
}

// ── Internal scheduler events ────────────────────────────────────────────

/// Events pushed onto the scheduler's internal queue. Matches Python's
/// `Frame` + `EOF` variants (§4b.12).
#[derive(Debug)]
enum SchedulerEvent {
    Frame {
        op: String,
        ctx: ContextId,
        result: Map<String, Value>,
    },
    Eof {
        op: String,
        ctx: ContextId,
    },
}

// ── GraphScheduler ───────────────────────────────────────────────────────

/// The real [`Scheduler`] implementation — walks a [`OpConfig`] graph and
/// drives it to completion.
pub struct GraphScheduler {
    graph: Arc<OpConfig>,
    registry: Arc<dyn OpRegistry>,
    /// Cached `{op_name: [(src_var, dst_var), ...]}` — set of vars whose
    /// `outputs[var].ref` points at PARENT with `is_output = true`. These are
    /// the ones the scheduler forwards to the external frame stream.
    out_vars: HashMap<String, Vec<(String, String)>>,
    /// Pre-built sub-schedulers for every nested `OpType::Graph` op, keyed
    /// by the child op's `full_name`. Built recursively at parent
    /// construction so the runtime hot path never builds a sub-engine —
    /// nested dispatch is a single map lookup + inline `run_collect`.
    /// Wrapped in `Arc` for cheap cloning into spawned op-execution
    /// tasks (the io/cpu dispatch path uses `tokio::spawn` with a `move`
    /// closure).
    child_schedulers: Arc<HashMap<String, Arc<GraphScheduler>>>,
    /// Pre-converted `initial_ready_count` (the `BTreeMap` lives on
    /// [`OpConfig`]). Cloned per context every time a fresh frame opens
    /// a never-seen `ContextId`; pre-converting once at construction
    /// drops a `clone()` + `into_iter().collect()` from `run_once` and
    /// `on_frame`.
    initial_ready_hm: HashMap<String, i32>,
    /// Pre-counted `(op_full_name, var)` pairs across the graph; used as
    /// the initial capacity for [`RuntimeState::slots`] so the per-run
    /// `HashMap` never grows in steps. Hash resizes amortise to ~zero
    /// for graphs that build the slot table once and never delete.
    slot_capacity: usize,
    /// Pre-counted edge totals to size the per-run sequential and
    /// collect bookkeeping `HashMap`s. Sequential edges and collect
    /// edges are mutually exclusive on a given `(src, dst)` link, so
    /// each link contributes to exactly one of the two counters.
    seq_edge_capacity: usize,
    collect_edge_capacity: usize,
    /// Cached `(src, dst) -> StreamPolicy` lookups. `route_edge_async`
    /// fires once per Frame per outgoing edge; the old per-call
    /// `edge_policy()` walked `dst.inputs` and string-matched. Map
    /// lookup is O(1) by hash — and on a 50-edge graph with 100 frames
    /// that's 5,000 walks → 5,000 hash lookups.
    edge_policies: HashMap<(String, String), StreamPolicy>,
    /// Pre-compiled per-op input plans. One `Vec<InputSlot>` per op,
    /// preserving the input declaration order. Each slot carries either
    /// a `CompiledRef` (resolved against state at runtime) or a
    /// pre-cloned literal/default value. The runtime walks the slot
    /// list once per op invocation — no `match param.ref_config`
    /// per input, no transform-name string match. PARENT is already
    /// substituted with the graph key at compile time. Wrapped in
    /// `Arc` so the spawn (io/cpu) path clones cheaply into the
    /// `tokio::spawn` move closure.
    input_plans: Arc<HashMap<String, Vec<InputSlot>>>,
    /// Pre-compiled branch case conditions, keyed by branch op name.
    /// Each entry is `(Vec<(CompiledRef, target)>, Option<default>)`.
    /// Empty for graphs with no branch ops.
    branches: Arc<HashMap<String, (Vec<(CompiledRef, String)>, Option<String>)>>,
}

impl std::fmt::Debug for GraphScheduler {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("GraphScheduler")
            .field("graph_name", &self.graph.name)
            .field("op_count", &self.graph.ops.len())
            .finish()
    }
}

impl GraphScheduler {
    /// Build a scheduler for `graph`. Validates that the top-level op is a
    /// graph, pre-computes PARENT-bound output var mappings, and
    /// recursively constructs sub-schedulers for every nested
    /// `OpType::Graph` so the runtime nested-dispatch path is a single
    /// map lookup.
    pub fn new(graph: Arc<OpConfig>, registry: Arc<dyn OpRegistry>) -> Result<Self, OperonError> {
        if !graph.is_graph() {
            return Err(OperonError::Config(format!(
                "top-level op must be a graph, got {:?}",
                graph.kind
            )));
        }
        let out_vars = compute_out_vars(&graph);
        let child_schedulers = Arc::new(build_child_schedulers(&graph, &registry)?);

        // Pre-convert `initial_ready_count` BTreeMap → HashMap once.
        let initial_ready_hm: HashMap<String, i32> = graph
            .initial_ready_count
            .iter()
            .map(|(k, v)| (k.clone(), *v))
            .collect();

        // Count `(op_full_name, var)` slot pairs across the graph for
        // RuntimeState pre-sizing. Each op contributes one slot per
        // input + one per output; over-estimating is fine.
        let slot_capacity: usize = graph
            .ops
            .values()
            .map(|op| op.inputs.len() + op.outputs.len())
            .sum::<usize>()
            .max(16);

        // Tally seq-vs-collect edge counts via the same policy lookup
        // we're about to cache. A single pass populates both
        // `edge_policies` and the capacity hints.
        let mut edge_policies: HashMap<(String, String), StreamPolicy> = HashMap::new();
        let mut seq_edge_capacity = 0usize;
        let mut collect_edge_capacity = 0usize;
        for (src_name, links) in &graph.compiled_adj {
            for link in links {
                let key = (src_name.clone(), link.dst.clone());
                let policy = resolve_edge_policy(&graph, src_name, &link.dst);
                if let Some(p) = policy {
                    edge_policies.insert(key.clone(), p);
                    if p.collect {
                        collect_edge_capacity += 1;
                    } else {
                        seq_edge_capacity += 1;
                    }
                } else {
                    // No policy = sequential default; still counts.
                    seq_edge_capacity += 1;
                }
            }
        }

        // Pre-compile every input ref + branch case condition.
        // `graph_key` substitutes the PARENT sentinel at compile time
        // so `eval_ref` skips the per-call branch.
        let graph_key_owned = if graph.full_name.is_empty() {
            graph.name.clone()
        } else {
            graph.full_name.clone()
        };
        let input_plans = Arc::new(compile_input_plans(&graph, &graph_key_owned));
        let branches = Arc::new(compile_branches(&graph, &graph_key_owned));

        Ok(Self {
            graph,
            registry,
            out_vars,
            child_schedulers,
            initial_ready_hm,
            slot_capacity,
            seq_edge_capacity: seq_edge_capacity.max(4),
            collect_edge_capacity: collect_edge_capacity.max(4),
            edge_policies,
            input_plans,
            branches,
        })
    }

    /// Full name of the workflow (the key ops use to address PARENT-level
    /// state).
    fn graph_key(&self) -> &str {
        if self.graph.full_name.is_empty() {
            &self.graph.name
        } else {
            &self.graph.full_name
        }
    }

    /// Inline nested-dispatch fast-path. Runs this scheduler in the
    /// caller's task with a tap-only `FrameSender` (no `tokio::spawn`,
    /// no `mpsc::channel(64)` allocation, no `pump_loop`, no UUID gen,
    /// no middleware). Returns the aggregated PARENT-bound outputs as
    /// a `Map<String, Value>` ready to flow into the calling op's
    /// frame.
    ///
    /// This is what `OpType::Graph` calls in `execute_op` instead of
    /// the heavy `Operon::run_json_async` round-trip.
    pub async fn run_collect(&self, inputs: Map<String, Value>) -> Result<Value, OperonError> {
        use crate::core::engine::{FrameSender, TraceTap};

        let tap: TraceTap = Arc::new(Mutex::new(Vec::new()));
        let sender = FrameSender::tap_only(tap.clone());
        let cancel = CancellationToken::new();
        let ctx = MiddlewareContext::default();

        // `run` only emits external frames via `sender.send` for
        // PARENT-bound output ops (and the loop summary frame), so the
        // tap accumulates exactly what we want and nothing else.
        Scheduler::run(self, inputs, ctx, sender, cancel).await?;

        // Merge every captured frame's `data` map. Multiple output ops
        // each contribute their dst_var → value pairs; later frames
        // overwrite earlier ones for the same key (Python parity).
        let frames = std::mem::take(&mut *tap.lock());
        let mut out: Map<String, Value> = Map::new();
        for frame in frames {
            for (k, v) in frame.data {
                out.insert(k, v);
            }
        }
        Ok(Value::Object(out))
    }
}

/// Recursively build sub-schedulers for every nested `OpType::Graph` in
/// `graph`. Each child gets its own `GraphScheduler` (with its own child
/// map, recursively) so the runtime never builds a sub-engine on the
/// hot path. Sub-schedulers share the parent's `OpRegistry` so
/// `#[op]`-registered functions resolve identically at every depth.
fn build_child_schedulers(
    graph: &OpConfig,
    registry: &Arc<dyn OpRegistry>,
) -> Result<HashMap<String, Arc<GraphScheduler>>, OperonError> {
    let mut out: HashMap<String, Arc<GraphScheduler>> = HashMap::new();
    for (_, child) in &graph.ops {
        if matches!(child.kind, OpType::Graph) {
            let sub = Arc::new(GraphScheduler::new(
                Arc::new(child.clone()),
                registry.clone(),
            )?);
            out.insert(child.full_name.clone(), sub);
        }
    }
    Ok(out)
}

#[async_trait]
impl Scheduler for GraphScheduler {
    async fn run(
        &self,
        inputs: Map<String, Value>,
        _context: MiddlewareContext,
        sender: FrameSender,
        cancel: CancellationToken,
    ) -> Result<(), OperonError> {
        let state = Arc::new(Mutex::new(RuntimeState::with_capacity(self.slot_capacity)));

        // Seed PARENT-level inputs at root context. Caller-provided `inputs`
        // override graph-declared literals (e.g. `GraphOp.loop(count=0)` is
        // serialized as `graph.inputs.count.literal = 0` and must be picked
        // up when the user doesn't pass an explicit value).
        let root_ctx = default_context();
        {
            let mut s = state.lock();
            for (k, param) in &self.graph.inputs {
                if inputs.contains_key(k) {
                    continue;
                }
                if let Some(lit) = &param.literal {
                    s.set(self.graph_key(), k, &root_ctx, lit.clone());
                } else if let Some(def) = &param.default {
                    s.set(self.graph_key(), k, &root_ctx, def.clone());
                }
            }
            for (k, v) in &inputs {
                s.set(self.graph_key(), k, &root_ctx, v.clone());
            }
        }

        // Drive the graph once (loop re-dispatch below).
        self.run_once(state.clone(), root_ctx.clone(), &sender, &cancel)
            .await?;

        // Top-level loop re-dispatch.
        if let Some(loop_cfg) = &self.graph.loop_config {
            let max_iters = loop_cfg.max_iterations.unwrap_or(1000).max(1);
            let mut current_ctx = root_ctx.clone();
            let mut n_iters: u32 = 0;

            while !self.loop_should_stop(loop_cfg, state.clone(), &current_ctx)? {
                if n_iters >= max_iters - 1 {
                    break;
                }
                n_iters += 1;
                // Build the next iteration's context: (… , "loop_N").
                let next_ctx = next_loop_ctx(&current_ctx, n_iters);

                // Carry graph-level outputs forward as the next iter's inputs.
                let carry = self.collect_graph_outputs(state.clone(), &current_ctx);
                {
                    let mut s = state.lock();
                    for (var, val) in carry {
                        s.set(self.graph_key(), &var, &next_ctx, val);
                    }
                }

                // Python parity: only the first iteration emits per-op
                // frames on the public sender. Subsequent iterations run
                // silent; only the final summary frame below is emitted.
                self.run_once(state.clone(), next_ctx.clone(), &sender.silent(), &cancel)
                    .await?;
                current_ctx = next_ctx;
            }

            if n_iters > 0 {
                // Emit the final outputs at the last iteration's context.
                let final_map = self.collect_graph_outputs(state.clone(), &current_ctx);
                if !final_map.is_empty() {
                    sender
                        .send(FrameEvent {
                            op: self.graph.name.clone(),
                            context: current_ctx,
                            data: final_map,
                        })
                        .await?;
                }
            }
        }

        Ok(())
    }
}

impl GraphScheduler {
    async fn run_once(
        &self,
        state: Arc<Mutex<RuntimeState>>,
        ctx: ContextId,
        sender: &FrameSender,
        cancel: &CancellationToken,
    ) -> Result<(), OperonError> {
        // ── Per-run mutable bookkeeping ───────────────────────────────────
        let mut ready: HashMap<ContextId, HashMap<String, i32>> = HashMap::new();
        ready.insert(ctx.clone(), self.initial_ready_hm.clone());

        let mut inflight: i32 = 0;

        // Sequential per-edge queueing — matches Python seq_queues/seq_active.
        // Pre-sized to the graph's seq edge count so the per-frame
        // `entry().or_default()` calls don't trigger HashMap resizes
        // mid-run.
        let mut seq_queues: HashMap<(String, String), VecDeque<ContextId>> =
            HashMap::with_capacity(self.seq_edge_capacity);
        let mut seq_active: HashMap<(String, String), bool> =
            HashMap::with_capacity(self.seq_edge_capacity);
        let mut seq_origins: HashMap<(String, ContextId), (String, String)> = HashMap::new();

        // Collect buffers — `(src, dst) → [(frame_ctx, result), ...]`. Python
        // parity: flushed when src emits Eof; merged per-key into lists, one
        // dispatch of dst under a `__collect__` sub-context.
        let mut collect_bufs: HashMap<(String, String), Vec<(ContextId, Map<String, Value>)>> =
            HashMap::with_capacity(self.collect_edge_capacity);

        let sem = Arc::new(Semaphore::new(
            self.graph.max_stream_concurrent.max(1) as usize
        ));

        // Internal event queue — op execution tasks post here, the main loop
        // drains it. Sized generously so the inline fast path (sync ops that
        // bypass `tokio::spawn` and post events from inside `on_frame`) can
        // never deadlock by filling the channel mid-handler.
        let (tx, mut rx) = mpsc::channel::<SchedulerEvent>(8192);

        // ── Seed entry ops ────────────────────────────────────────────────
        for entry in &self.graph.entries {
            inflight += 1;
            self.spawn_op(
                entry.clone(),
                ctx.clone(),
                state.clone(),
                tx.clone(),
                sem.clone(),
                cancel.clone(),
            )
            .await?;
        }

        // ── Main event loop ───────────────────────────────────────────────
        //
        // `inflight` counts outstanding *tasks* (pre-`Eof`) — **not** events
        // in the queue. `Frame` events are handled then discarded without
        // decrementing, because an op's Frame doesn't signal completion (the
        // matching `Eof` does). Dispatching downstream ops inside an on_frame
        // or on_eof handler bumps `inflight` back up.
        while inflight > 0 {
            tokio::select! {
                _ = cancel.cancelled() => {
                    return Err(OperonError::Runtime("workflow cancelled".into()));
                }
                maybe_ev = rx.recv() => {
                    let ev = match maybe_ev {
                        Some(ev) => ev,
                        None => break,
                    };
                    match ev {
                        SchedulerEvent::Frame { op, ctx: frame_ctx, result } => {
                            self.on_frame(
                                &op,
                                &frame_ctx,
                                &result,
                                &mut ready,
                                &mut seq_queues,
                                &mut seq_active,
                                &mut seq_origins,
                                &mut collect_bufs,
                                &mut inflight,
                                state.clone(),
                                tx.clone(),
                                sem.clone(),
                                cancel,
                                sender,
                            )
                            .await?;
                        }
                        SchedulerEvent::Eof { op, ctx: eof_ctx } => {
                            // One task finished — decrement before handing off
                            // to `on_eof`, which may itself re-dispatch (bumps
                            // inflight back up).
                            inflight -= 1;
                            self.on_eof(
                                &op,
                                &eof_ctx,
                                &mut seq_queues,
                                &mut seq_active,
                                &mut seq_origins,
                                &mut collect_bufs,
                                &mut inflight,
                                state.clone(),
                                tx.clone(),
                                sem.clone(),
                                cancel,
                            )
                            .await?;
                        }
                    }
                }
            }
        }

        Ok(())
    }

    /// Dispatch one op — resolve its inputs, call the function, push Frame/Eof
    /// events back onto the scheduler's internal queue.
    ///
    /// Two dispatch paths:
    ///
    /// * **Inline (`bound: Sync`)** — runs in the caller's task with no
    ///   `tokio::spawn`, no semaphore acquire, and the events go onto the
    ///   queue via `try_send`. This is the hot path: a 500-op linear chain
    ///   of sync ops doesn't pay 500 task spawns + 500 semaphore awaits.
    /// * **Spawned (`bound: Io | Cpu`)** — existing `tokio::spawn` path with
    ///   the concurrency semaphore. Required for ops that may yield on I/O
    ///   or do real CPU work that would block the scheduler task.
    ///
    /// The op's `Future` is awaited in both paths; for sync ops the
    /// future-from-`#[op]` resolves on the first poll without yielding so
    /// "inline" is essentially synchronous.
    async fn spawn_op(
        &self,
        op_name: String,
        ctx: ContextId,
        state: Arc<Mutex<RuntimeState>>,
        tx: mpsc::Sender<SchedulerEvent>,
        sem: Arc<Semaphore>,
        cancel: CancellationToken,
    ) -> Result<(), OperonError> {
        let op_cfg = self
            .graph
            .ops
            .get(&op_name)
            .ok_or_else(|| OperonError::Config(format!("op '{}' not in graph", op_name)))?
            .clone();
        let registry = self.registry.clone();
        let child_schedulers = self.child_schedulers.clone();
        // One HashMap lookup per op invocation (not per input). The
        // returned `&[InputSlot]` borrows from `self.input_plans`; in
        // the spawn path we clone the slice into the closure as a
        // small `Vec<InputSlot>` (bounded by the op's input count, ~8
        // for typical ops).
        let plan_slice: &[InputSlot] = self
            .input_plans
            .get(&op_name)
            .map(|v| v.as_slice())
            .unwrap_or(&[]);

        // ── Inline fast path for sync ops ──────────────────────────────────
        if matches!(op_cfg.bound, OpBound::Sync) {
            if cancel.is_cancelled() {
                return Ok(());
            }
            // Frames to post: one per "yield" for generators, exactly one
            // for everything else. Each entry is `(ctx, frame_map)`; a
            // single trailing `Eof` on the original `ctx` follows.
            let frames: Vec<(ContextId, Map<String, Value>)> =
                if matches!(op_cfg.kind, OpType::Branch) {
                    // Branch ops never call user code — the "execution" is a
                    // condition-eval loop over `op_cfg.cases`. Done inline so
                    // we can read state without re-locking through the
                    // resolve_inputs/execute_op split.
                    let single = match evaluate_branch(&op_cfg, &self.branches, &ctx, &state) {
                        Ok(target) => {
                            let mut m = Map::new();
                            m.insert("__branch_target__".into(), Value::from(target.clone()));
                            {
                                let mut s = state.lock();
                                s.set(
                                    &op_cfg.full_name,
                                    "__branch_target__",
                                    &ctx,
                                    Value::from(target),
                                );
                            }
                            m
                        }
                        Err(e) => {
                            {
                                let mut s = state.lock();
                                s.set(&op_cfg.full_name, "error", &ctx, Value::from(e.to_string()));
                            }
                            error_frame(&e)
                        }
                    };
                    vec![(ctx.clone(), single)]
                } else {
                    match resolve_inputs(&op_cfg, plan_slice, &ctx, &state) {
                        Err(e) => vec![(ctx.clone(), error_frame(&e))],
                        Ok(inputs) => {
                            match execute_op(&op_cfg, &registry, inputs, &self.child_schedulers)
                                .await
                            {
                                Ok(value) => fan_out_value(&op_cfg, &ctx, value, &state),
                                Err(e) => {
                                    {
                                        let mut s = state.lock();
                                        s.set(
                                            &op_cfg.full_name,
                                            "error",
                                            &ctx,
                                            Value::from(e.to_string()),
                                        );
                                    }
                                    vec![(ctx.clone(), error_frame(&e))]
                                }
                            }
                        }
                    }
                };

            // Post Frame events (one per generator yield, or just one) +
            // a single trailing Eof on the parent context.
            // `try_send` keeps the inline path zero-await on the happy
            // path. Channel is sized generously (8192) so this is
            // overflow-only; fall back to `send().await` if it ever fills.
            for (frame_ctx, frame_map) in frames {
                let ev = SchedulerEvent::Frame {
                    op: op_name.clone(),
                    ctx: frame_ctx,
                    result: frame_map,
                };
                if let Err(tokio::sync::mpsc::error::TrySendError::Full(ev)) = tx.try_send(ev) {
                    let _ = tx.send(ev).await;
                }
            }
            let eof = SchedulerEvent::Eof {
                op: op_name,
                ctx: ctx.clone(),
            };
            if let Err(tokio::sync::mpsc::error::TrySendError::Full(ev)) = tx.try_send(eof) {
                let _ = tx.send(ev).await;
            }
            return Ok(());
        }

        // ── Spawn path for io/cpu ──────────────────────────────────────────
        let owned_plan: Vec<InputSlot> = plan_slice.to_vec();
        tokio::spawn(async move {
            let _permit = match sem.acquire_owned().await {
                Ok(p) => p,
                Err(_) => return, // semaphore closed — nothing more to do
            };
            if cancel.is_cancelled() {
                return;
            }

            let inputs = match resolve_inputs(&op_cfg, &owned_plan, &ctx, &state) {
                Ok(m) => m,
                Err(e) => {
                    let _ = tx
                        .send(SchedulerEvent::Frame {
                            op: op_name.clone(),
                            ctx: ctx.clone(),
                            result: error_frame(&e),
                        })
                        .await;
                    let _ = tx.send(SchedulerEvent::Eof { op: op_name, ctx }).await;
                    return;
                }
            };

            let exec_result = execute_op(&op_cfg, &registry, inputs, &child_schedulers).await;

            match exec_result {
                Ok(value) => {
                    let frames = fan_out_value(&op_cfg, &ctx, value, &state);
                    for (frame_ctx, frame_map) in frames {
                        let _ = tx
                            .send(SchedulerEvent::Frame {
                                op: op_name.clone(),
                                ctx: frame_ctx,
                                result: frame_map,
                            })
                            .await;
                    }
                    let _ = tx.send(SchedulerEvent::Eof { op: op_name, ctx }).await;
                }
                Err(e) => {
                    {
                        let mut s = state.lock();
                        s.set(&op_cfg.full_name, "error", &ctx, Value::from(e.to_string()));
                    }
                    let _ = tx
                        .send(SchedulerEvent::Frame {
                            op: op_name.clone(),
                            ctx: ctx.clone(),
                            result: error_frame(&e),
                        })
                        .await;
                    let _ = tx.send(SchedulerEvent::Eof { op: op_name, ctx }).await;
                }
            }
        });

        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    async fn on_frame(
        &self,
        op: &str,
        ctx: &ContextId,
        result: &Map<String, Value>,
        ready: &mut HashMap<ContextId, HashMap<String, i32>>,
        seq_queues: &mut HashMap<(String, String), VecDeque<ContextId>>,
        seq_active: &mut HashMap<(String, String), bool>,
        seq_origins: &mut HashMap<(String, ContextId), (String, String)>,
        collect_bufs: &mut HashMap<(String, String), Vec<(ContextId, Map<String, Value>)>>,
        inflight: &mut i32,
        state: Arc<Mutex<RuntimeState>>,
        tx: mpsc::Sender<SchedulerEvent>,
        sem: Arc<Semaphore>,
        cancel: &CancellationToken,
        sender: &FrameSender,
    ) -> Result<(), OperonError> {
        // Seed ready counts for a never-seen context.
        if !ready.contains_key(ctx) {
            ready.insert(ctx.clone(), self.initial_ready_hm.clone());
        }

        // Forward any PARENT-bound output vars to the external frame stream,
        // and also persist them to graph-level slots so `result()` / `collect()`
        // aggregate cleanly.
        if let Some(mapped) = self.out_vars.get(op) {
            let mut filtered = Map::new();
            let graph_key = self.graph_key().to_string();
            {
                let mut s = state.lock();
                for (src_var, dst_var) in mapped {
                    if let Some(v) = result.get(src_var) {
                        s.set(&graph_key, dst_var, ctx, v.clone());
                        filtered.insert(dst_var.clone(), v.clone());
                    }
                }
            }
            if !filtered.is_empty() {
                sender
                    .send(FrameEvent {
                        op: op.to_string(),
                        context: ctx.clone(),
                        data: filtered,
                    })
                    .await?;
            }
        }

        // Branch routing (if the op emitted `__branch_target__`, only that edge fires).
        let branch_target = result
            .get("__branch_target__")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());

        let Some(adj) = self.graph.compiled_adj.get(op) else {
            return Ok(());
        };
        let adj = adj.clone();

        for link in adj {
            if let Some(target) = &branch_target {
                if &link.dst != target {
                    continue;
                }
            }
            self.route_edge_async(
                op,
                &link,
                ctx,
                result,
                ready,
                seq_queues,
                seq_active,
                seq_origins,
                collect_bufs,
                inflight,
                state.clone(),
                tx.clone(),
                sem.clone(),
                cancel,
            )
            .await?;
        }
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    async fn route_edge_async(
        &self,
        src: &str,
        link: &CompiledLink,
        ctx: &ContextId,
        result: &Map<String, Value>,
        ready: &mut HashMap<ContextId, HashMap<String, i32>>,
        seq_queues: &mut HashMap<(String, String), VecDeque<ContextId>>,
        seq_active: &mut HashMap<(String, String), bool>,
        seq_origins: &mut HashMap<(String, ContextId), (String, String)>,
        collect_bufs: &mut HashMap<(String, String), Vec<(ContextId, Map<String, Value>)>>,
        inflight: &mut i32,
        state: Arc<Mutex<RuntimeState>>,
        tx: mpsc::Sender<SchedulerEvent>,
        sem: Arc<Semaphore>,
        cancel: &CancellationToken,
    ) -> Result<(), OperonError> {
        let rc = ready.get_mut(ctx).expect("ready entry seeded earlier");
        let Some(count) = rc.get_mut(&link.dst) else {
            return Ok(());
        };
        if link.soft && *count <= 0 {
            return Ok(());
        }
        *count -= 1;
        if *count != 0 {
            return Ok(());
        }

        // Consult the dst op's per-var stream policy — pre-cached at
        // construction in `edge_policies`. Python's equivalent lookup
        // lives in task_scheduler.py _route.
        let policy = self
            .edge_policies
            .get(&(src.to_string(), link.dst.clone()))
            .copied();

        // `Collect` — buffer the frame and wait for src's Eof.
        if let Some(p) = &policy {
            if p.collect {
                collect_bufs
                    .entry((src.to_string(), link.dst.clone()))
                    .or_default()
                    .push((ctx.clone(), result.clone()));
                return Ok(());
            }
            if p.parallel {
                // Each frame dispatches immediately; the semaphore caps real
                // concurrency at `max_stream_concurrent` (or `parallel_max`
                // when finer control is desired — left for a follow-up).
                *inflight += 1;
                self.spawn_op(
                    link.dst.clone(),
                    ctx.clone(),
                    state,
                    tx,
                    sem,
                    cancel.clone(),
                )
                .await?;
                return Ok(());
            }
        }

        // Sequential — one item at a time; queue the rest.
        let key = (src.to_string(), link.dst.clone());
        if !*seq_active.entry(key.clone()).or_insert(false) {
            *seq_active.get_mut(&key).unwrap() = true;
            seq_origins.insert((link.dst.clone(), ctx.clone()), key.clone());
            *inflight += 1;
            self.spawn_op(
                link.dst.clone(),
                ctx.clone(),
                state,
                tx,
                sem,
                cancel.clone(),
            )
            .await?;
        } else {
            seq_queues.entry(key).or_default().push_back(ctx.clone());
        }
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    async fn on_eof(
        &self,
        op: &str,
        ctx: &ContextId,
        seq_queues: &mut HashMap<(String, String), VecDeque<ContextId>>,
        seq_active: &mut HashMap<(String, String), bool>,
        seq_origins: &mut HashMap<(String, ContextId), (String, String)>,
        collect_bufs: &mut HashMap<(String, String), Vec<(ContextId, Map<String, Value>)>>,
        inflight: &mut i32,
        state: Arc<Mutex<RuntimeState>>,
        tx: mpsc::Sender<SchedulerEvent>,
        sem: Arc<Semaphore>,
        cancel: &CancellationToken,
    ) -> Result<(), OperonError> {
        // Flush any `collect` buffers sourced from this op. Per Python parity:
        // merge per-key into lists, persist the merged result to src's state
        // under a fresh `__collect__` sub-context, then dispatch dst once.
        let keys: Vec<(String, String)> = collect_bufs
            .keys()
            .filter(|(src, _dst)| src == op)
            .cloned()
            .collect();
        for key in keys {
            let buf = collect_bufs.remove(&key).unwrap_or_default();
            if buf.is_empty() {
                continue;
            }
            let mut merged: Map<String, Value> = Map::new();
            for (_c, r) in &buf {
                for (k, v) in r {
                    let entry = merged
                        .entry(k.clone())
                        .or_insert_with(|| Value::Array(Vec::new()));
                    if let Value::Array(arr) = entry {
                        arr.push(v.clone());
                    }
                }
            }
            let mut collect_ctx = ctx.clone();
            collect_ctx.push("__collect__".to_string());

            let src_full = self
                .graph
                .ops
                .get(&key.0)
                .map(|o| o.full_name.clone())
                .unwrap_or_else(|| key.0.clone());
            {
                let mut s = state.lock();
                for (k, v) in &merged {
                    s.set(&src_full, k, &collect_ctx, v.clone());
                }
            }
            *inflight += 1;
            self.spawn_op(
                key.1.clone(),
                collect_ctx,
                state.clone(),
                tx.clone(),
                sem.clone(),
                cancel.clone(),
            )
            .await?;
        }

        // Advance the sequential queue if this EOF unblocks a following item.
        if let Some(key) = seq_origins.remove(&(op.to_string(), ctx.clone())) {
            if let Some(q) = seq_queues.get_mut(&key) {
                if let Some(next_ctx) = q.pop_front() {
                    seq_origins.insert((key.1.clone(), next_ctx.clone()), key.clone());
                    *inflight += 1;
                    self.spawn_op(key.1.clone(), next_ctx, state, tx, sem, cancel.clone())
                        .await?;
                } else {
                    seq_active.insert(key, false);
                }
            } else {
                seq_active.insert(key, false);
            }
        }
        Ok(())
    }

    /// Read the graph-level outputs (all vars declared in `graph.outputs`) at
    /// `ctx`.
    fn collect_graph_outputs(
        &self,
        state: Arc<Mutex<RuntimeState>>,
        ctx: &ContextId,
    ) -> Map<String, Value> {
        let mut out = Map::new();
        let s = state.lock();
        for var in self.graph.outputs.keys() {
            if let Some(v) = s.get(self.graph_key(), var, ctx) {
                out.insert(var.clone(), v.clone());
            }
        }
        out
    }

    /// Evaluate the top-level graph's `loop_config.until`. Returns `true` when
    /// the loop should stop.
    fn loop_should_stop(
        &self,
        loop_cfg: &LoopConfig,
        state: Arc<Mutex<RuntimeState>>,
        ctx: &ContextId,
    ) -> Result<bool, OperonError> {
        let Some(expr) = loop_cfg.until.as_deref() else {
            return Ok(false);
        };
        let outputs = self.collect_graph_outputs(state, ctx);
        eval_until(expr, &outputs)
    }
}

// ── Compiled ref-transform pipeline ──────────────────────────────────────
//
// At construction time we walk every `RefConfig` in the graph (op-input
// refs + branch case conditions, recursively into nested refs) and
// produce a flattened, enum-tagged form. The runtime hot path then
// reads one `CompiledRef` per input via a HashMap lookup and dispatches
// each step through a single `match` on `TransformKind` — no
// `transform.name.as_str()` walk, no `if source == PARENT` branch
// (PARENT is already substituted with the graph key at compile time).
//
// `Unknown(name)` falls through to a runtime `not implemented` error,
// matching the previous string-match path's behaviour for ops we haven't
// promoted (e.g. `apply`, `call`, `matmul`).

#[derive(Debug, Clone)]
struct CompiledRef {
    /// Final state-source key — `__PARENT__` already replaced with the
    /// enclosing graph's key.
    source: String,
    var: String,
    /// Transform pipeline in evaluation order. Empty for plain refs.
    chain: Vec<CompiledOp>,
}

#[derive(Debug, Clone)]
struct CompiledOp {
    kind: TransformKind,
    args: Vec<CompiledArg>,
}

#[derive(Debug, Clone)]
enum CompiledArg {
    Lit(Value),
    Ref(Box<CompiledRef>),
}

#[derive(Debug, Clone)]
enum TransformKind {
    Eq,
    Ne,
    Lt,
    Le,
    Gt,
    Ge,
    Contains,
    GetItem, // also handles `getattr`
    And,
    RAnd,
    Or,
    ROr,
    Not,
    Add,
    RAdd,
    Sub,
    RSub,
    Mul,
    RMul,
    TrueDiv,
    RTrueDiv,
    FloorDiv,
    RFloorDiv,
    Mod,
    RMod,
    Pow,
    RPow,
    Neg,
    Pos,
    Abs,
    /// Unrecognised transform name — surfaces at runtime as
    /// `OperonError::Runtime` matching the legacy path's error.
    Unknown(String),
}

/// Compile a `RefConfig` (recursively into nested arg refs).
fn compile_ref(rc: &RefConfig, graph_key: &str) -> CompiledRef {
    let source = if rc.source == PARENT {
        graph_key.to_string()
    } else {
        rc.source.clone()
    };
    let chain = rc
        .transforms
        .iter()
        .map(|t| compile_op(t, graph_key))
        .collect();
    CompiledRef {
        source,
        var: rc.var.clone(),
        chain,
    }
}

fn compile_op(t: &RefTransform, graph_key: &str) -> CompiledOp {
    let kind = match t.name.as_str() {
        "eq" => TransformKind::Eq,
        "ne" => TransformKind::Ne,
        "lt" => TransformKind::Lt,
        "le" => TransformKind::Le,
        "gt" => TransformKind::Gt,
        "ge" => TransformKind::Ge,
        "contains" => TransformKind::Contains,
        "getitem" | "getattr" => TransformKind::GetItem,
        "and_" => TransformKind::And,
        "rand_" => TransformKind::RAnd,
        "or_" => TransformKind::Or,
        "ror_" => TransformKind::ROr,
        "not_" => TransformKind::Not,
        "add" => TransformKind::Add,
        "radd" => TransformKind::RAdd,
        "sub" => TransformKind::Sub,
        "rsub" => TransformKind::RSub,
        "mul" => TransformKind::Mul,
        "rmul" => TransformKind::RMul,
        "truediv" => TransformKind::TrueDiv,
        "rtruediv" => TransformKind::RTrueDiv,
        "floordiv" => TransformKind::FloorDiv,
        "rfloordiv" => TransformKind::RFloorDiv,
        "mod" => TransformKind::Mod,
        "rmod" => TransformKind::RMod,
        "pow" => TransformKind::Pow,
        "rpow" => TransformKind::RPow,
        "neg" => TransformKind::Neg,
        "pos" => TransformKind::Pos,
        "abs" => TransformKind::Abs,
        other => TransformKind::Unknown(other.to_string()),
    };
    let args = t.args.iter().map(|a| compile_arg(a, graph_key)).collect();
    CompiledOp { kind, args }
}

fn compile_arg(a: &RefArg, graph_key: &str) -> CompiledArg {
    match a {
        RefArg::Literal(v) => CompiledArg::Lit(v.clone()),
        RefArg::NestedRef(r) => CompiledArg::Ref(Box::new(compile_ref(r, graph_key))),
    }
}

/// Evaluate a compiled ref against runtime state. Walks the transform
/// chain in order, threading the running value.
fn eval_ref(
    cref: &CompiledRef,
    ctx: &ContextId,
    state: &Mutex<RuntimeState>,
) -> Result<Value, OperonError> {
    let base = {
        let s = state.lock();
        s.get(&cref.source, &cref.var, ctx)
            .cloned()
            .ok_or_else(|| {
                OperonError::State(format!(
                    "ref resolution: no value for ({}, {}) at context {:?}",
                    cref.source, cref.var, ctx
                ))
            })?
    };
    let mut current = base;
    for cop in &cref.chain {
        current = eval_op(current, cop, ctx, state)?;
    }
    Ok(current)
}

fn eval_arg(
    a: &CompiledArg,
    ctx: &ContextId,
    state: &Mutex<RuntimeState>,
) -> Result<Value, OperonError> {
    match a {
        CompiledArg::Lit(v) => Ok(v.clone()),
        CompiledArg::Ref(r) => eval_ref(r, ctx, state),
    }
}

fn eval_op(
    value: Value,
    cop: &CompiledOp,
    ctx: &ContextId,
    state: &Mutex<RuntimeState>,
) -> Result<Value, OperonError> {
    use TransformKind::*;
    let arg0 = || -> Result<Value, OperonError> {
        match cop.args.first() {
            Some(a) => eval_arg(a, ctx, state),
            None => Ok(Value::Null),
        }
    };
    match &cop.kind {
        Eq => Ok(Value::Bool(values_equal(&value, &arg0()?))),
        Ne => Ok(Value::Bool(!values_equal(&value, &arg0()?))),
        Lt => cmp_op(&value, &arg0()?, |o| o.is_lt()),
        Le => cmp_op(&value, &arg0()?, |o| o.is_le()),
        Gt => cmp_op(&value, &arg0()?, |o| o.is_gt()),
        Ge => cmp_op(&value, &arg0()?, |o| o.is_ge()),
        Contains => Ok(Value::Bool(value_contains(&value, &arg0()?))),
        GetItem => Ok(value_getitem(&value, &arg0()?)),
        And => {
            if !value_truthy(&value) {
                Ok(value)
            } else {
                Ok(arg0()?)
            }
        }
        RAnd => {
            let lhs = arg0()?;
            if !value_truthy(&lhs) {
                Ok(lhs)
            } else {
                Ok(value)
            }
        }
        Or => {
            if value_truthy(&value) {
                Ok(value)
            } else {
                Ok(arg0()?)
            }
        }
        ROr => {
            let lhs = arg0()?;
            if value_truthy(&lhs) {
                Ok(lhs)
            } else {
                Ok(value)
            }
        }
        Not => Ok(Value::Bool(!value_truthy(&value))),
        Add => arith(&value, &arg0()?, |l, r| l + r),
        RAdd => arith(&arg0()?, &value, |l, r| l + r),
        Sub => arith(&value, &arg0()?, |l, r| l - r),
        RSub => arith(&arg0()?, &value, |l, r| l - r),
        Mul => arith(&value, &arg0()?, |l, r| l * r),
        RMul => arith(&arg0()?, &value, |l, r| l * r),
        TrueDiv => arith(&value, &arg0()?, |l, r| l / r),
        RTrueDiv => arith(&arg0()?, &value, |l, r| l / r),
        FloorDiv => arith(&value, &arg0()?, |l, r| (l / r).floor()),
        RFloorDiv => arith(&arg0()?, &value, |l, r| (l / r).floor()),
        Mod => arith(&value, &arg0()?, |l, r| l.rem_euclid(r)),
        RMod => arith(&arg0()?, &value, |l, r| l.rem_euclid(r)),
        Pow => arith(&value, &arg0()?, |l, r| l.powf(r)),
        RPow => arith(&arg0()?, &value, |l, r| l.powf(r)),
        Neg => unary(&value, |v| -v),
        Pos => unary(&value, |v| v),
        Abs => unary(&value, |v| v.abs()),
        Unknown(name) => Err(OperonError::Runtime(format!(
            "ref transform '{}' not implemented in Rust runtime",
            name
        ))),
    }
}

/// One entry in an op's pre-compiled input plan. Mirrors the four
/// branches of the legacy `resolve_inputs` body (ref / literal /
/// default / required-missing) but pre-resolves which path each input
/// takes, so the runtime walks a tight `Vec` instead of re-reading
/// `Param` flags per frame.
#[derive(Debug, Clone)]
enum InputResolver {
    /// Resolve from runtime state (with optional transforms).
    Ref(CompiledRef),
    /// Inlined literal — `Value` cloned on each invocation.
    Lit(Value),
    /// Default — same as `Lit` but kept distinct for readability.
    Default(Value),
    /// `param.required && param.literal.is_none() && param.default.is_none()`.
    /// Surfaces the same error the legacy path did.
    RequiredMissing,
    /// Optional input with no value — yields `Value::Null`.
    Null,
}

#[derive(Debug, Clone)]
struct InputSlot {
    /// Output key under which the resolved value lands in the inputs map.
    var: String,
    plan: InputResolver,
}

/// One input plan per op (preserves insertion order from
/// `OpConfig.inputs`). Built once at construction so the runtime hot
/// path replaces `for (var, param) in &op_cfg.inputs` + per-input
/// `match param` with a single `for slot in plan` walk.
fn compile_input_plans(graph: &OpConfig, graph_key: &str) -> HashMap<String, Vec<InputSlot>> {
    let mut out: HashMap<String, Vec<InputSlot>> = HashMap::with_capacity(graph.ops.len());
    for (op_name, op_cfg) in &graph.ops {
        let mut slots = Vec::with_capacity(op_cfg.inputs.len());
        for (var, param) in &op_cfg.inputs {
            let plan = if let Some(rc) = &param.ref_config {
                InputResolver::Ref(compile_ref(rc, graph_key))
            } else if let Some(lit) = &param.literal {
                InputResolver::Lit(lit.clone())
            } else if let Some(def) = &param.default {
                InputResolver::Default(def.clone())
            } else if param.required {
                InputResolver::RequiredMissing
            } else {
                InputResolver::Null
            };
            slots.push(InputSlot {
                var: var.clone(),
                plan,
            });
        }
        out.insert(op_name.clone(), slots);
    }
    out
}

/// `op_name -> (Vec<(condition, target)>, default)`. Empty when there
/// are no branch ops in the graph.
fn compile_branches(
    graph: &OpConfig,
    graph_key: &str,
) -> HashMap<String, (Vec<(CompiledRef, String)>, Option<String>)> {
    let mut out = HashMap::new();
    for (op_name, op_cfg) in &graph.ops {
        if !matches!(op_cfg.kind, OpType::Branch) {
            continue;
        }
        let cases = op_cfg
            .cases
            .iter()
            .map(|c| (compile_ref(&c.condition, graph_key), c.target.clone()))
            .collect();
        out.insert(op_name.clone(), (cases, op_cfg.default.clone()));
    }
    out
}

// ── Helper functions ──────────────────────────────────────────────────────

fn compute_out_vars(graph: &OpConfig) -> HashMap<String, Vec<(String, String)>> {
    let mut map: HashMap<String, Vec<(String, String)>> = HashMap::new();
    let graph_key: &str = if graph.full_name.is_empty() {
        &graph.name
    } else {
        &graph.full_name
    };
    for (op_name, op_cfg) in &graph.ops {
        for (src_var, param) in &op_cfg.outputs {
            let Some(ref_cfg) = &param.ref_config else {
                continue;
            };
            // Output is forwarded to the parent graph when the ref's source
            // is either the sentinel `__PARENT__` (Rust-emitted JSON) or the
            // graph's own name / full_name (Python-emitted JSON — Python
            // resolves `PARENT` to the enclosing graph's identifier at
            // serialization time). Either carries the same semantics.
            let targets_parent = ref_cfg.source == PARENT
                || ref_cfg.source == graph_key
                || ref_cfg.source == graph.name;
            if targets_parent {
                map.entry(op_name.clone())
                    .or_default()
                    .push((src_var.clone(), ref_cfg.var.clone()));
            }
        }
    }
    map
}

/// Walk `dst.inputs` looking for a ref whose `source` matches `src` (by
/// short name or `full_name`). Returns the first carrying a
/// `StreamPolicy`. Pulled out of `GraphScheduler` so the constructor
/// can pre-cache the lookup at build time — `route_edge_async` then
/// hits `edge_policies` instead of re-walking inputs per frame.
fn resolve_edge_policy(graph: &OpConfig, src: &str, dst: &str) -> Option<StreamPolicy> {
    let dst_op = graph.ops.get(dst)?;
    let src_full = graph
        .ops
        .get(src)
        .map(|o| o.full_name.as_str())
        .unwrap_or(src);
    for (_var, param) in &dst_op.inputs {
        let Some(ref_cfg) = &param.ref_config else {
            continue;
        };
        if ref_cfg.source == src || ref_cfg.source == src_full {
            if let Some(p) = ref_cfg.stream_policy {
                return Some(p);
            }
        }
    }
    None
}

fn resolve_inputs(
    op_cfg: &OpConfig,
    plan: &[InputSlot],
    ctx: &ContextId,
    state: &Mutex<RuntimeState>,
) -> Result<Map<String, Value>, OperonError> {
    let mut resolved = Map::with_capacity(plan.len());
    for slot in plan {
        let value = match &slot.plan {
            InputResolver::Ref(cref) => eval_ref(cref, ctx, state)?,
            InputResolver::Lit(v) | InputResolver::Default(v) => v.clone(),
            InputResolver::RequiredMissing => {
                return Err(OperonError::Op(OpError::Code(format!(
                    "op '{}': required input '{}' not provided",
                    op_cfg.full_name, slot.var
                ))));
            }
            InputResolver::Null => Value::Null,
        };
        resolved.insert(slot.var.clone(), value);
    }
    Ok(resolved)
}

/// Evaluate a `BranchOp`'s cases. Walks `op_cfg.cases` in order, resolving
/// each `condition` Ref (with its transforms) to a `Value`, and returns
/// the `target` of the first case whose condition is truthy. If no case
/// matches and `op_cfg.default` is set, returns that. Otherwise errors
/// — Python's `BranchOp.run` raises `BranchError` in the same scenario.
fn evaluate_branch(
    op_cfg: &OpConfig,
    branches: &HashMap<String, (Vec<(CompiledRef, String)>, Option<String>)>,
    ctx: &ContextId,
    state: &Mutex<RuntimeState>,
) -> Result<String, OperonError> {
    let entry = branches.get(&op_cfg.name).ok_or_else(|| {
        OperonError::Runtime(format!(
            "branch '{}' missing pre-compiled case table",
            op_cfg.full_name
        ))
    })?;
    for (cond, target) in &entry.0 {
        let v = eval_ref(cond, ctx, state)?;
        if value_truthy(&v) {
            return Ok(target.clone());
        }
    }
    if let Some(d) = &entry.1 {
        return Ok(d.clone());
    }
    Err(OperonError::Runtime(format!(
        "branch '{}' has no matching case and no default",
        op_cfg.full_name
    )))
}

/// JSON-value equality with Python-ish numeric coercion (3 == 3.0).
fn values_equal(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(an), Value::Number(bn)) => match (an.as_f64(), bn.as_f64()) {
            (Some(af), Some(bf)) => af == bf,
            _ => an == bn,
        },
        _ => a == b,
    }
}

fn cmp_op(
    a: &Value,
    b: &Value,
    pred: impl Fn(std::cmp::Ordering) -> bool,
) -> Result<Value, OperonError> {
    use std::cmp::Ordering;
    let ord = match (a, b) {
        (Value::Number(an), Value::Number(bn)) => match (an.as_f64(), bn.as_f64()) {
            (Some(af), Some(bf)) => af.partial_cmp(&bf).unwrap_or(Ordering::Equal),
            _ => Ordering::Equal,
        },
        (Value::String(a), Value::String(b)) => a.cmp(b),
        (Value::Bool(a), Value::Bool(b)) => a.cmp(b),
        _ => {
            return Err(OperonError::Runtime(format!(
                "ref comparison: cannot compare {:?} and {:?}",
                a, b
            )));
        }
    };
    Ok(Value::Bool(pred(ord)))
}

fn value_contains(haystack: &Value, needle: &Value) -> bool {
    match haystack {
        Value::Array(items) => items.iter().any(|x| values_equal(x, needle)),
        Value::Object(map) => match needle {
            Value::String(s) => map.contains_key(s),
            _ => false,
        },
        Value::String(s) => match needle {
            Value::String(sub) => s.contains(sub.as_str()),
            _ => false,
        },
        _ => false,
    }
}

/// `value[key]` — for objects (string key) and arrays (numeric index).
/// Returns `Value::Null` on miss to match Python's KeyError-vs-None
/// nuance loosely; pure-bool transforms downstream see Null as falsy.
fn value_getitem(value: &Value, key: &Value) -> Value {
    match (value, key) {
        (Value::Object(map), Value::String(k)) => map.get(k).cloned().unwrap_or(Value::Null),
        (Value::Array(items), Value::Number(n)) => {
            if let Some(i) = n.as_i64() {
                let idx = if i < 0 {
                    (items.len() as i64 + i) as usize
                } else {
                    i as usize
                };
                items.get(idx).cloned().unwrap_or(Value::Null)
            } else {
                Value::Null
            }
        }
        _ => Value::Null,
    }
}

/// Python truthiness: `False`, `None`/null, 0, "", [], {} are falsy.
fn value_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(m) => !m.is_empty(),
    }
}

/// Coerce two `Value`s to f64 and apply `op`. Returns the result as a
/// `Value::Number`. Errors when either side isn't a number.
fn arith(a: &Value, b: &Value, op: impl Fn(f64, f64) -> f64) -> Result<Value, OperonError> {
    let (af, bf) = match (a.as_f64(), b.as_f64()) {
        (Some(x), Some(y)) => (x, y),
        _ => {
            return Err(OperonError::Runtime(format!(
                "ref arithmetic: non-numeric operands {:?} and {:?}",
                a, b
            )))
        }
    };
    let result = op(af, bf);
    serde_json::Number::from_f64(result)
        .map(Value::Number)
        .ok_or_else(|| {
            OperonError::Runtime(format!("ref arithmetic produced non-finite: {}", result))
        })
}

fn unary(a: &Value, op: impl Fn(f64) -> f64) -> Result<Value, OperonError> {
    let af = a.as_f64().ok_or_else(|| {
        OperonError::Runtime(format!("ref unary op: non-numeric operand {:?}", a))
    })?;
    let result = op(af);
    serde_json::Number::from_f64(result)
        .map(Value::Number)
        .ok_or_else(|| {
            OperonError::Runtime(format!("ref unary op produced non-finite: {}", result))
        })
}

async fn execute_op(
    op_cfg: &OpConfig,
    registry: &Arc<dyn OpRegistry>,
    inputs: Map<String, Value>,
    child_schedulers: &Arc<HashMap<String, Arc<GraphScheduler>>>,
) -> Result<Value, OperonError> {
    use crate::providers::ops::{execute_provider_op, is_provider_kind};

    if is_provider_kind(op_cfg.kind) {
        return execute_provider_op(op_cfg, inputs).await;
    }

    match op_cfg.kind {
        OpType::Code | OpType::Lambda => {
            let func_name = op_cfg.func_name.as_deref().ok_or_else(|| {
                OperonError::Config(format!("code op '{}' missing func_name", op_cfg.full_name))
            })?;
            // Python emits fully-qualified names (e.g. `tests.spec._ops.double`)
            // while `#[op]` / `OperonBuilder::op` typically register bare names
            // (`double`). Try the exact name first, then fall back to the last
            // dotted component — mirrors the Python bare-name resolution.
            let func = registry
                .lookup(func_name)
                .or_else(|| {
                    func_name
                        .rsplit_once('.')
                        .and_then(|(_, short)| registry.lookup(short))
                })
                .ok_or_else(|| {
                    OperonError::Runtime(format!(
                        "no registered function named '{}' (register via OperonBuilder::op or the #[op] macro)",
                        func_name
                    ))
                })?;
            func(inputs).await
        }
        OpType::Graph => {
            // Nested @graph dispatch — single map lookup into the parent
            // scheduler's pre-built child schedulers, then `run_collect`
            // inline (no `tokio::spawn`, no `mpsc::channel(64)`, no UUID
            // gen, no middleware). Mirrors Python's
            // `child._scheduler.run(state, ctx)` which is the same
            // shape: a recursive call into a pre-built scheduler with
            // shared state semantics.
            let child = child_schedulers.get(&op_cfg.full_name).ok_or_else(|| {
                OperonError::Runtime(format!(
                    "nested graph '{}' not in parent scheduler's child map; \
                     this should have been built at parent construction time",
                    op_cfg.full_name
                ))
            })?;
            child.run_collect(inputs).await
        }
        other => Err(OperonError::Runtime(format!(
            "op type {:?} not yet implemented for {}",
            other, op_cfg.full_name
        ))),
    }
}

/// Build an error frame for the scheduler stream — single `error` key.
fn error_frame(e: &OperonError) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("error".into(), Value::from(e.to_string()));
    m
}

/// Coerce an op's return value into a frame map. Object values pass through;
/// scalar / array results are wrapped in a `{"result": <value>}` envelope —
/// matches Python's `FuncOp` behaviour.
fn value_to_map(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(m) => m,
        other => {
            let mut m = Map::new();
            m.insert("result".into(), other);
            m
        }
    }
}

/// Fan an op's return value out into the per-yield frames the scheduler
/// will post. Generators emit one frame per element of a `Value::Array`
/// (each frame at a `(parent_ctx, "yield_N")` sub-context); regular ops
/// emit exactly one frame on `parent_ctx`. State is mutated in place so
/// downstream `resolve_ref` calls can read each yield at its own
/// sub-context.
fn fan_out_value(
    op_cfg: &OpConfig,
    parent_ctx: &ContextId,
    value: Value,
    state: &Arc<Mutex<RuntimeState>>,
) -> Vec<(ContextId, Map<String, Value>)> {
    if op_cfg.is_generator {
        let items = match value {
            Value::Array(a) => a,
            // Be lenient — a generator that returned a single object
            // collapses to a one-yield list, matching Python's
            // single-yield iteration.
            other => vec![other],
        };
        let mut out = Vec::with_capacity(items.len());
        for (i, item) in items.into_iter().enumerate() {
            let mut yield_ctx = parent_ctx.clone();
            yield_ctx.push(format!("yield_{i}"));
            let item_map = value_to_map(item);
            {
                let mut s = state.lock();
                for (k, v) in &item_map {
                    s.set(&op_cfg.full_name, k, &yield_ctx, v.clone());
                }
            }
            out.push((yield_ctx, item_map));
        }
        out
    } else {
        let map = value_to_map(value);
        {
            let mut s = state.lock();
            for (k, v) in &map {
                s.set(&op_cfg.full_name, k, parent_ctx, v.clone());
            }
        }
        vec![(parent_ctx.clone(), map)]
    }
}

/// Compute the next loop-iteration context. Matches Python:
/// - iter 1 (n_iters=1): `parent_ctx + ("loop_1",)`
/// - iter N (>1):        `parent_ctx[:-1] + ("loop_N",)`
fn next_loop_ctx(current: &ContextId, n_iters: u32) -> ContextId {
    let label = format!("loop_{}", n_iters);
    if n_iters == 1 {
        let mut next = current.clone();
        next.push(label);
        next
    } else {
        let mut next = current.clone();
        if !next.is_empty() {
            next.pop();
        }
        next.push(label);
        next
    }
}

/// Minimal `until` expression evaluator — supports the small grammar Python
/// produces from loop `until=` strings:
///
/// ```text
/// var <op> number     e.g. "count >= 5"
/// var == var          e.g. "flag == other"
/// ```
///
/// Where `<op>` ∈ `==`, `!=`, `>=`, `<=`, `>`, `<`. Booleans / numeric coercion
/// handled. Richer expressions (parens, boolean ops) land with the proper ref
/// evaluator in Phase 5.
fn eval_until(expr: &str, outputs: &Map<String, Value>) -> Result<bool, OperonError> {
    let expr = expr.trim();
    for op in ["==", "!=", ">=", "<=", ">", "<"] {
        if let Some(idx) = expr.find(op) {
            let lhs = expr[..idx].trim();
            let rhs = expr[idx + op.len()..].trim();
            let lhs_val = lookup_operand(lhs, outputs);
            let rhs_val = lookup_operand(rhs, outputs);
            return compare(op, &lhs_val, &rhs_val);
        }
    }
    // Bare bool variable: `"stop"` stops when outputs["stop"] is truthy.
    let val = lookup_operand(expr, outputs);
    Ok(is_truthy(&val))
}

fn lookup_operand(token: &str, outputs: &Map<String, Value>) -> Value {
    let token = token.trim();
    // Numeric literal
    if let Ok(n) = token.parse::<i64>() {
        return Value::from(n);
    }
    if let Ok(n) = token.parse::<f64>() {
        return serde_json::Number::from_f64(n)
            .map(Value::Number)
            .unwrap_or(Value::Null);
    }
    // Boolean literal
    match token {
        "true" | "True" => return Value::Bool(true),
        "false" | "False" => return Value::Bool(false),
        "None" | "null" => return Value::Null,
        _ => {}
    }
    // String literal (quoted)
    if (token.starts_with('"') && token.ends_with('"'))
        || (token.starts_with('\'') && token.ends_with('\''))
    {
        let inner = &token[1..token.len() - 1];
        return Value::from(inner);
    }
    // Variable reference into outputs.
    outputs.get(token).cloned().unwrap_or(Value::Null)
}

fn compare(op: &str, lhs: &Value, rhs: &Value) -> Result<bool, OperonError> {
    // Numeric comparison if both are numbers.
    if let (Some(a), Some(b)) = (lhs.as_f64(), rhs.as_f64()) {
        return Ok(match op {
            "==" => a == b,
            "!=" => a != b,
            ">=" => a >= b,
            "<=" => a <= b,
            ">" => a > b,
            "<" => a < b,
            _ => unreachable!(),
        });
    }
    // Fallback string comparison for `==` / `!=` only.
    let eq = lhs == rhs;
    match op {
        "==" => Ok(eq),
        "!=" => Ok(!eq),
        other => Err(OperonError::Runtime(format!(
            "loop until: cannot compare non-numeric values with {}",
            other
        ))),
    }
}

fn is_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|x| x != 0.0).unwrap_or(false),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

// ── Tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn eval_until_numeric() {
        let mut out = Map::new();
        out.insert("count".into(), Value::from(5));
        assert!(eval_until("count >= 5", &out).unwrap());
        assert!(!eval_until("count >= 6", &out).unwrap());
        assert!(eval_until("count == 5", &out).unwrap());
        assert!(eval_until("count < 10", &out).unwrap());
    }

    #[test]
    fn eval_until_bool_var() {
        let mut out = Map::new();
        out.insert("done".into(), Value::from(true));
        assert!(eval_until("done", &out).unwrap());
        out.insert("done".into(), Value::from(false));
        assert!(!eval_until("done", &out).unwrap());
    }

    #[test]
    fn next_loop_ctx_progression() {
        let root = default_context();
        let it1 = next_loop_ctx(&root, 1);
        assert_eq!(it1.last().map(|s| s.as_str()), Some("loop_1"));
        let it2 = next_loop_ctx(&it1, 2);
        assert_eq!(it2.last().map(|s| s.as_str()), Some("loop_2"));
        // Second iteration replaces the previous loop label rather than
        // nesting.
        assert_eq!(it2.len(), it1.len());
    }

    #[test]
    fn runtime_state_parent_walk_on_read() {
        let mut s = RuntimeState::new();
        let root = default_context();
        s.set("op", "v", &root, Value::from(1));

        let mut deep = root.clone();
        deep.push("[0]".into());
        // Read at deeper context falls back to root.
        assert_eq!(s.get("op", "v", &deep), Some(&Value::from(1)));
    }
}
