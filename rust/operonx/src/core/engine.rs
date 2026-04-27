//! `Operon` engine + `ExecutionHandle`.
//!
//! Mirrors Python [`operon/core/engine.py`](../../../../operon/core/engine.py).
//!
//! The engine is the user-facing entry point:
//!
//! ```rust,ignore
//! // Auto-load .env and ./resources.yaml:
//! let engine = Operon::new(&graph_json)?;
//!
//! // Or configure explicitly:
//! let engine = Operon::builder(&graph_json)
//!     .resources("configs/prod.yaml")
//!     .tracer(Arc::new(my_tracer))
//!     .middleware(Arc::new(log_mw))
//!     .build()?;
//!
//! // Sync one-shot:
//! let result = engine.run_json(inputs, None, None, None)?;
//!
//! // Streaming:
//! let handle = engine.start(inputs, None, None, None, None)?;
//! while let Some(frame) = handle.next().await { ... }
//! ```
//!
//! # Phase 3 scope
//! This file delivers the full **engine lifecycle** + **ExecutionHandle stream /
//! wait_for / collect / cancel** surface. The scheduler itself is a `Scheduler`
//! trait with a single stub implementation that errors until Phase 4 lands
//! the real graph walker.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::task::{Context, Poll};

use async_trait::async_trait;
use futures::Stream;
use parking_lot::Mutex;
use serde_json::{Map, Value};
use tokio::sync::{mpsc, oneshot, Notify};
use tokio::task::JoinHandle;
use tokio_util::sync::CancellationToken;
use tracing::{debug, info, warn};

use crate::core::configs::op_config::OpConfig;
use crate::core::exceptions::{OperonError, SUPPORTED_SCHEMA_VERSION};
use crate::core::middleware::{Middleware, MiddlewareContext};
use crate::core::ops::graph::task_scheduler::GraphScheduler;
use crate::core::ops::graph::validation::validate_graph;
use crate::core::registry::{InMemoryOpRegistry, OpRegistry, ResourceHub};
use crate::core::states::cell::ContextId;
use crate::core::tracing::base::Tracer;

// ── FrameEvent ───────────────────────────────────────────────────────────

/// One output frame emitted by the scheduler — mirrors Python's
/// `(op_name, ctx, data)` tuple pushed onto the output queue.
#[derive(Debug, Clone)]
pub struct FrameEvent {
    /// The op that produced the frame (fully-qualified name).
    pub op: String,
    /// Execution context where the frame originated.
    pub context: ContextId,
    /// Output data — typically the op's `outputs` dict.
    pub data: Map<String, Value>,
}

/// Aggregation shape for [`ExecutionHandle::collect`].
///
/// - [`CollectMode::Group`] merges values by output key into lists.
/// - [`CollectMode::Flat`] returns an ordered list of raw frame data dicts.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CollectMode {
    Group,
    Flat,
}

impl Default for CollectMode {
    fn default() -> Self {
        Self::Group
    }
}

// ── ExecutionHandle ──────────────────────────────────────────────────────

/// Async-iterable handle for a running workflow execution.
///
/// Mirrors Python's `ExecutionHandle`. The engine hands this back from
/// [`Operon::start`]; the caller streams frames via [`futures::StreamExt`],
/// awaits specific outputs via [`wait_for`](Self::wait_for), or aggregates
/// everything via [`collect`](Self::collect).
///
/// Dropping the handle aborts the pump and the scheduler task.
pub struct ExecutionHandle {
    inner: Arc<HandleInner>,
    /// Per-handle stream cursor — advances as `Stream::poll_next` returns items.
    cursor: usize,
    /// Wakeup future currently registered with `inner.notify` (set by poll_next).
    notified: Option<Pin<Box<dyn std::future::Future<Output = ()> + Send>>>,
    pump: Option<JoinHandle<()>>,
}

/// Shared state between the handle, the pump, and the scheduler.
struct HandleInner {
    buffered: Mutex<Vec<FrameEvent>>,
    waiters: Mutex<HashMap<(String, String), Vec<oneshot::Sender<Option<Value>>>>>,
    done: AtomicBool,
    error: Mutex<Option<OperonError>>,
    notify: Notify,
    cancel: CancellationToken,
    /// Handle to the scheduler task, so `cancel()` / `drop()` can abort it.
    scheduler_task: Mutex<Option<JoinHandle<Result<(), OperonError>>>>,
    /// Per-run metadata (user_id / session_id / request_id).
    ctx: MiddlewareContext,
}

/// Message type pushed onto the scheduler → handle channel.
///
/// `End` signals normal completion; `Error` signals failure. Anything else
/// is a frame to buffer + match against waiters.
#[derive(Debug)]
enum PumpMsg {
    Frame(FrameEvent),
    End,
    Error(OperonError),
}

/// Producer side of the scheduler → handle channel. Handed to the
/// [`Scheduler`] implementation.
///
/// Holds an optional [`TraceTap`] — a shared `Vec<FrameEvent>` that receives
/// every frame synchronously as it's pushed onto the mpsc channel. The
/// engine uses this tap to build a complete trace payload after the
/// scheduler task returns, without racing the async pump loop.
#[derive(Clone)]
pub struct FrameSender {
    tx: mpsc::Sender<PumpMsg>,
    tap: Option<TraceTap>,
    /// When `true`, `send`/`finish`/`fail` become no-ops on the public
    /// channel. The trace tap still captures. Toggled by [`silent()`] —
    /// see that method's docs for why.
    silent: bool,
}

/// Shared frame buffer fed by [`FrameSender::send`]. Cloned into both the
/// engine task (for tracer hand-off) and — transitively — the scheduler.
pub type TraceTap = Arc<Mutex<Vec<FrameEvent>>>;

impl FrameSender {
    /// Push a frame. Returns `Err` if the handle has been dropped.
    pub async fn send(&self, frame: FrameEvent) -> Result<(), OperonError> {
        if let Some(tap) = &self.tap {
            tap.lock().push(frame.clone());
        }
        if self.silent {
            return Ok(());
        }
        self.tx
            .send(PumpMsg::Frame(frame))
            .await
            .map_err(|_| OperonError::Runtime("execution handle dropped".into()))
    }

    /// Signal normal scheduler completion.
    pub async fn finish(&self) {
        if self.silent {
            return;
        }
        let _ = self.tx.send(PumpMsg::End).await;
    }

    /// Report a scheduler error.
    pub async fn fail(&self, error: OperonError) {
        if self.silent {
            return;
        }
        let _ = self.tx.send(PumpMsg::Error(error)).await;
    }

    /// A clone that **suppresses** outbound frames on the public channel.
    /// The trace tap still fires, and the inner scheduler still receives
    /// Frame/Eof events via its own mpsc — only `sender.send()` becomes a
    /// no-op. Used by the loop re-dispatch path to match Python's behavior:
    /// subsequent iterations execute silently; only the initial iteration +
    /// the final summary frame reach the handle.
    pub fn silent(&self) -> Self {
        Self {
            tx: self.tx.clone(),
            tap: self.tap.clone(),
            silent: true,
        }
    }
}

