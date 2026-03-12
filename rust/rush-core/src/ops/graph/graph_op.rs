//! Graph op execution — async event-queue scheduler, output collection, nested graphs.
//!
//! Port of Python's `ops/graph/scheduler.py` to Rust.
//! Uses tokio channels as the event queue, matching Python's asyncio.Queue.
//!
//! Execution flow:
//!   run_graph() [sync entry]
//!     → block_on(run_scheduler())
//!       → drain_ready(entries)
//!       → event loop: Done / DonePending / Yield / Exhausted
//!       → collect outputs

use std::collections::VecDeque;
use std::sync::Arc;

use ahash::{AHashMap, AHashSet};
use serde_json::Value;
use tokio::sync::{mpsc, Semaphore};

use crate::config::{GraphConfig, LoopConfig, OpBound, OpConfig};
use crate::error::RushError;
use crate::ops::base;
use crate::ops::graph::loop_eval;
use crate::registry::OpRegistry;
use crate::runtime;
use crate::states::state::EngineState;

// =============================================================================
// Scheduler events (mirrors Python's event_queue tuples)
// =============================================================================

#[derive(Debug)]
enum SchedulerEvent {
    /// Op completed normally — propagate to successors.
    Done(String, String),
    /// Op returned PENDING — no propagation.
    DonePending(String, String),
    /// Generator yielded an item — create stream context + propagate.
    Yield(String, String, Value),
    /// Generator exhausted — decrement active_count.
    Exhausted(String),
}

/// Result of dispatching a single op.
enum DispatchResult {
    /// Op ran inline — here are the newly ready successors.
    Completed(Vec<String>),
    /// Op returned PENDING inline — no successors.
    Pending,
    /// Op was spawned as an async task — events will arrive via channel.
    Spawned,
}

// =============================================================================
// Public sync entry point
// =============================================================================

/// Run a graph's scheduling loop (sync wrapper).
///
/// Used for both top-level graphs (from engine.rs) and nested graphs.
/// Wraps the async scheduler in block_on for the sync API.
pub(crate) fn run_graph(
    config: &GraphConfig,
    state: &EngineState,
    context: &str,
    registry: &Option<Arc<dyn OpRegistry>>,
) -> Result<Vec<String>, RushError> {
    runtime::block_on_async(run_scheduler(config, state, context, registry))
}

// =============================================================================
// Async event-queue scheduler (mirrors Python scheduler.py)
// =============================================================================

