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
use tracing::debug;

use crate::core::configs::op_config::{CompiledLink, LoopConfig, OpConfig, OpType};
use crate::core::engine::{FrameEvent, FrameSender, Scheduler};
use crate::core::exceptions::{OpError, OperonError};
use crate::core::middleware::MiddlewareContext;
use crate::core::ops::edges::PARENT;
use crate::core::registry::OpRegistry;
use crate::core::states::cell::{default_context, ContextId};
use crate::core::states::ref_::RefConfig;

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
    fn new() -> Self {
        Self::default()
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
    /// graph and pre-computes PARENT-bound output var mappings.
    pub fn new(graph: Arc<OpConfig>, registry: Arc<dyn OpRegistry>) -> Result<Self, OperonError> {
        if !graph.is_graph() {
            return Err(OperonError::Config(format!(
                "top-level op must be a graph, got {:?}",
                graph.kind
            )));
        }
        let out_vars = compute_out_vars(&graph);
        Ok(Self {
            graph,
            registry,
            out_vars,
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
        let state = Arc::new(Mutex::new(RuntimeState::new()));

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
        ready.insert(
            ctx.clone(),
            self.graph.initial_ready_count.clone().into_iter().collect(),
        );

        let mut inflight: i32 = 0;

        // Sequential per-edge queueing — matches Python seq_queues/seq_active.
        let mut seq_queues: HashMap<(String, String), VecDeque<ContextId>> = HashMap::new();
        let mut seq_active: HashMap<(String, String), bool> = HashMap::new();
        let mut seq_origins: HashMap<(String, ContextId), (String, String)> = HashMap::new();

        // Collect buffers — `(src, dst) → [(frame_ctx, result), ...]`. Python
        // parity: flushed when src emits Eof; merged per-key into lists, one
        // dispatch of dst under a `__collect__` sub-context.
        let mut collect_bufs: HashMap<(String, String), Vec<(ContextId, Map<String, Value>)>> =
            HashMap::new();

        let sem = Arc::new(Semaphore::new(
            self.graph.max_stream_concurrent.max(1) as usize
        ));

        // Internal event queue — op execution tasks post here, the main loop
        // drains it. Bounded per plan §1.
        let (tx, mut rx) = mpsc::channel::<SchedulerEvent>(256);

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
            )?;
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
                            )?;
                        }
                    }
                }
            }
        }

        Ok(())
    }

    /// Dispatch one op — resolve its inputs, call the function, push Frame/Eof
    /// events back onto the scheduler's internal queue.
    fn spawn_op(
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
        let graph_key = self.graph_key().to_string();

        tokio::spawn(async move {
            let _permit = match sem.acquire_owned().await {
                Ok(p) => p,
                Err(_) => return, // semaphore closed — nothing more to do
            };
            if cancel.is_cancelled() {
                return;
            }

            // Resolve inputs from state via the op's ref/literal/default
            // declarations.
            let inputs = match resolve_inputs(&op_cfg, &graph_key, &ctx, &state) {
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

            let exec_result = execute_op(&op_cfg, &registry, inputs).await;

            match exec_result {
                Ok(value) => {
                    let result_map = match value {
                        Value::Object(m) => m,
                        other => {
                            let mut m = Map::new();
                            m.insert("result".into(), other);
                            m
                        }
                    };
                    // Persist the op's outputs into state before emitting the
                    // Frame so downstream ops see them.
                    {
                        let mut s = state.lock();
                        for (k, v) in &result_map {
                            s.set(&op_cfg.full_name, k, &ctx, v.clone());
                        }
                    }
                    let _ = tx
                        .send(SchedulerEvent::Frame {
                            op: op_name.clone(),
                            ctx: ctx.clone(),
                            result: result_map,
                        })
                        .await;
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
            let base = self.graph.initial_ready_count.clone().into_iter().collect();
            ready.insert(ctx.clone(), base);
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
            self.route_edge(
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
            )?;
        }
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    fn route_edge(
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

        // Consult the dst op's per-var stream policy — the input var whose
        // ref.source == src carries the `StreamPolicy`. Python's equivalent
        // lookup lives in task_scheduler.py _route.
        let policy = self.edge_policy(src, &link.dst);

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
                )?;
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
            )?;
        } else {
            seq_queues.entry(key).or_default().push_back(ctx.clone());
        }
        Ok(())
    }

    /// Resolve the [`StreamPolicy`](crate::core::states::ref_::StreamPolicy)
    /// declared on the dst op's input var that references `src`. Returns
    /// `None` when dst's inputs don't reference src or when none of the
    /// matching refs carry a policy.
    fn edge_policy(&self, src: &str, dst: &str) -> Option<crate::core::states::ref_::StreamPolicy> {
        let dst_op = self.graph.ops.get(dst)?;
        let src_full = self
            .graph
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

    #[allow(clippy::too_many_arguments)]
    fn on_eof(
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
            )?;
        }

        // Advance the sequential queue if this EOF unblocks a following item.
        if let Some(key) = seq_origins.remove(&(op.to_string(), ctx.clone())) {
            if let Some(q) = seq_queues.get_mut(&key) {
                if let Some(next_ctx) = q.pop_front() {
                    seq_origins.insert((key.1.clone(), next_ctx.clone()), key.clone());
                    *inflight += 1;
                    self.spawn_op(key.1.clone(), next_ctx, state, tx, sem, cancel.clone())?;
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

fn resolve_inputs(
    op_cfg: &OpConfig,
    graph_key: &str,
    ctx: &ContextId,
    state: &Mutex<RuntimeState>,
) -> Result<Map<String, Value>, OperonError> {
    let mut resolved = Map::new();
    for (var, param) in &op_cfg.inputs {
        let value = if let Some(ref_cfg) = &param.ref_config {
            resolve_ref(ref_cfg, graph_key, ctx, state)?
        } else if let Some(lit) = &param.literal {
            lit.clone()
        } else if let Some(default) = &param.default {
            default.clone()
        } else if param.required {
            return Err(OperonError::Op(OpError::Code(format!(
                "op '{}': required input '{}' not provided",
                op_cfg.full_name, var
            ))));
        } else {
            Value::Null
        };
        resolved.insert(var.clone(), value);
    }
    Ok(resolved)
}

fn resolve_ref(
    ref_cfg: &RefConfig,
    graph_key: &str,
    ctx: &ContextId,
    state: &Mutex<RuntimeState>,
) -> Result<Value, OperonError> {
    let source = if ref_cfg.source == PARENT {
        graph_key
    } else {
        &ref_cfg.source
    };
    let s = state.lock();
    let base = s.get(source, &ref_cfg.var, ctx).cloned().ok_or_else(|| {
        OperonError::State(format!(
            "ref resolution: no value for ({}, {}) at context {:?}",
            source, ref_cfg.var, ctx
        ))
    })?;
    // Phase 4: transforms are not applied. Refs with transforms remain for
    // Phase 5/6 when the ref evaluator is wired up.
    if !ref_cfg.transforms.is_empty() {
        debug!(
            "ref transforms not yet applied (target: {}.{}, {} transforms)",
            source,
            ref_cfg.var,
            ref_cfg.transforms.len()
        );
    }
    Ok(base)
}

async fn execute_op(
    op_cfg: &OpConfig,
    registry: &Arc<dyn OpRegistry>,
    inputs: Map<String, Value>,
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
        OpType::Graph => Err(OperonError::Runtime(format!(
            "nested graph ops not yet supported — deferred past Phase 4 ({})",
            op_cfg.full_name
        ))),
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