impl ExecutionHandle {
    /// Build an empty handle + the [`FrameSender`] the scheduler writes to.
    ///
    /// Engine-internal — callers should use [`Operon::start`].
    pub(crate) fn new(
        cancel: CancellationToken,
        ctx: MiddlewareContext,
    ) -> (Self, FrameSender) {
        Self::new_with_tap(cancel, ctx, None)
    }

    /// As [`ExecutionHandle::new`], but with an optional [`TraceTap`] that
    /// mirrors every frame. The engine uses this for synchronous collector
    /// access after the scheduler task completes.
    pub(crate) fn new_with_tap(
        cancel: CancellationToken,
        ctx: MiddlewareContext,
        tap: Option<TraceTap>,
    ) -> (Self, FrameSender) {
        let (tx, rx) = mpsc::channel::<PumpMsg>(64);
        let inner = Arc::new(HandleInner {
            buffered: Mutex::new(Vec::new()),
            waiters: Mutex::new(HashMap::new()),
            done: AtomicBool::new(false),
            error: Mutex::new(None),
            notify: Notify::new(),
            cancel,
            scheduler_task: Mutex::new(None),
            ctx,
        });
        let inner_clone = inner.clone();
        let pump = tokio::spawn(async move { pump_loop(rx, inner_clone).await });
        (
            Self {
                inner,
                cursor: 0,
                notified: None,
                pump: Some(pump),
            },
            FrameSender { tx, tap, silent: false },
        )
    }

    /// Attach the scheduler task — the handle aborts it on `cancel()` / drop.
    pub(crate) fn set_scheduler_task(&self, task: JoinHandle<Result<(), OperonError>>) {
        *self.inner.scheduler_task.lock() = Some(task);
    }

    /// Per-run metadata (user_id / session_id / request_id).
    pub fn context(&self) -> &MiddlewareContext {
        &self.inner.ctx
    }

    /// Frame count received so far.
    pub fn frame_count(&self) -> usize {
        self.inner.buffered.lock().len()
    }

    /// `true` once the scheduler has emitted End or an error.
    pub fn is_done(&self) -> bool {
        self.inner.done.load(Ordering::Acquire)
    }

    /// Resolve the **last** value seen for `(op, var)` across all frames so
    /// far; if none are buffered yet, block until one arrives (or execution
    /// completes).
    pub async fn wait_for(&self, op: &str, var: &str) -> Result<Option<Value>, OperonError> {
        // Scan existing buffer for a match (last-wins).
        {
            let buf = self.inner.buffered.lock();
            let mut last: Option<Value> = None;
            for f in buf.iter() {
                if f.op == op {
                    if let Some(v) = f.data.get(var) {
                        last = Some(v.clone());
                    }
                }
            }
            if let Some(v) = last {
                return Ok(Some(v));
            }
            if self.inner.done.load(Ordering::Acquire) {
                if let Some(e) = self.inner.error.lock().as_ref() {
                    return Err(clone_error(e));
                }
                return Ok(None);
            }
        }

        // Register a oneshot and wait.
        let (tx, rx) = oneshot::channel::<Option<Value>>();
        self.inner
            .waiters
            .lock()
            .entry((op.to_string(), var.to_string()))
            .or_default()
            .push(tx);
        match rx.await {
            Ok(v) => Ok(v),
            Err(_) => {
                if let Some(e) = self.inner.error.lock().as_ref() {
                    Err(clone_error(e))
                } else {
                    Ok(None)
                }
            }
        }
    }

    /// Drain every remaining frame and aggregate.
    ///
    /// - [`CollectMode::Group`]: `{var: [v1, v2, …]}` — values grouped by key.
    ///   When `unwrap = true`, single-element lists collapse to the scalar.
    /// - [`CollectMode::Flat`]: ordered `[data1, data2, …]` — raw frames as
    ///   JSON arrays (emitted as `Value::Array` for easy downstream use).
    pub async fn collect(&mut self, mode: CollectMode, unwrap: bool) -> Result<Value, OperonError> {
        use futures::StreamExt;
        match mode {
            CollectMode::Flat => {
                let mut frames = Vec::new();
                while let Some(frame) = self.next().await {
                    let frame = frame?;
                    frames.push(Value::Object(frame.data));
                }
                Ok(Value::Array(frames))
            }
            CollectMode::Group => {
                let mut out: HashMap<String, Vec<Value>> = HashMap::new();
                while let Some(frame) = self.next().await {
                    let frame = frame?;
                    for (k, v) in frame.data {
                        out.entry(k).or_default().push(v);
                    }
                }
                let mut merged = Map::with_capacity(out.len());
                for (k, mut vs) in out {
                    if unwrap && vs.len() == 1 {
                        merged.insert(k, vs.remove(0));
                    } else {
                        merged.insert(k, Value::Array(vs));
                    }
                }
                Ok(Value::Object(merged))
            }
        }
    }

    /// Build the result from the buffered frames — does **not** consume the
    /// stream. Safe to call after iterating; blocks until execution completes.
    pub async fn result(&self, unwrap: bool) -> Result<Value, OperonError> {
        // Wait for completion.
        loop {
            if self.inner.done.load(Ordering::Acquire) {
                break;
            }
            self.inner.notify.notified().await;
        }
        if let Some(e) = self.inner.error.lock().as_ref() {
            return Err(clone_error(e));
        }
        let buf = self.inner.buffered.lock();
        let mut out: HashMap<String, Vec<Value>> = HashMap::new();
        for f in buf.iter() {
            for (k, v) in &f.data {
                out.entry(k.clone()).or_default().push(v.clone());
            }
        }
        let mut merged = Map::with_capacity(out.len());
        for (k, mut vs) in out {
            if unwrap && vs.len() == 1 {
                merged.insert(k, vs.remove(0));
            } else {
                merged.insert(k, Value::Array(vs));
            }
        }
        Ok(Value::Object(merged))
    }

    /// Cancel the workflow execution. Aborts both the scheduler task and the
    /// pump. Pending waiters resolve with `None`.
    pub fn cancel(&self) {
        self.inner.cancel.cancel();
        if let Some(task) = self.inner.scheduler_task.lock().take() {
            task.abort();
        }
        self.inner.done.store(true, Ordering::Release);
        resolve_all(&self.inner, None);
        self.inner.notify.notify_waiters();
    }
}

impl Drop for ExecutionHandle {
    fn drop(&mut self) {
        // Best-effort: abort pump + scheduler.
        if let Some(task) = self.inner.scheduler_task.lock().take() {
            task.abort();
        }
        if let Some(pump) = self.pump.take() {
            pump.abort();
        }
    }
}