/// Core async scheduler — 1:1 port of Python's run_scheduler().
///
/// All runtime state is local — concurrent calls on the same graph are safe.
async fn run_scheduler(
    config: &GraphConfig,
    state: &EngineState,
    context_id: &str,
    registry: &Option<Arc<dyn OpRegistry>>,
) -> Result<Vec<String>, RushError> {
    let (event_tx, mut event_rx) = mpsc::unbounded_channel::<SchedulerEvent>();
    let mut active_count: usize = 0;

    // Per-context ready_counts: context → {op_name → remaining_predecessors}
    let mut ready_counts: AHashMap<String, AHashMap<String, i32>> = AHashMap::new();
    ready_counts.insert(context_id.to_string(), config.initial_ready_count.clone());

    // Per-context soft_satisfied: context → set of targets already satisfied
    let mut soft_satisfied: AHashMap<String, AHashSet<String>> = AHashMap::new();

    // Track stream contexts for output collection
    let mut stream_contexts: Vec<String> = Vec::new();

    let semaphore = Arc::new(Semaphore::new(config.max_stream_concurrent));

    // ── Helper: propagate completion ───────────────────────────────
    // Returns list of op names whose ready_count just hit 0.
    let propagate = |op_name: &str,
                     ctx: &str,
                     ready_counts: &mut AHashMap<String, AHashMap<String, i32>>,
                     soft_satisfied: &mut AHashMap<String, AHashSet<String>>|
     -> Result<Vec<String>, RushError> {
        let op = config
            .ops
            .get(op_name)
            .ok_or_else(|| RushError::ConfigError(format!("Op '{}' not found", op_name)))?;

        // Get successors, filtered for branch ops
        let all_successors = config
            .compiled_adj
            .get(op_name)
            .cloned()
            .unwrap_or_default();

        let successors = if op.op_type == "branch" {
            let target = state
                .get(&op.full_name, "target", ctx)
                .and_then(|v| v.as_str().map(|s| s.to_string()));

            match target {
                Some(ref t) if t == "__END__" => Default::default(),
                Some(ref t) => all_successors
                    .iter()
                    .filter(|e| e.target == *t)
                    .cloned()
                    .collect(),
                None => Default::default(),
            }
        } else {
            all_successors.to_vec()
        };

        let rc = ready_counts
            .get_mut(ctx)
            .ok_or_else(|| RushError::Runtime(format!("No ready_counts for context '{}'", ctx)))?;
        let ss = soft_satisfied.entry(ctx.to_string()).or_default();
        let mut newly_ready = Vec::new();

        for entry in &successors {
            if entry.is_soft {
                if ss.contains(&entry.target) {
                    continue;
                }
                ss.insert(entry.target.clone());
            }
            if let Some(count) = rc.get_mut(&entry.target) {
                *count -= 1;
                if *count == 0 {
                    newly_ready.push(entry.target.clone());
                }
            }
        }

        Ok(newly_ready)
    };

    // ── Helper: dispatch a single op ──────────────────────────────
    let dispatch_op = |op_name: &str,
                       ctx: &str,
                       active_count: &mut usize,
                       ready_counts: &mut AHashMap<String, AHashMap<String, i32>>,
                       soft_satisfied: &mut AHashMap<String, AHashSet<String>>,
                       event_tx: &mpsc::UnboundedSender<SchedulerEvent>,
                       semaphore: &Arc<Semaphore>|
     -> Result<DispatchResult, RushError> {
        let op = config
            .ops
            .get(op_name)
            .ok_or_else(|| RushError::ConfigError(format!("Op '{}' not found", op_name)))?;

        // Generator ops
        if op.is_generator {
            // Built-in generators run inline (no tokio::spawn overhead).
            // call_generator() returns Vec<Value> synchronously — just send
            // Yield + Exhausted events to the channel for the main loop.
            if op.rust_op.is_some() {
                *active_count += 1;
                run_builtin_generator_inline(op, state, ctx, event_tx, registry);
                return Ok(DispatchResult::Spawned);
            }
            // Provider streaming / other generators → spawn async task
            *active_count += 1;
            spawn_generator_task(op, state, ctx, context_id, event_tx.clone(), Arc::clone(semaphore), registry);
            return Ok(DispatchResult::Spawned);
        }

        // Graph ops → spawn async task (recursive scheduler)
        if op.op_type == "graph" {
            *active_count += 1;
            spawn_graph_task(op, config, state, ctx, context_id, event_tx.clone(), Arc::clone(semaphore), registry);
            return Ok(DispatchResult::Spawned);
        }

        // Branch ops → always inline (just condition evaluation)
        if op.op_type == "branch" {
            base::execute_branch(op, state, ctx)?;
            let newly_ready = propagate(op_name, ctx, ready_counts, soft_satisfied)?;
            return Ok(DispatchResult::Completed(newly_ready));
        }

        // IO-bound ops → spawn_blocking for parallel fan-out
        if op.bound == OpBound::Io || op.provider_config.is_some() {
            *active_count += 1;
            spawn_blocking_task(op, state, ctx, context_id, event_tx.clone(), Arc::clone(semaphore), registry);
            return Ok(DispatchResult::Spawned);
        }

        // CPU-bound / simple ops → inline execution
        let result = base::run(op, state, ctx, registry)?;

        // PENDING sentinel — absorb input without triggering downstream
        if result == base::OpResult::Pending {
            return Ok(DispatchResult::Pending);
        }

        let newly_ready = propagate(op_name, ctx, ready_counts, soft_satisfied)?;
        Ok(DispatchResult::Completed(newly_ready))
    };

    // ── Helper: drain ready queue ─────────────────────────────────
    // Process queue of ready ops: dispatch each, append newly ready successors.
    let drain_ready =
        |queue: &mut VecDeque<String>,
         ctx: &str,
         active_count: &mut usize,
         ready_counts: &mut AHashMap<String, AHashMap<String, i32>>,
         soft_satisfied: &mut AHashMap<String, AHashSet<String>>,
         event_tx: &mpsc::UnboundedSender<SchedulerEvent>,
         semaphore: &Arc<Semaphore>|
         -> Result<(), RushError> {
            while let Some(op_name) = queue.pop_front() {
                match dispatch_op(
                    &op_name,
                    ctx,
                    active_count,
                    ready_counts,
                    soft_satisfied,
                    event_tx,
                    semaphore,
                )? {
                    DispatchResult::Completed(newly_ready) => {
                        queue.extend(newly_ready);
                    }
                    DispatchResult::Pending | DispatchResult::Spawned => {}
                }
            }
            Ok(())
        };

    // ── 1. Start entry ops ────────────────────────────────────────
    let mut queue: VecDeque<String> = config.entries.iter().cloned().collect();
    drain_ready(
        &mut queue,
        context_id,
        &mut active_count,
        &mut ready_counts,
        &mut soft_satisfied,
        &event_tx,
        &semaphore,
    )?;

    // ── 2. Event loop ─────────────────────────────────────────────
    while active_count > 0 {
        let event = event_rx
            .recv()
            .await
            .ok_or_else(|| RushError::Runtime("Scheduler event channel closed".into()))?;

        match event {
            SchedulerEvent::Done(op_name, ctx) => {
                active_count -= 1;
                let mut newly_ready: VecDeque<String> = propagate(&op_name, &ctx, &mut ready_counts, &mut soft_satisfied)?.into();
                drain_ready(
                    &mut newly_ready,
                    &ctx,
                    &mut active_count,
                    &mut ready_counts,
                    &mut soft_satisfied,
                    &event_tx,
                    &semaphore,
                )?;
            }

            SchedulerEvent::DonePending(_op_name, _ctx) => {
                active_count -= 1;
            }

            SchedulerEvent::Yield(gen_name, stream_ctx, _result_data) => {
                // Create stream context with pre-computed ready counts (predecrements already applied).
                let rc = config.stream_ready_counts
                    .get(&gen_name)
                    .cloned()
                    .unwrap_or_else(|| config.initial_ready_count.clone());
                ready_counts.insert(stream_ctx.clone(), rc);
                stream_contexts.push(stream_ctx.clone());

                let mut newly_ready: VecDeque<String> =
                    propagate(&gen_name, &stream_ctx, &mut ready_counts, &mut soft_satisfied)?.into();
                drain_ready(
                    &mut newly_ready,
                    &stream_ctx,
                    &mut active_count,
                    &mut ready_counts,
                    &mut soft_satisfied,
                    &event_tx,
                    &semaphore,
                )?;
            }

            SchedulerEvent::Exhausted(_gen_name) => {
                active_count -= 1;
            }
        }
    }

    // ── 3. Return stream contexts for output aggregation ──────────
    Ok(stream_contexts)
}

// =============================================================================
// Task spawners
// =============================================================================

/// Spawn a nested graph op as a tokio task.
///
/// SAFETY: config and state are alive for the duration of run_scheduler,
/// which awaits all tasks (via active_count + event loop) before returning.
fn spawn_graph_task(
    op: &OpConfig,
    _parent_config: &GraphConfig,
    state: &EngineState,
    context: &str,
    context_id: &str,
    event_tx: mpsc::UnboundedSender<SchedulerEvent>,
    semaphore: Arc<Semaphore>,
    registry: &Option<Arc<dyn OpRegistry>>,
) {
    let op_addr = op as *const OpConfig as usize;
    let state_addr = state as *const EngineState as usize;
    let ctx = context.to_string();
    let ctx_id = context_id.to_string();
    let op_name = op.name.clone();
    let registry = registry.clone();

    tokio::spawn(async move {
        let is_stream = ctx != ctx_id;
        let _permit = if is_stream {
            Some(semaphore.acquire().await.expect("semaphore closed"))
        } else {
            None
        };

        // SAFETY: op and state are alive — caller awaits all tasks before returning
        let op = unsafe { &*(op_addr as *const OpConfig) };
        let state = unsafe { &*(state_addr as *const EngineState) };

        // Must call async version directly — block_on from async context deadlocks
        match run_nested_graph_async(op, state, &ctx, &registry).await {
            Ok(()) => {
                let _ = event_tx.send(SchedulerEvent::Done(op_name, ctx));
            }
            Err(e) => {
                state.set(&op.full_name, "error", &ctx, Value::String(format!("{}", e)));
                log::error!("[rush] Error in graph op {}: {}", op.full_name, e);
                let _ = event_tx.send(SchedulerEvent::Done(op_name, ctx));
            }
        }
    });
}