impl Stream for ExecutionHandle {
    type Item = Result<FrameEvent, OperonError>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        loop {
            // Fast path — buffered frame available at cursor.
            {
                let buf = self.inner.buffered.lock();
                if self.cursor < buf.len() {
                    let frame = buf[self.cursor].clone();
                    drop(buf);
                    self.cursor += 1;
                    self.notified = None;
                    return Poll::Ready(Some(Ok(frame)));
                }
                if self.inner.done.load(Ordering::Acquire) {
                    if let Some(e) = self.inner.error.lock().as_ref() {
                        return Poll::Ready(Some(Err(clone_error(e))));
                    }
                    return Poll::Ready(None);
                }
            }

            // Register a wake-up on `notify` — we must hold this future across
            // polls so we keep the waker slot active.
            if self.notified.is_none() {
                let inner = self.inner.clone();
                let fut = async move { inner.notify.notified().await };
                self.notified = Some(Box::pin(fut));
            }
            let fut = self.notified.as_mut().unwrap();
            match fut.as_mut().poll(cx) {
                Poll::Ready(()) => {
                    self.notified = None;
                    // Loop and re-check the buffer.
                    continue;
                }
                Poll::Pending => return Poll::Pending,
            }
        }
    }
}

// ── Pump loop ────────────────────────────────────────────────────────────

async fn pump_loop(mut rx: mpsc::Receiver<PumpMsg>, inner: Arc<HandleInner>) {
    loop {
        tokio::select! {
            _ = inner.cancel.cancelled() => {
                inner.done.store(true, Ordering::Release);
                resolve_all(&inner, None);
                inner.notify.notify_waiters();
                return;
            }
            msg = rx.recv() => {
                match msg {
                    None => {
                        // Sender dropped without explicit End → treat as completion.
                        inner.done.store(true, Ordering::Release);
                        resolve_all(&inner, None);
                        inner.notify.notify_waiters();
                        return;
                    }
                    Some(PumpMsg::End) => {
                        inner.done.store(true, Ordering::Release);
                        resolve_all(&inner, None);
                        inner.notify.notify_waiters();
                        return;
                    }
                    Some(PumpMsg::Error(e)) => {
                        *inner.error.lock() = Some(clone_error(&e));
                        inner.done.store(true, Ordering::Release);
                        resolve_all(&inner, None);
                        inner.notify.notify_waiters();
                        return;
                    }
                    Some(PumpMsg::Frame(frame)) => {
                        // Buffer, then match waiters against the new frame.
                        let pending = {
                            let mut w = inner.waiters.lock();
                            let mut to_fire: Vec<(String, String, Option<Value>)> = Vec::new();
                            for (k, v) in &frame.data {
                                let key = (frame.op.clone(), k.clone());
                                if w.contains_key(&key) {
                                    to_fire.push((key.0, key.1, Some(v.clone())));
                                }
                            }
                            let mut fired: Vec<(oneshot::Sender<Option<Value>>, Option<Value>)> =
                                Vec::new();
                            for (op, var, val) in to_fire {
                                if let Some(senders) = w.remove(&(op, var)) {
                                    for tx in senders {
                                        fired.push((tx, val.clone()));
                                    }
                                }
                            }
                            fired
                        };
                        inner.buffered.lock().push(frame);
                        for (tx, val) in pending {
                            let _ = tx.send(val);
                        }
                        inner.notify.notify_waiters();
                    }
                }
            }
        }
    }
}

fn resolve_all(inner: &HandleInner, value: Option<Value>) {
    let mut waiters = inner.waiters.lock();
    let drained: Vec<_> = waiters.drain().collect();
    drop(waiters);
    for (_, senders) in drained {
        for tx in senders {
            let _ = tx.send(value.clone());
        }
    }
}

/// Deep-clone an [`OperonError`] — since the variants are all stringly-typed,
/// this just re-wraps the formatted message under the same variant.
fn clone_error(e: &OperonError) -> OperonError {
    match e {
        OperonError::Op(op) => OperonError::Runtime(op.to_string()),
        OperonError::Provider(s) => OperonError::Provider(s.clone()),
        OperonError::ResourceHub(s) => OperonError::ResourceHub(s.clone()),
        OperonError::Config(s) => OperonError::Config(s.clone()),
        OperonError::State(s) => OperonError::State(s.clone()),
        OperonError::Runtime(s) => OperonError::Runtime(s.clone()),
        OperonError::UnsupportedSchema(s) => OperonError::UnsupportedSchema(s.clone()),
        OperonError::EnvVarUnset {
            var,
            key,
            source_path,
            env_paths,
        } => OperonError::EnvVarUnset {
            var: var.clone(),
            key: key.clone(),
            source_path: source_path.clone(),
            env_paths: env_paths.clone(),
        },
    }
}

// ── Scheduler trait (stub until Phase 4) ────────────────────────────────

/// The piece Phase 4 implements: a runner that walks a parsed graph,
/// dispatches ops, and pushes frames via [`FrameSender`].
///
/// Phase 3 ships one impl — [`NotImplementedScheduler`] — that errors out
/// so `start()` / `run_json()` can be called but will surface a clear
/// "scheduler not yet wired" message.
#[async_trait]
pub trait Scheduler: Send + Sync {
    /// Run the workflow: consume `inputs`, write frames to `sender`, then
    /// `sender.finish()` on success or `sender.fail(e)` on error.
    async fn run(
        &self,
        inputs: Map<String, Value>,
        context: MiddlewareContext,
        sender: FrameSender,
        cancel: CancellationToken,
    ) -> Result<(), OperonError>;
}

/// Placeholder scheduler for Phase 3. Returns an error — Phase 4 replaces
/// this with a real graph walker that consumes the serialized `GraphConfig`.
pub struct NotImplementedScheduler;

#[async_trait]
impl Scheduler for NotImplementedScheduler {
    async fn run(
        &self,
        _inputs: Map<String, Value>,
        _context: MiddlewareContext,
        sender: FrameSender,
        _cancel: CancellationToken,
    ) -> Result<(), OperonError> {
        let err = OperonError::Runtime(
            "scheduler not yet implemented — lands in Phase 4 per MIGRATION_rust.md §11"
                .to_string(),
        );
        sender.fail(clone_error(&err)).await;
        Err(err)
    }
}

// ── Graph envelope ───────────────────────────────────────────────────────

/// Parsed + version-checked wrapper around a serialized workflow.
///
/// `schema_version` is stripped from the JSON before the rest is deserialized
/// into [`OpConfig`], so the inner config can stay schema-agnostic.
#[derive(Debug, Clone)]
pub struct GraphEnvelope {
    pub schema_version: String,
    pub config: OpConfig,
}