/// Spawn an IO-bound op as a blocking task.
///
/// SAFETY: same as spawn_graph_task — caller awaits all tasks.
fn spawn_blocking_task(
    op: &OpConfig,
    state: &EngineState,
    context: &str,
    context_id: &str,
    event_tx: mpsc::UnboundedSender<SchedulerEvent>,
    semaphore: Arc<Semaphore>,
    registry: &Option<Arc<dyn OpRegistry>>,
) {
    let op_addr = op as *const OpConfig as usize;
    let state_addr = state as *const EngineState as usize;
    let ctx = context.to_string();
    let ctx_id = context_id.to_string();
    let op_name = op.name.clone();
    let registry = registry.clone();

    tokio::task::spawn_blocking(move || {
        // Acquire semaphore for stream contexts (backpressure).
        // block_on inside spawn_blocking is fine — we're on a blocking thread.
        let is_stream = ctx != ctx_id;
        let _permit = if is_stream {
            Some(runtime::block_on_async(async {
                semaphore.acquire().await.expect("semaphore closed")
            }))
        } else {
            None
        };

        // SAFETY: op and state are alive — caller awaits all tasks
        let op = unsafe { &*(op_addr as *const OpConfig) };
        let state = unsafe { &*(state_addr as *const EngineState) };

        match base::run(op, state, &ctx, &registry) {
            Ok(base::OpResult::Pending) => {
                let _ = event_tx.send(SchedulerEvent::DonePending(op_name, ctx));
            }
            Ok(base::OpResult::Done) => {
                let _ = event_tx.send(SchedulerEvent::Done(op_name, ctx));
            }
            Err(e) => {
                state.set(&op.full_name, "error", &ctx, Value::String(format!("{}", e)));
                log::error!("[rush] Error in op {}: {}", op.full_name, e);
                let _ = event_tx.send(SchedulerEvent::Done(op_name, ctx));
            }
        }
    });
}

/// Spawn a generator op as a tokio task.
///
/// Built-in generators: call `builtin_ops::call_generator()` → get Vec<Value> → emit Yield per item.
/// Provider streaming: call `execute_streaming()` via channel → receive chunks → emit Yield per chunk.
///
/// SAFETY: same as spawn_graph_task — caller awaits all tasks.
fn spawn_generator_task(
    op: &OpConfig,
    state: &EngineState,
    context: &str,
    context_id: &str,
    event_tx: mpsc::UnboundedSender<SchedulerEvent>,
    _semaphore: Arc<Semaphore>,
    registry: &Option<Arc<dyn OpRegistry>>,
) {
    let op_addr = op as *const OpConfig as usize;
    let state_addr = state as *const EngineState as usize;
    let ctx = context.to_string();
    let _ctx_id = context_id.to_string();
    let op_name = op.name.clone();
    let registry = registry.clone();

    tokio::spawn(async move {
        // SAFETY: op and state are alive — caller awaits all tasks
        let op = unsafe { &*(op_addr as *const OpConfig) };
        let state = unsafe { &*(state_addr as *const EngineState) };

        // Resolve inputs
        let mut inputs = serde_json::Map::new();
        for param in &op.inputs {
            match base::resolve_param(param, state, &ctx) {
                Ok(Some(value)) => { inputs.insert(param.var_name.clone(), value); }
                Ok(None) => {}
                Err(e) => {
                    log::error!("[rush] Error resolving generator inputs for {}: {}", op.full_name, e);
                    let _ = event_tx.send(SchedulerEvent::Exhausted(op_name));
                    return;
                }
            }
        }

        let input_value = Value::Object(inputs);

        // Dispatch based on generator kind
        if let Some(ref rust_op) = op.rust_op {
            // Registry generator: call_generator → Vec<Value>
            run_registry_generator(op, state, &ctx, rust_op, &input_value, &op_name, &event_tx, &registry);
        } else if op.provider_config.is_some() && op.stream {
            // Provider streaming (LLM): use execute_streaming with channel
            run_provider_streaming(op, state, &ctx, &input_value, &op_name, &event_tx).await;
        } else {
            log::error!("[rush] Generator op '{}' has no rust_op or streaming provider", op.full_name);
        }

        let _ = event_tx.send(SchedulerEvent::Exhausted(op_name));
    });
}

/// Run a built-in generator inline (no tokio::spawn).
///
/// Resolves inputs, calls call_generator() synchronously, sends Yield events
/// to the channel, then sends Exhausted. Eliminates thread scheduling overhead
/// for CPU-bound generators (avoids Windows 15.6ms timer quantum spikes).
fn run_builtin_generator_inline(
    op: &OpConfig,
    state: &EngineState,
    ctx: &str,
    event_tx: &mpsc::UnboundedSender<SchedulerEvent>,
    registry: &Option<Arc<dyn OpRegistry>>,
) {
    // Resolve inputs
    let mut inputs = serde_json::Map::new();
    for param in &op.inputs {
        match base::resolve_param(param, state, ctx) {
            Ok(Some(value)) => { inputs.insert(param.var_name.clone(), value); }
            Ok(None) => {}
            Err(e) => {
                log::error!("[rush] Error resolving generator inputs for {}: {}", op.full_name, e);
                let _ = event_tx.send(SchedulerEvent::Exhausted(op.name.clone()));
                return;
            }
        }
    }

    let input_value = Value::Object(inputs);
    let rust_op = op.rust_op.as_ref().unwrap();
    run_registry_generator(op, state, ctx, rust_op, &input_value, &op.name, event_tx, registry);
    let _ = event_tx.send(SchedulerEvent::Exhausted(op.name.clone()));
}

/// Run a generator via the registry: call_generator → iterate Vec<Value> → emit Yield per item.
fn run_registry_generator(
    op: &OpConfig,
    state: &EngineState,
    ctx: &str,
    rust_op: &str,
    input_value: &Value,
    op_name: &str,
    event_tx: &mpsc::UnboundedSender<SchedulerEvent>,
    registry: &Option<Arc<dyn OpRegistry>>,
) {
    let func_name = rust_op.rsplit("::").next().unwrap_or(rust_op);

    let items = registry
        .as_ref()
        .and_then(|r| r.call_generator(func_name, input_value));

    match items {
        Some(items) => {
            for (idx, result) in items.into_iter().enumerate() {
                let stream_ctx = format!("{}.[{}]", ctx, idx);

                // Store result in state under the generator's full_name + stream context
                if let Value::Object(ref map) = result {
                    for (key, value) in map {
                        if !key.starts_with('$') {
                            state.set(&op.full_name, key, &stream_ctx, value.clone());
                        }
                    }
                }

                let _ = event_tx.send(SchedulerEvent::Yield(
                    op_name.to_string(),
                    stream_ctx,
                    result,
                ));
            }
        }
        None => {
            log::error!(
                "[rush] Unknown generator: '{}' (from rust_op='{}') — no registry loaded or op not found",
                func_name, rust_op
            );
        }
    }
}

/// Run provider streaming: execute_streaming with channel → receive chunks → emit Yield.
async fn run_provider_streaming(
    op: &OpConfig,
    state: &EngineState,
    ctx: &str,
    _input_value: &Value,
    op_name: &str,
    event_tx: &mpsc::UnboundedSender<SchedulerEvent>,
) {
    let config = match op.provider_config.as_ref() {
        Some(c) => c,
        None => return,
    };

    // Resolve inputs fresh for the provider call
    let mut inputs = serde_json::Map::new();
    for param in &op.inputs {
        if let Ok(Some(value)) = base::resolve_param(param, state, ctx) {
            inputs.insert(param.var_name.clone(), value);
        }
    }

    // Use std::sync::mpsc channel (execute_streaming expects std::sync::mpsc::Sender)
    let (chunk_tx, chunk_rx) = std::sync::mpsc::channel::<Value>();

    // Spawn the provider streaming call using unsafe ptr (ProviderConfig doesn't impl Clone)
    let op_type = op.op_type.clone();
    let config_addr = config as *const rush_providers::config::ProviderConfig as usize;
    let provider_handle = tokio::spawn(async move {
        // SAFETY: config is alive — parent op outlives this task
        let config = unsafe { &*(config_addr as *const rush_providers::config::ProviderConfig) };
        rush_providers::ops::execute_streaming(
            &op_type,
            Value::Object(inputs),
            config,
            chunk_tx,
        )
        .await
    });

    // Receive chunks and emit Yield events
    // Use try_recv in a loop with yield_now to avoid blocking the async runtime
    let mut idx = 0;
    loop {
        match chunk_rx.try_recv() {
            Ok(chunk) => {
                let stream_ctx = format!("{}.[{}]", ctx, idx);

                // Store chunk in state
                if let Value::Object(ref map) = chunk {
                    for (key, value) in map {
                        if !key.starts_with('$') {
                            state.set(&op.full_name, key, &stream_ctx, value.clone());
                        }
                    }
                }

                let _ = event_tx.send(SchedulerEvent::Yield(
                    op_name.to_string(),
                    stream_ctx,
                    chunk,
                ));
                idx += 1;
            }
            Err(std::sync::mpsc::TryRecvError::Empty) => {
                // Check if provider task is done
                if provider_handle.is_finished() {
                    // Drain remaining items
                    while let Ok(chunk) = chunk_rx.try_recv() {
                        let stream_ctx = format!("{}.[{}]", ctx, idx);
                        if let Value::Object(ref map) = chunk {
                            for (key, value) in map {
                                if !key.starts_with('$') {
                                    state.set(&op.full_name, key, &stream_ctx, value.clone());
                                }
                            }
                        }
                        let _ = event_tx.send(SchedulerEvent::Yield(
                            op_name.to_string(),
                            stream_ctx,
                            chunk,
                        ));
                        idx += 1;
                    }
                    break;
                }
                tokio::task::yield_now().await;
            }
            Err(std::sync::mpsc::TryRecvError::Disconnected) => break,
        }
    }

    // Handle provider errors
    match provider_handle.await {
        Ok(Err(e)) => {
            state.set(
                &op.full_name,
                "error",
                ctx,
                Value::String(format!("Streaming provider error: {}", e)),
            );
            log::error!("[rush] Streaming error in op {}: {}", op.full_name, e);
        }
        Err(e) => {
            log::error!("[rush] Provider task panicked for {}: {}", op.full_name, e);
        }
        Ok(Ok(_)) => {}
    }
}