impl GraphEnvelope {
    /// Parse + version-check a serialized workflow JSON.
    pub fn parse(json: &str) -> Result<Self, OperonError> {
        let mut value: Value = serde_json::from_str(json)?;
        let map = value.as_object_mut().ok_or_else(|| {
            OperonError::Config("graph JSON must be a top-level object".into())
        })?;
        let schema_version = map
            .remove("schema_version")
            .ok_or_else(|| OperonError::Config("graph JSON missing schema_version".into()))?;
        let schema_version = schema_version
            .as_str()
            .ok_or_else(|| OperonError::Config("schema_version must be a string".into()))?
            .to_string();
        if schema_version != SUPPORTED_SCHEMA_VERSION {
            return Err(OperonError::UnsupportedSchema(schema_version));
        }
        // Default the graph type if upstream omitted it — Python omits the
        // outer `type` key in some paths.
        if !map.contains_key("type") {
            map.insert("type".into(), Value::String("graph".into()));
        }
        let config: OpConfig = serde_json::from_value(value)?;
        Ok(Self {
            schema_version,
            config,
        })
    }

    /// Top-level graph name.
    pub fn name(&self) -> &str {
        &self.config.name
    }
}

// ── Operon engine ────────────────────────────────────────────────────────

/// Workflow execution engine.
///
/// Mirrors Python's `Operon` class. Each instance holds a parsed graph
/// envelope + a resolved [`ResourceHub`] + a scheduler implementation +
/// optional middleware/tracers.
///
/// Construct via [`Operon::new`] for the auto-loading happy path, or
/// [`Operon::builder`] for explicit wiring.
pub struct Operon {
    graph: GraphEnvelope,
    name: String,
    hub: Arc<ResourceHub>,
    op_registry: Arc<dyn OpRegistry>,
    scheduler: Arc<dyn Scheduler>,
    middleware: Vec<Arc<dyn Middleware>>,
    tracers: Vec<Arc<dyn Tracer>>,
}

impl std::fmt::Debug for Operon {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Operon")
            .field("name", &self.name)
            .field("middleware_count", &self.middleware.len())
            .field("tracer_count", &self.tracers.len())
            .finish()
    }
}

impl Operon {
    /// Happy-path constructor: auto-loads `./.env` + `./resources.yaml`
    /// from CWD. Use [`Operon::builder`] when you need explicit paths or
    /// plumbing.
    pub fn new(graph_json: &str) -> Result<Self, OperonError> {
        OperonBuilder::new(graph_json).build()
    }

    /// Fluent builder — see [`OperonBuilder`].
    pub fn builder(graph_json: &str) -> OperonBuilder {
        OperonBuilder::new(graph_json)
    }

    /// Workflow name as declared in the serialized graph.
    pub fn name(&self) -> &str {
        &self.name
    }

    /// Borrow the active [`ResourceHub`].
    pub fn resources(&self) -> &Arc<ResourceHub> {
        &self.hub
    }

    /// Borrow the parsed graph envelope.
    pub fn graph(&self) -> &GraphEnvelope {
        &self.graph
    }

    /// Borrow the op registry — useful for inspection / post-construction
    /// registrations in tests.
    pub fn op_registry(&self) -> &Arc<dyn OpRegistry> {
        &self.op_registry
    }

    /// Append a middleware. Mirrors Python's chainable `engine.use(...)`.
    pub fn use_middleware(&mut self, mw: Arc<dyn Middleware>) -> &mut Self {
        self.middleware.push(mw);
        self
    }

    /// Start the workflow asynchronously and return a streaming handle.
    ///
    /// Does **not** run middleware — the chain is applied inside
    /// [`run_json_async`]. `start()` is the lower-level entry point for
    /// callers that want raw frame streaming.
    pub fn start(
        &self,
        inputs: Map<String, Value>,
        user_id: Option<String>,
        session_id: Option<String>,
        request_id: Option<String>,
        tracer_override: Option<Vec<Arc<dyn Tracer>>>,
    ) -> Result<ExecutionHandle, OperonError> {
        let ctx = MiddlewareContext {
            user_id: user_id.unwrap_or_else(new_uuid),
            session_id: session_id.unwrap_or_else(new_uuid),
            request_id: request_id.unwrap_or_else(new_uuid),
            extra: Map::new(),
        };

        let tracers = tracer_override.unwrap_or_else(|| self.tracers.clone());

        info!(
            "workflow_start request_id={} graph_name={}",
            ctx.request_id, self.name
        );

        let cancel = CancellationToken::new();
        // Allocate a shared TraceTap when any tracers are wired so the engine
        // task can build a `TraceData` from every emitted frame. Skip the tap
        // when there are no tracers — keeps the no-observability path cost
        // free.
        let tap: Option<TraceTap> = if tracers.is_empty() {
            None
        } else {
            Some(Arc::new(Mutex::new(Vec::new())))
        };
        let (handle, sender) =
            ExecutionHandle::new_with_tap(cancel.clone(), ctx.clone(), tap.clone());

        let scheduler = self.scheduler.clone();
        let graph_name = self.name.clone();
        let request_id = ctx.request_id.clone();
        let run_ctx = ctx.clone();
        let cancel_run = cancel.clone();
        let task = tokio::spawn(async move {
            let sender_finish = sender.clone();
            let result = scheduler
                .run(inputs, run_ctx.clone(), sender.clone(), cancel_run)
                .await;
            match &result {
                Ok(()) => sender_finish.finish().await,
                Err(e) => sender_finish.fail(clone_error(e)).await,
            }
            info!(
                "workflow_done request_id={} graph_name={}",
                request_id, graph_name
            );

            // Telemetry hand-off — collect from the shared tap and submit to
            // the flush worker. Fire-and-forget: the FlushWorker handle is
            // awaited so a panic in a tracer surfaces as a log, but any
            // flush error does not mask the original scheduler result.
            if let Some(tap) = &tap {
                if !tracers.is_empty() {
                    let frames = tap.lock().clone();
                    let trace_data = crate::core::tracing::TraceCollector::new(
                        graph_name.clone(),
                        request_id.clone(),
                    )
                    .with_user(Some(run_ctx.user_id.clone()))
                    .with_session(Some(run_ctx.session_id.clone()))
                    .collect_from_frames(&frames);
                    let flush = crate::core::tracing::FlushWorker::new()
                        .submit(tracers.clone(), trace_data);
                    if let Err(e) = flush.await {
                        warn!(
                            "flush_worker: join failed for request_id={}: {}",
                            request_id, e
                        );
                    }
                }
            }

            result
        });
        handle.set_scheduler_task(task);
        Ok(handle)
    }

    /// Synchronous one-shot — spins up a temporary tokio runtime, applies the
    /// middleware chain, and collects results.
    ///
    /// Mirrors Python's `engine.run(inputs)` minus the async. Callers inside
    /// an existing runtime should use [`run_json_async`] instead.
    pub fn run_json(
        &self,
        inputs: Map<String, Value>,
        user_id: Option<String>,
        session_id: Option<String>,
        request_id: Option<String>,
    ) -> Result<Value, OperonError> {
        use crate::core::runtime::get_runtime;
        get_runtime().block_on(self.run_json_async(inputs, user_id, session_id, request_id))
    }

    /// Async equivalent of [`run_json`] — applies middleware, runs the graph,
    /// returns the aggregated result dict.
    pub async fn run_json_async(
        &self,
        mut inputs: Map<String, Value>,
        user_id: Option<String>,
        session_id: Option<String>,
        request_id: Option<String>,
    ) -> Result<Value, OperonError> {
        let ctx = MiddlewareContext {
            user_id: user_id.clone().unwrap_or_else(new_uuid),
            session_id: session_id.clone().unwrap_or_else(new_uuid),
            request_id: request_id.clone().unwrap_or_else(new_uuid),
            extra: Map::new(),
        };

        // before_run: forward order.
        for mw in &self.middleware {
            inputs = mw.before_run(inputs, &ctx).await?;
        }
        let original_inputs = inputs.clone();

        let mut handle = self.start(
            inputs,
            Some(ctx.user_id.clone()),
            Some(ctx.session_id.clone()),
            Some(ctx.request_id.clone()),
            None,
        )?;

        let collected = match handle.collect(CollectMode::Group, true).await {
            Ok(v) => v,
            Err(e) => {
                // on_error: reverse order.
                let mut err = e;
                for mw in self.middleware.iter().rev() {
                    if let Err(mapped) = mw.on_error(&original_inputs, clone_error(&err), &ctx).await
                    {
                        err = mapped;
                    }
                }
                return Err(err);
            }
        };

        let mut result = match collected {
            Value::Object(m) => m,
            other => {
                let mut m = Map::new();
                m.insert("$collected".into(), other);
                m
            }
        };

        // after_run: reverse order.
        for mw in self.middleware.iter().rev() {
            result = mw.after_run(&original_inputs, result, &ctx).await?;
        }

        Ok(Value::Object(result))
    }
}

// ── OperonBuilder ────────────────────────────────────────────────────────

/// Fluent builder for [`Operon`]. Use when you need explicit control over
/// resource paths, tracers, middleware, or the scheduler impl.
pub struct OperonBuilder {
    graph_json: String,
    resources_path: Option<PathBuf>,
    load_dotenv: bool,
    scheduler: Option<Arc<dyn Scheduler>>,
    tracers: Vec<Arc<dyn Tracer>>,
    middleware: Vec<Arc<dyn Middleware>>,
    install_global_hub: bool,
    /// Custom op registry; defaults to an empty [`InMemoryOpRegistry`] when
    /// unset (ops can be registered after construction via [`OperonBuilder::op`]).
    op_registry: Option<Arc<dyn OpRegistry>>,
    /// Ops registered via [`OperonBuilder::op`] — applied onto the built
    /// registry on `.build()`.
    pending_ops: Vec<PendingOp>,
    /// Whether to skip validating `resources.yaml`'s presence (for tests that
    /// don't need the hub).
    require_resources_file: bool,
    /// When `true`, `.build()` iterates every `#[op]`-submitted
    /// [`OpEntry`](crate::core::registry::OpEntry) via the `inventory` crate
    /// and installs each into the op registry.
    auto_register: bool,
}

struct PendingOp {
    func_name: String,
    func: crate::core::registry::OpFunc,
}

impl OperonBuilder {
    /// Start building from a serialized graph JSON.
    ///
    /// Defaults match Python's pure-orchestrator semantics:
    /// - `load_dotenv: false` — call [`crate::bootstrap`] before constructing
    ///   the engine if you need `.env` loaded.
    /// - `require_resources_file: false` — pure-compute graphs work
    ///   without any `resources.yaml`. Provider ops surface a typed error
    ///   at op resolution if no hub is installed.
    /// - `install_global_hub: true` — kept for backwards compatibility,
    ///   but a no-op when the resolved hub is empty (no clobbering of
    ///   pre-installed hubs from [`crate::bootstrap`]).
    pub fn new(graph_json: &str) -> Self {
        Self {
            graph_json: graph_json.to_string(),
            resources_path: None,
            load_dotenv: false,
            scheduler: None,
            tracers: Vec::new(),
            middleware: Vec::new(),
            install_global_hub: true,
            op_registry: None,
            pending_ops: Vec::new(),
            require_resources_file: false,
            auto_register: false,
        }
    }

    /// Pull every `#[op]`-annotated fn registered via the `inventory` crate
    /// into the built [`OpRegistry`]. Both the short name (e.g. `"double"`)
    /// and the module-qualified name (e.g. `"math::double"`) are registered
    /// so graph JSON can reference either.
    ///
    /// Equivalent to Hush's `HushServer::builder().auto_register()`.
    pub fn auto_register(mut self) -> Self {
        self.auto_register = true;
        self
    }

    /// Override the resources.yaml path. Defaults to `./resources.yaml` in CWD.
    pub fn resources(mut self, path: impl Into<PathBuf>) -> Self {
        self.resources_path = Some(path.into());
        self
    }

    /// Skip dotenv auto-load. Defaults to `true` (loads `./.env` if present).
    pub fn load_dotenv(mut self, enable: bool) -> Self {
        self.load_dotenv = enable;
        self
    }

    /// Provide a custom scheduler. If unset, the built engine uses
    /// [`NotImplementedScheduler`] until Phase 4 lands the real impl.
    pub fn scheduler(mut self, scheduler: Arc<dyn Scheduler>) -> Self {
        self.scheduler = Some(scheduler);
        self
    }

    /// Append a tracer.
    pub fn tracer(mut self, tracer: Arc<dyn Tracer>) -> Self {
        self.tracers.push(tracer);
        self
    }

    /// Append a middleware.
    pub fn middleware(mut self, mw: Arc<dyn Middleware>) -> Self {
        self.middleware.push(mw);
        self
    }

    /// Whether to install the resolved hub as the global singleton.
    /// Defaults to `true` — disable when multiple engines coexist in-process.
    pub fn install_global_hub(mut self, enable: bool) -> Self {
        self.install_global_hub = enable;
        self
    }

    /// Provide a pre-populated op registry (takes precedence over
    /// [`OperonBuilder::op`] registrations).
    pub fn op_registry(mut self, registry: Arc<dyn OpRegistry>) -> Self {
        self.op_registry = Some(registry);
        self
    }

    /// Register a sync op body by its serialized `func_name`. Maps to Python's
    /// `@op`-decorated function name.
    pub fn op<F>(mut self, func_name: impl Into<String>, f: F) -> Self
    where
        F: Fn(Map<String, Value>) -> Result<Value, OperonError> + Send + Sync + 'static,
    {
        let f = std::sync::Arc::new(f);
        let func: crate::core::registry::OpFunc = std::sync::Arc::new(move |inputs| {
            let f = f.clone();
            Box::pin(async move { f(inputs) })
        });
        self.pending_ops.push(PendingOp {
            func_name: func_name.into(),
            func,
        });
        self
    }