// =============================================================================
// Loop execution (replaces WhileOp)
// =============================================================================

/// Run feedback loop iterations until condition met or max reached.
///
/// Mirrors Python's `_loop.py::run_loop()`. After the first scheduler pass,
/// evaluates `until` condition. If not met, feeds outputs back as inputs
/// and runs another iteration with a new context.
async fn run_loop(
    config: &GraphConfig,
    loop_config: &LoopConfig,
    state: &EngineState,
    base_context: &str,
    registry: &Option<Arc<dyn OpRegistry>>,
) -> Result<(), RushError> {
    let mut current_ctx = base_context.to_string();
    let mut iteration = 0;

    loop {
        // Collect current outputs (loops don't use stream aggregation at loop level)
        let outputs = get_outputs(config, state, &current_ctx, &[])?;
        let outputs_map = match &outputs {
            Value::Object(m) => m,
            _ => return Err(RushError::LoopError("Loop outputs not an object".into())),
        };

        // Check until condition
        if loop_eval::evaluate_until_safe(
            &config.full_name,
            loop_config.until.as_deref(),
            outputs_map,
        ) {
            // Store loop metrics
            state.set(
                &config.full_name,
                "_loop_metrics",
                base_context,
                serde_json::json!({
                    "total_iterations": iteration + 1,
                    "stopped_by_condition": true,
                }),
            );
            // Copy final outputs back to base_context so caller can find them
            copy_outputs_to_base(config, state, &current_ctx, base_context, outputs_map);
            return Ok(());
        }

        iteration += 1;
        if iteration >= loop_config.max_iterations {
            state.set(
                &config.full_name,
                "_loop_metrics",
                base_context,
                serde_json::json!({
                    "total_iterations": iteration,
                    "stopped_by_condition": false,
                    "max_iterations_reached": true,
                }),
            );
            // Copy final outputs back to base_context so caller can find them
            copy_outputs_to_base(config, state, &current_ctx, base_context, outputs_map);
            return Ok(());
        }

        // Feed outputs back as inputs for next iteration
        let next_ctx = format!("{}.loop_{}", base_context, iteration);
        for (var_name, value) in outputs_map {
            if !var_name.starts_with('$') && !var_name.starts_with('_') {
                state.set(&config.full_name, var_name, &next_ctx, value.clone());
            }
        }

        // Run next iteration (ignore stream_contexts for loop iterations)
        let _sc = run_scheduler(config, state, &next_ctx, registry).await?;
        current_ctx = next_ctx;
    }
}

/// Copy final loop outputs from the last iteration context back to the base context.
/// This ensures collect_nested_outputs (which reads base_context) finds the final values.
fn copy_outputs_to_base(
    config: &GraphConfig,
    state: &EngineState,
    current_ctx: &str,
    base_context: &str,
    outputs_map: &serde_json::Map<String, Value>,
) {
    if current_ctx == base_context {
        return; // First iteration — already at base context
    }
    // Copy graph-level outputs
    for (var_name, value) in outputs_map {
        if !var_name.starts_with('$') && !var_name.starts_with('_') {
            state.set(&config.full_name, var_name, base_context, value.clone());
        }
    }
    // Copy terminal op outputs so auto-forward works
    for (op_name, adj_list) in &config.compiled_adj {
        let is_terminal = adj_list.iter().any(|e| e.target == "__end__");
        if is_terminal {
            if let Some(op) = config.ops.get(op_name) {
                for (key, value) in state.get_all_with_prefix(&op.full_name, current_ctx) {
                    if !key.starts_with('$') {
                        state.set(&op.full_name, &key, base_context, value);
                    }
                }
            }
        }
    }
}

// =============================================================================
// Output collection
// =============================================================================

/// Collect graph outputs as a serde_json::Value (JSON object).
///
/// When explicit `outputs` are defined, resolves each param via refs or graph state.
/// When `outputs` is empty, auto-forwards all values from terminal ops (those
/// whose adjacency points to `__end__`), matching Python's `>> END` behavior.
pub(crate) fn get_outputs(
    config: &GraphConfig,
    state: &EngineState,
    context: &str,
    stream_contexts: &[String],
) -> Result<Value, RushError> {
    let mut result = serde_json::Map::new();

    // Determine leaf stream contexts (not a prefix of any other stream context)
    let leaf_contexts: Vec<&String> = if stream_contexts.is_empty() {
        Vec::new()
    } else {
        stream_contexts
            .iter()
            .filter(|sc| {
                !stream_contexts
                    .iter()
                    .any(|other| other != *sc && other.starts_with(sc.as_str()))
            })
            .collect()
    };

    let has_streaming = !leaf_contexts.is_empty();

    if !config.outputs.is_empty() {
        if has_streaming {
            // Explicit outputs + streaming: aggregate from leaf contexts as lists
            for param in &config.outputs {
                // Try to find the source op/var from ref_config
                if let Some(ref ref_config) = param.ref_config {
                    let src_full_name = &ref_config.source;
                    let src_var = &ref_config.var;
                    let mut values = Vec::new();
                    for sc in &leaf_contexts {
                        if let Some(arc_val) = state.get(src_full_name, src_var, sc) {
                            values.push((*arc_val).clone());
                        }
                    }
                    if !values.is_empty() {
                        result.insert(param.var_name.clone(), Value::Array(values));
                    }
                } else {
                    // No ref — search terminal ops for this var across leaf contexts
                    let terminal_ops = find_terminal_ops(config);
                    let mut values = Vec::new();
                    for t_name in &terminal_ops {
                        if let Some(t_op) = config.ops.get(t_name.as_str()) {
                            for sc in &leaf_contexts {
                                if let Some(arc_val) = state.get(&t_op.full_name, &param.var_name, sc) {
                                    values.push((*arc_val).clone());
                                }
                            }
                            if !values.is_empty() {
                                break;
                            }
                        }
                    }
                    if !values.is_empty() {
                        result.insert(param.var_name.clone(), Value::Array(values));
                    }
                }
            }
        } else {
            // Explicit output mappings (no streaming)
            for param in &config.outputs {
                if let Some(value) = base::resolve_param(param, state, context)? {
                    result.insert(param.var_name.clone(), value);
                } else if let Some(arc_val) = state.get(&config.full_name, &param.var_name, context) {
                    let value = Arc::try_unwrap(arc_val).unwrap_or_else(|arc| (*arc).clone());
                    result.insert(param.var_name.clone(), value);
                }
            }
        }
    } else {
        if has_streaming {
            // Auto-forward + streaming: find terminal ops, aggregate from leaf contexts
            let terminal_ops = find_terminal_ops(config);
            for op_name in &terminal_ops {
                if let Some(op) = config.ops.get(op_name.as_str()) {
                    // Discover vars from the first leaf context
                    let first_vars: Vec<(String, Value)> =
                        state.get_all_with_prefix(&op.full_name, &leaf_contexts[0]);
                    for (key, _) in &first_vars {
                        if key.starts_with('$') {
                            continue;
                        }
                        let mut values = Vec::new();
                        for sc in &leaf_contexts {
                            if let Some(arc_val) = state.get(&op.full_name, key, sc) {
                                values.push((*arc_val).clone());
                            }
                        }
                        if !values.is_empty() {
                            result.insert(key.clone(), Value::Array(values));
                        }
                    }
                }
            }
        } else {
            // Auto-forward: collect all values from terminal ops (ops → __end__)
            let terminal_ops = find_terminal_ops(config);
            for op_name in &terminal_ops {
                if let Some(op) = config.ops.get(op_name.as_str()) {
                    let prefix = &op.full_name;
                    for (key, value) in state.get_all_with_prefix(prefix, context) {
                        if !key.starts_with('$') {
                            result.insert(key, value);
                        }
                    }
                }
            }
        }
    }

    Ok(Value::Object(result))
}