    /// Register an async op body by its serialized `func_name`.
    pub fn op_async<F, Fut>(mut self, func_name: impl Into<String>, f: F) -> Self
    where
        F: Fn(Map<String, Value>) -> Fut + Send + Sync + 'static,
        Fut: std::future::Future<Output = Result<Value, OperonError>> + Send + 'static,
    {
        let func: crate::core::registry::OpFunc =
            std::sync::Arc::new(move |inputs| Box::pin(f(inputs)));
        self.pending_ops.push(PendingOp {
            func_name: func_name.into(),
            func,
        });
        self
    }

    /// Skip the `resources.yaml` presence check. The built engine uses an
    /// empty in-memory hub — useful in tests and graphs that don't touch
    /// provider resources.
    pub fn no_resources(mut self) -> Self {
        self.require_resources_file = false;
        self
    }

    /// Materialize the engine.
    pub fn build(self) -> Result<Operon, OperonError> {
        let graph = GraphEnvelope::parse(&self.graph_json)?;
        validate_graph(&graph.config)?;

        if self.load_dotenv {
            load_dotenv_from_cwd();
        }

        // Hub resolution order (mirrors Python):
        // 1. Explicit `resources(path)` wins.
        // 2. Otherwise, reuse a pre-installed singleton if any (do NOT clobber
        //    a hub set up by `crate::bootstrap` or `set_instance`).
        // 3. Otherwise, fall back to an empty hub. Provider ops surface a
        //    typed error at resolution time pointing at `bootstrap()`.
        let hub: Arc<ResourceHub> = if let Some(path) = self.resources_path {
            if !path.exists() {
                return Err(OperonError::Config(format!(
                    "resources.yaml not found at: {}\n\
                     \u{20} Create the file or omit `.resources(...)` to use \
                     the auto-discovered path.",
                    path.display()
                )));
            }
            Arc::new(ResourceHub::from_yaml(&path)?)
        } else if let Ok(existing) = ResourceHub::instance() {
            existing
        } else {
            Arc::new(ResourceHub::empty())
        };
        if self.install_global_hub && ResourceHub::instance().is_err() {
            ResourceHub::set_instance(hub.clone());
        }

        // Build op registry: explicit override wins; otherwise start fresh
        // and apply any `.op(...)` registrations.
        let op_registry: Arc<dyn OpRegistry> = match self.op_registry {
            Some(r) => r,
            None => {
                let reg = InMemoryOpRegistry::new();
                if self.auto_register {
                    install_inventory_ops(&reg);
                }
                for op in &self.pending_ops {
                    reg.register_async(op.func_name.clone(), {
                        let f = op.func.clone();
                        move |inputs| {
                            let f = f.clone();
                            async move { f(inputs).await }
                        }
                    });
                }
                Arc::new(reg)
            }
        };

        let scheduler = match self.scheduler {
            Some(s) => s,
            None => Arc::new(GraphScheduler::new(
                Arc::new(graph.config.clone()),
                op_registry.clone(),
            )?),
        };

        let engine = Operon {
            name: graph.config.name.clone(),
            graph,
            hub,
            op_registry,
            scheduler,
            middleware: self.middleware,
            tracers: self.tracers,
        };
        debug!("Operon engine initialized for workflow {}", engine.name);
        Ok(engine)
    }
}

/// Install every `inventory`-submitted [`OpEntry`] into `reg`.
///
/// Each entry is registered under **both** its bare name (e.g. `"double"`)
/// and its module-qualified name (e.g. `"math::double"`). This lets graph
/// JSON reference whichever form it prefers and mirrors Hush's behaviour.
/// When two entries share a bare name, the first one wins and subsequent
/// collisions are logged — the qualified names remain unique regardless.
fn install_inventory_ops(reg: &InMemoryOpRegistry) {
    use crate::core::registry::OpEntry;
    let mut seen_qualified: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut seen_bare: std::collections::HashSet<String> = std::collections::HashSet::new();
    for entry in inventory::iter::<OpEntry>() {
        let qualified = entry.qualified_name();
        let bare = entry.name.to_string();
        let op_fn = entry.op_fn;

        // Qualified names must be globally unique. A collision means two
        // `#[op]`-annotated fns share both their module path and their name
        // attribute — almost always an accidental copy-paste, never valid.
        // Panic loudly (mirrors Hush's behaviour) instead of silently
        // overwriting — silent overwrite hides real bugs in link order.
        if !seen_qualified.insert(qualified.clone()) {
            panic!(
                "operonx::auto_register: duplicate op '{}' — each #[op] must \
                 have a unique (module_path, name) pair. Remove the duplicate \
                 or disambiguate with `#[op(name = \"...\")]`.",
                qualified
            );
        }

        // Async-wrap the sync fn so it matches the registry's OpFunc shape.
        let make = move || {
            move |inputs: Map<String, Value>| -> Result<Value, OperonError> {
                Ok(op_fn(&Value::Object(inputs)))
            }
        };

        // Qualified name: always registered.
        reg.register_sync(qualified.clone(), make());

        // Bare name: first writer wins; subsequent bare duplicates are
        // addressable only by qualified name.
        if seen_bare.insert(bare.clone()) {
            reg.register_sync(bare, make());
        } else {
            debug!(
                "operonx::auto_register: bare name '{}' ambiguous — reachable only via '{}'",
                entry.name, qualified
            );
        }
    }
}

fn default_resources_path() -> PathBuf {
    std::env::current_dir()
        .map(|p| p.join("resources.yaml"))
        .unwrap_or_else(|_| PathBuf::from("resources.yaml"))
}

fn load_dotenv_from_cwd() {
    let cwd = match std::env::current_dir() {
        Ok(p) => p,
        Err(_) => return,
    };
    let env_path = cwd.join(".env");
    if !env_path.exists() {
        return;
    }
    match dotenvy::from_path_override(&env_path) {
        Ok(()) => debug!(".env loaded from {}", env_path.display()),
        Err(e) => warn!(".env present but failed to load ({}): {}", env_path.display(), e),
    }
}

fn new_uuid() -> String {
    uuid::Uuid::new_v4().to_string()
}

// Keep the `Path` import tidy for future use without cluttering the top.
#[allow(dead_code)]
fn _use_path(_p: &Path) {}

// ── Tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use futures::StreamExt;

    fn sample_graph(name: &str) -> String {
        format!(
            r#"{{
                "schema_version": "1.0",
                "type": "graph",
                "name": "{}",
                "full_name": "{}",
                "ops": {{}},
                "edges": [],
                "entries": ["__START__"],
                "exits": ["__START__"]
            }}"#,
            name, name
        )
    }

    #[test]
    fn parses_graph_envelope() {
        // Graph doesn't need to pass validate_graph for this test — we call
        // parse() directly rather than going through build().
        let raw = format!(
            r#"{{"schema_version": "1.0", "type": "graph", "name": "{}", "full_name": "{}"}}"#,
            "test", "test"
        );
        let env = GraphEnvelope::parse(&raw).unwrap();
        assert_eq!(env.name(), "test");
        assert_eq!(env.schema_version, "1.0");
    }

    #[test]
    fn rejects_wrong_schema_version() {
        let bad = r#"{"schema_version": "9.9", "name": "x"}"#;
        let err = GraphEnvelope::parse(bad).unwrap_err();
        assert!(matches!(err, OperonError::UnsupportedSchema(_)));
    }

    #[tokio::test]
    async fn handle_streams_then_ends() {
        let cancel = CancellationToken::new();
        let (mut handle, sender) = ExecutionHandle::new(cancel, MiddlewareContext::default());

        // Feed two frames, then finish.
        let s = sender.clone();
        tokio::spawn(async move {
            let mut d1 = Map::new();
            d1.insert("result".into(), Value::from(1));
            s.send(FrameEvent {
                op: "a".into(),
                context: crate::core::states::cell::default_context(),
                data: d1,
            })
            .await
            .unwrap();
            let mut d2 = Map::new();
            d2.insert("result".into(), Value::from(2));
            s.send(FrameEvent {
                op: "a".into(),
                context: crate::core::states::cell::default_context(),
                data: d2,
            })
            .await
            .unwrap();
            s.finish().await;
        });

        let f1 = handle.next().await.unwrap().unwrap();
        assert_eq!(f1.data.get("result"), Some(&Value::from(1)));
        let f2 = handle.next().await.unwrap().unwrap();
        assert_eq!(f2.data.get("result"), Some(&Value::from(2)));
        assert!(handle.next().await.is_none());
        assert!(handle.is_done());
        assert_eq!(handle.frame_count(), 2);
    }

    #[tokio::test]
    async fn wait_for_resolves_after_frame() {
        let (handle, sender) = ExecutionHandle::new(
            CancellationToken::new(),
            MiddlewareContext::default(),
        );
        let s = sender.clone();
        tokio::spawn(async move {
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            let mut d = Map::new();
            d.insert("answer".into(), Value::from("hi"));
            s.send(FrameEvent {
                op: "llm".into(),
                context: crate::core::states::cell::default_context(),
                data: d,
            })
            .await
            .unwrap();
            s.finish().await;
        });
        let v = handle.wait_for("llm", "answer").await.unwrap();
        assert_eq!(v, Some(Value::from("hi")));
    }

    #[tokio::test]
    async fn collect_group_merges_by_key() {
        let (mut handle, sender) = ExecutionHandle::new(
            CancellationToken::new(),
            MiddlewareContext::default(),
        );
        let s = sender.clone();
        tokio::spawn(async move {
            for i in 0..3 {
                let mut d = Map::new();
                d.insert("x".into(), Value::from(i));
                s.send(FrameEvent {
                    op: "gen".into(),
                    context: crate::core::states::cell::default_context(),
                    data: d,
                })
                .await
                .unwrap();
            }
            s.finish().await;
        });
        let out = ExecutionHandle::collect(&mut handle, CollectMode::Group, false)
            .await
            .unwrap();
        let obj = out.as_object().unwrap();
        let xs = obj.get("x").unwrap().as_array().unwrap();
        assert_eq!(xs.len(), 3);
    }

    #[tokio::test]
    async fn cancel_terminates_stream() {
        let (mut handle, _sender) = ExecutionHandle::new(
            CancellationToken::new(),
            MiddlewareContext::default(),
        );
        handle.cancel();
        assert!(handle.next().await.is_none());
        assert!(handle.is_done());
    }

    #[tokio::test]
    async fn scheduler_stub_returns_error_via_handle() {
        let hub = Arc::new(ResourceHub::empty());
        let envelope = GraphEnvelope::parse(&sample_graph("stub")).unwrap();
        let engine = Operon {
            graph: envelope,
            name: "stub".into(),
            hub,
            op_registry: Arc::new(InMemoryOpRegistry::new()),
            scheduler: Arc::new(NotImplementedScheduler),
            middleware: Vec::new(),
            tracers: Vec::new(),
        };
        let mut handle = engine
            .start(Map::new(), None, None, None, None)
            .unwrap();
        let first = handle.next().await;
        assert!(first.is_some());
        let err = first.unwrap().unwrap_err();
        assert!(matches!(err, OperonError::Runtime(_)));
    }

    /// Phase 4 end-to-end smoke test — `@op double(x=5)` serialized to JSON,
    /// dispatched through the engine, returns `{"result": 10}`.
    ///
    /// The JSON mirrors what Python's `Operon.export_config()` emits for a
    /// single-op graph with a PARENT input and a PARENT-bound output.
    #[tokio::test]
    async fn end_to_end_double_op_runs_to_completion() {
        let graph_json = r#"{
            "schema_version": "1.0",
            "type": "graph",
            "name": "main",
            "full_name": "main",
            "entries": ["double"],
            "exits": ["double"],
            "initial_ready_count": {"double": 0},
            "compiled_adj": {"double": []},
            "inputs": {"x": {"required": true}},
            "outputs": {"result": {}},
            "ops": {
                "double": {
                    "type": "code",
                    "name": "double",
                    "full_name": "main.double",
                    "func_name": "double",
                    "is_async": false,
                    "is_generator": false,
                    "bound": "sync",
                    "inputs": {
                        "x": {
                            "required": true,
                            "ref": {
                                "source": "__PARENT__",
                                "var": "x"
                            }
                        }
                    },
                    "outputs": {
                        "result": {
                            "ref": {
                                "source": "__PARENT__",
                                "var": "result",
                                "is_output": true
                            }
                        }
                    }
                }
            }
        }"#;

        let engine = Operon::builder(graph_json)
            .no_resources()
            .install_global_hub(false)
            .op("double", |inputs| {
                let x = inputs.get("x").and_then(|v| v.as_i64()).unwrap_or(0);
                Ok(serde_json::json!({"result": x * 2}))
            })
            .build()
            .unwrap();

        let mut inputs = Map::new();
        inputs.insert("x".into(), Value::from(5));

        let out = engine.run_json_async(inputs, None, None, None).await.unwrap();
        let obj = out.as_object().unwrap();
        assert_eq!(obj.get("result"), Some(&Value::from(10)));
    }

    /// Two-op chain: `double(x=PARENT["x"]) -> add_one(n=double["result"])`.
    /// Verifies hard-edge ready-count dispatch and op-to-op ref resolution.
    #[tokio::test]
    async fn end_to_end_two_op_chain() {
        let graph_json = r#"{
            "schema_version": "1.0",
            "type": "graph",
            "name": "main",
            "full_name": "main",
            "entries": ["double"],
            "exits": ["add_one"],
            "initial_ready_count": {"double": 0, "add_one": 1},
            "compiled_adj": {
                "double":  [["add_one", false]],
                "add_one": []
            },
            "inputs": {"x": {"required": true}},
            "outputs": {"answer": {}},
            "ops": {
                "double": {
                    "type": "code",
                    "name": "double",
                    "full_name": "main.double",
                    "func_name": "double",
                    "bound": "sync",
                    "inputs": {
                        "x": {
                            "required": true,
                            "ref": {"source": "__PARENT__", "var": "x"}
                        }
                    },
                    "outputs": {
                        "result": {}
                    }
                },
                "add_one": {
                    "type": "code",
                    "name": "add_one",
                    "full_name": "main.add_one",
                    "func_name": "add_one",
                    "bound": "sync",
                    "inputs": {
                        "n": {
                            "required": true,
                            "ref": {"source": "main.double", "var": "result"}
                        }
                    },
                    "outputs": {
                        "answer": {
                            "ref": {
                                "source": "__PARENT__",
                                "var": "answer",
                                "is_output": true
                            }
                        }
                    }
                }
            }
        }"#;

        let engine = Operon::builder(graph_json)
            .no_resources()
            .install_global_hub(false)
            .op("double", |inputs| {
                let x = inputs.get("x").and_then(|v| v.as_i64()).unwrap_or(0);
                Ok(serde_json::json!({"result": x * 2}))
            })
            .op("add_one", |inputs| {
                let n = inputs.get("n").and_then(|v| v.as_i64()).unwrap_or(0);
                Ok(serde_json::json!({"answer": n + 1}))
            })
            .build()
            .unwrap();

        let mut inputs = Map::new();
        inputs.insert("x".into(), Value::from(5));

        let out = engine.run_json_async(inputs, None, None, None).await.unwrap();
        let obj = out.as_object().unwrap();
        // double(5) = 10, add_one(10) = 11
        assert_eq!(obj.get("answer"), Some(&Value::from(11)));
    }

    /// Phase 6 smoke test — `PromptOp` renders a template without needing
    /// any ResourceHub entries. Verifies the provider-op dispatch path from
    /// the scheduler into [`providers::ops::factory`].
    #[tokio::test]
    async fn end_to_end_prompt_op_renders_messages() {
        let graph_json = r#"{
            "schema_version": "1.0",
            "type": "graph",
            "name": "main",
            "full_name": "main",
            "entries": ["greet"],
            "exits": ["greet"],
            "initial_ready_count": {"greet": 0},
            "compiled_adj": {"greet": []},
            "inputs": {"name": {"required": true}},
            "outputs": {"messages": {}},
            "ops": {
                "greet": {
                    "type": "prompt",
                    "name": "greet",
                    "full_name": "main.greet",
                    "bound": "sync",
                    "inputs": {
                        "template": {
                            "literal": {"system": "You are friendly.", "user": "Hello {name}!"}
                        },
                        "name": {
                            "required": true,
                            "ref": {"source": "__PARENT__", "var": "name"}
                        }
                    },
                    "outputs": {
                        "messages": {
                            "ref": {
                                "source": "__PARENT__",
                                "var": "messages",
                                "is_output": true
                            }
                        }
                    }
                }
            }
        }"#;

        let engine = Operon::builder(graph_json)
            .no_resources()
            .install_global_hub(false)
            .build()
            .unwrap();

        let mut inputs = Map::new();
        inputs.insert("name".into(), Value::from("world"));

        let out = engine.run_json_async(inputs, None, None, None).await.unwrap();
        let msgs = out
            .get("messages")
            .expect("messages present")
            .as_array()
            .expect("messages is array");
        assert_eq!(msgs.len(), 2);
        assert_eq!(msgs[0]["role"], "system");
        assert_eq!(msgs[0]["content"], "You are friendly.");
        assert_eq!(msgs[1]["role"], "user");
        assert_eq!(msgs[1]["content"], "Hello world!");
    }

    /// Phase 7 smoke test — registers a [`LocalTracer`] and verifies the
    /// engine collects + writes `{request_id}.json` on completion.
    #[tokio::test]
    async fn end_to_end_local_tracer_receives_trace() {
        use crate::core::tracing::{LocalTracer, TraceData};
        use std::fs;

        // Isolated trace dir per test.
        let tmp = std::env::temp_dir()
            .join(format!("operonx-trace-smoke-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        let request_id = "trace-smoke-1".to_string();
        let graph_json = r#"{
            "schema_version": "1.0",
            "type": "graph",
            "name": "main",
            "full_name": "main",
            "entries": ["double"],
            "exits": ["double"],
            "initial_ready_count": {"double": 0},
            "compiled_adj": {"double": []},
            "inputs": {"x": {"required": true}},
            "outputs": {"result": {}},
            "ops": {
                "double": {
                    "type": "code",
                    "name": "double",
                    "full_name": "main.double",
                    "func_name": "double",
                    "bound": "sync",
                    "inputs": {
                        "x": {
                            "required": true,
                            "ref": {"source": "__PARENT__", "var": "x"}
                        }
                    },
                    "outputs": {
                        "result": {
                            "ref": {
                                "source": "__PARENT__",
                                "var": "result",
                                "is_output": true
                            }
                        }
                    }
                }
            }
        }"#;

        let engine = Operon::builder(graph_json)
            .no_resources()
            .install_global_hub(false)
            .tracer(Arc::new(LocalTracer::new(Some(tmp.clone()), vec![])))
            .op("double", |inputs| {
                let x = inputs.get("x").and_then(|v| v.as_i64()).unwrap_or(0);
                Ok(serde_json::json!({"result": x * 2}))
            })
            .build()
            .unwrap();

        let mut inputs = Map::new();
        inputs.insert("x".into(), Value::from(7));
        let _ = engine
            .run_json_async(inputs, None, None, Some(request_id.clone()))
            .await
            .unwrap();

        // The scheduler task spawns a fire-and-forget blocking flush — poll
        // briefly for the file to appear.
        let path = tmp.join(format!("{}.json", request_id));
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while !path.exists() {
            if std::time::Instant::now() > deadline {
                panic!("trace file did not appear at {}", path.display());
            }
            tokio::time::sleep(std::time::Duration::from_millis(25)).await;
        }
        let body: TraceData = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(body.request_id, request_id);
        assert_eq!(body.workflow_name, "main");
        // Root trace node + at least one span for `double`.
        assert!(body.nodes.len() >= 2);
        assert_eq!(body.nodes[0].node_type, "trace");
        // Frames carry the short op-key (matches Python's
        // `Frame(op_name=…)` where `op_name` is the dict key in
        // `graph._ops`). The full name lives on each op's `OpConfig.full_name`.
        assert!(body
            .nodes
            .iter()
            .any(|n| n.op_name.as_deref() == Some("double")));

        let _ = fs::remove_dir_all(&tmp);
    }
}