/// Find terminal ops: ops with edges to `__end__` OR ops with empty adjacency lists.
fn find_terminal_ops(config: &GraphConfig) -> Vec<String> {
    let mut terminals = Vec::new();
    for (op_name, adj_list) in &config.compiled_adj {
        let is_terminal = adj_list.is_empty() || adj_list.iter().any(|e| e.target == "__end__");
        if is_terminal {
            terminals.push(op_name.clone());
        }
    }
    terminals
}

// =============================================================================
// Nested graph execution (async)
// =============================================================================

/// Execute a nested GraphOp asynchronously: resolve inputs, run inner scheduler, push outputs.
///
/// Called from tokio::spawn — must NOT use block_on (would deadlock).
/// Calls run_scheduler directly since we're already in an async context.
async fn run_nested_graph_async(
    op: &OpConfig,
    state: &EngineState,
    context: &str,
    registry: &Option<Arc<dyn OpRegistry>>,
) -> Result<(), RushError> {
    let inner = op.inner_graph.as_ref().ok_or_else(|| {
        RushError::ConfigError(format!(
            "Graph op '{}' missing inner_graph config",
            op.full_name
        ))
    })?;

    // 1. Resolve inputs from parent scope
    for param in &op.inputs {
        if let Some(value) = base::resolve_param(param, state, context)? {
            state.set(&op.full_name, &param.var_name, context, value);
        }
    }

    // 2. Run inner graph scheduling loop (async — no block_on needed)
    let stream_contexts = run_scheduler(inner, state, context, registry).await?;

    // 2b. If this graph has a loop_config, run feedback loop
    if let Some(ref loop_config) = inner.loop_config {
        run_loop(inner, loop_config, state, context, registry).await?;
    }

    // 3. Collect inner graph outputs
    collect_nested_outputs(op, inner, state, context, &stream_contexts)?;

    // 4. Push output refs
    base::push_output_refs(op, state, context)?;

    Ok(())
}

/// Collect outputs from a nested graph and store them under the parent op.
fn collect_nested_outputs(
    op: &OpConfig,
    inner: &GraphConfig,
    state: &EngineState,
    context: &str,
    stream_contexts: &[String],
) -> Result<(), RushError> {
    if !stream_contexts.is_empty() {
        // Streaming: delegate to get_outputs which handles aggregation
        let aggregated = get_outputs(inner, state, context, stream_contexts)?;
        if let Value::Object(map) = aggregated {
            for (key, value) in map {
                if !key.starts_with('$') {
                    state.set(&op.full_name, &key, context, value);
                }
            }
        }
    } else if !inner.outputs.is_empty() {
        for param in &inner.outputs {
            if let Some(value) = base::resolve_param(param, state, context)? {
                state.set(&op.full_name, &param.var_name, context, value);
            } else if let Some(arc_val) =
                state.get(&inner.full_name, &param.var_name, context)
            {
                let value = Arc::try_unwrap(arc_val).unwrap_or_else(|arc| (*arc).clone());
                state.set(&op.full_name, &param.var_name, context, value);
            }
        }
    } else {
        // Auto-forward from terminal ops (>> END)
        for (inner_op_name, adj_list) in &inner.compiled_adj {
            let is_terminal = adj_list.iter().any(|e| e.target == "__end__");
            if is_terminal {
                if let Some(inner_op) = inner.ops.get(inner_op_name) {
                    for (key, value) in
                        state.get_all_with_prefix(&inner_op.full_name, context)
                    {
                        if !key.starts_with('$') {
                            state.set(&op.full_name, &key, context, value);
                        }
                    }
                }
            }
        }
    }

    Ok(())
}
