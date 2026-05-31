//! `EventEmitter` — what ops + scheduler call to record trace events.
//!
//! Mirrors Python [`operonx/core/tracing/emitter.py`](../../../../../operonx/core/tracing/emitter.py).
//! All emit methods are sync + O(1) per execution-model rule §3.7 — events
//! land on the bound pipeline's buffer immediately and the actual processor
//! / exporter work happens off the hot path inside `pipeline.flush()`.
//!
//! ## Thread-local current op (Rust analogue of Python's ContextVar)
//!
//! Python uses two `ContextVar`s (`_current_emitter_var`, `_current_op_var`)
//! that propagate across `asyncio.create_task`. The Rust runtime uses
//! `tokio::task_local!` macros for the same shape — tasks spawned with
//! `LocalKey::scope` inherit the binding; tasks spawned outside don't, which
//! mirrors Python's `asyncio.create_task` semantics.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;

use chrono::Utc;
use parking_lot::Mutex;
use serde_json::Value;

use super::events::{EventCtx, EventKind, TraceEvent};
use super::pipeline::TracePipeline;

tokio::task_local! {
    /// Per-task binding of `(op_name, ctx)` for the op currently executing.
    /// Set by the scheduler around each op invocation; read by
    /// `EventEmitter::annotate` to scope user-attached metadata.
    pub static CURRENT_OP: (String, EventCtx);
}

/// Sync, fast emit surface. Owns a per-call seq counter + a
/// `(op_name, ctx) -> start_time` map for cancel-emit duration synthesis
/// (Rule 3). Clone-cheap — `Arc<Inner>`.
#[derive(Clone)]
pub struct EventEmitter {
    inner: Arc<EmitterInner>,
}

struct EmitterInner {
    pipeline: Option<Arc<TracePipeline>>,
    request_id: String,
    seq: Mutex<u64>,
    start_times: Mutex<HashMap<(String, EventCtx), Instant>>,
}

impl EventEmitter {
    /// Bind to a pipeline for the duration of one `engine.start()` call.
    pub fn new(pipeline: Arc<TracePipeline>, request_id: impl Into<String>) -> Self {
        Self {
            inner: Arc::new(EmitterInner {
                pipeline: Some(pipeline),
                request_id: request_id.into(),
                seq: Mutex::new(0),
                start_times: Mutex::new(HashMap::new()),
            }),
        }
    }

    /// A no-op emitter — every method is a cheap fast-path. Used when no
    /// tracer is wired so call sites don't need null checks.
    pub fn null() -> Self {
        Self {
            inner: Arc::new(EmitterInner {
                pipeline: None,
                request_id: String::new(),
                seq: Mutex::new(0),
                start_times: Mutex::new(HashMap::new()),
            }),
        }
    }

    /// `true` when this emitter is bound to a real pipeline. Used by callers
    /// that want to skip expensive payload assembly when nothing's listening.
    pub fn is_active(&self) -> bool {
        self.inner.pipeline.is_some()
    }

    /// Push an event onto the pipeline's buffer.
    pub fn emit(&self, event: TraceEvent) {
        if let Some(p) = &self.inner.pipeline {
            p.push(event);
        }
    }

    pub fn op_start(&self, op_name: &str, ctx: &EventCtx, inputs: Value) {
        if !self.is_active() {
            return;
        }
        self.inner
            .start_times
            .lock()
            .insert((op_name.to_string(), ctx.clone()), Instant::now());
        let event = self.build(EventKind::OpStart, Some(op_name), ctx, payload_one("inputs", inputs));
        self.emit(event);
    }

    pub fn op_end(
        &self,
        op_name: &str,
        ctx: &EventCtx,
        outputs: Value,
        status: &str,
        duration_ms: Option<f64>,
        yield_count: u32,
    ) {
        if !self.is_active() {
            return;
        }
        let key = (op_name.to_string(), ctx.clone());
        // Idempotent — second call (e.g. cancel-emit racing finally) is a no-op.
        let start = self.inner.start_times.lock().remove(&key);
        let duration = duration_ms.unwrap_or_else(|| match start {
            Some(t) => t.elapsed().as_secs_f64() * 1000.0,
            None => return_no_op(0.0),
        });
        if start.is_none() && duration_ms.is_none() {
            return;
        }
        let mut payload = serde_json::Map::new();
        payload.insert("outputs".into(), outputs);
        payload.insert("status".into(), Value::String(status.to_string()));
        payload.insert(
            "duration_ms".into(),
            serde_json::Number::from_f64(duration)
                .map(Value::Number)
                .unwrap_or(Value::Null),
        );
        payload.insert(
            "yield_count".into(),
            Value::Number(serde_json::Number::from(yield_count)),
        );
        let event = self.build(EventKind::OpEnd, Some(op_name), ctx, into_btree(payload));
        self.emit(event);
    }

    pub fn op_yield(&self, op_name: &str, ctx: &EventCtx, yielded: Value, idx: u64) {
        if !self.is_active() {
            return;
        }
        let mut payload = serde_json::Map::new();
        payload.insert("yielded".into(), yielded);
        payload.insert(
            "idx".into(),
            Value::Number(serde_json::Number::from(idx)),
        );
        let event = self.build(EventKind::OpYield, Some(op_name), ctx, into_btree(payload));
        self.emit(event);
    }

    /// Attach metadata to the currently executing op. Reads the
    /// `CURRENT_OP` task-local. Silently drops the event when called outside
    /// an op scope — mirrors Python's LookupError-as-programming-error
    /// pattern but doesn't panic the runtime.
    pub fn annotate(&self, key: &str, value: Value) {
        if !self.is_active() {
            return;
        }
        let (op_name, ctx) = match CURRENT_OP.try_with(|cur| cur.clone()) {
            Ok(v) => v,
            Err(_) => return,
        };
        let mut payload = serde_json::Map::new();
        payload.insert("key".into(), Value::String(key.to_string()));
        payload.insert("value".into(), value);
        let event = self.build(EventKind::Annotation, Some(&op_name), &ctx, into_btree(payload));
        self.emit(event);
    }

    pub fn llm_usage(
        &self,
        op_name: &str,
        ctx: &EventCtx,
        model: &str,
        prompt_tokens: u64,
        completion_tokens: u64,
        total_tokens: u64,
        cost_usd: f64,
    ) {
        if !self.is_active() {
            return;
        }
        let mut payload = serde_json::Map::new();
        payload.insert("model".into(), Value::String(model.to_string()));
        payload.insert("prompt_tokens".into(), Value::Number(prompt_tokens.into()));
        payload.insert("completion_tokens".into(), Value::Number(completion_tokens.into()));
        payload.insert("total_tokens".into(), Value::Number(total_tokens.into()));
        payload.insert(
            "cost_usd".into(),
            serde_json::Number::from_f64(cost_usd)
                .map(Value::Number)
                .unwrap_or(Value::Null),
        );
        let event = self.build(EventKind::LlmUsage, Some(op_name), ctx, into_btree(payload));
        self.emit(event);
    }

    pub fn media_ref(
        &self,
        op_name: &str,
        ctx: &EventCtx,
        handle: &str,
        mime: &str,
        size_bytes: u64,
    ) {
        if !self.is_active() {
            return;
        }
        let mut payload = serde_json::Map::new();
        payload.insert("handle".into(), Value::String(handle.to_string()));
        payload.insert("mime".into(), Value::String(mime.to_string()));
        payload.insert("size_bytes".into(), Value::Number(size_bytes.into()));
        let event = self.build(EventKind::MediaRef, Some(op_name), ctx, into_btree(payload));
        self.emit(event);
    }

    /// Open a manual group scope. Emits `GroupStart` immediately and returns
    /// a guard whose `Drop` emits `GroupEnd`. Most callers won't need this —
    /// the `GroupBy` processor synthesises boundaries from the event stream.
    pub fn group<'a>(&'a self, name: &str) -> GroupGuard<'a> {
        if self.is_active() {
            let mut payload = serde_json::Map::new();
            payload.insert("name".into(), Value::String(name.to_string()));
            let event = self.build(EventKind::GroupStart, None, &Vec::new(), into_btree(payload));
            self.emit(event);
        }
        GroupGuard {
            emitter: self,
            name: name.to_string(),
        }
    }

    /// `perf_counter`-equivalent for the op's OP_START, or `None` when
    /// unknown (cancel-before-start). Surfaces a duration_ms the scheduler
    /// can pass into `op_end` on cancel emission (Rule 3).
    pub fn start_time_of(&self, op_name: &str, ctx: &EventCtx) -> Option<Instant> {
        self.inner
            .start_times
            .lock()
            .get(&(op_name.to_string(), ctx.clone()))
            .copied()
    }

    fn build(
        &self,
        kind: EventKind,
        op_name: Option<&str>,
        ctx: &EventCtx,
        payload: std::collections::BTreeMap<String, Value>,
    ) -> TraceEvent {
        let mut s = self.inner.seq.lock();
        let seq = *s;
        *s = seq + 1;
        drop(s);
        TraceEvent {
            event_id: format!("{}-{}", self.inner.request_id, seq),
            request_id: self.inner.request_id.clone(),
            kind,
            op_name: op_name.map(String::from),
            ctx: ctx.clone(),
            timestamp: Utc::now(),
            seq,
            payload,
        }
    }
}

/// Returned by `EventEmitter::group`. Emits `GroupEnd` on drop.
pub struct GroupGuard<'a> {
    emitter: &'a EventEmitter,
    name: String,
}

impl Drop for GroupGuard<'_> {
    fn drop(&mut self) {
        if !self.emitter.is_active() {
            return;
        }
        let mut payload = serde_json::Map::new();
        payload.insert("name".into(), Value::String(self.name.clone()));
        payload.insert("status".into(), Value::String("ok".into()));
        let event = self.emitter.build(EventKind::GroupEnd, None, &Vec::new(), into_btree(payload));
        self.emitter.emit(event);
    }
}

fn payload_one(key: &str, value: Value) -> std::collections::BTreeMap<String, Value> {
    let mut m = std::collections::BTreeMap::new();
    m.insert(key.to_string(), value);
    m
}

fn into_btree(m: serde_json::Map<String, Value>) -> std::collections::BTreeMap<String, Value> {
    let mut out = std::collections::BTreeMap::new();
    for (k, v) in m {
        out.insert(k, v);
    }
    out
}

/// `unwrap_or_else` companion — returns the constant unused, used to mark
/// the branch as a no-op for clarity.
fn return_no_op<T>(t: T) -> T {
    t
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::tracing::pipeline::TracePipeline;

    #[test]
    fn null_emitter_is_inactive_and_silent() {
        let e = EventEmitter::null();
        assert!(!e.is_active());
        e.op_start("op", &vec!["main".into()], serde_json::json!({}));
        e.op_end("op", &vec!["main".into()], serde_json::json!({}), "ok", None, 0);
    }

    #[test]
    fn op_start_op_end_round_trip_records_duration() {
        let pipeline = Arc::new(TracePipeline::new());
        let emitter = EventEmitter::new(pipeline.clone(), "req-1");
        emitter.op_start("op", &vec!["main".into()], serde_json::json!({"x": 1}));
        std::thread::sleep(std::time::Duration::from_millis(2));
        emitter.op_end(
            "op",
            &vec!["main".into()],
            serde_json::json!({"y": 2}),
            "ok",
            None,
            0,
        );
        let events = pipeline.drain();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].kind, EventKind::OpStart);
        assert_eq!(events[1].kind, EventKind::OpEnd);
        let duration = events[1]
            .payload
            .get("duration_ms")
            .and_then(|v| v.as_f64())
            .expect("duration_ms set");
        assert!(duration >= 1.0, "duration_ms = {duration}");
    }

    #[test]
    fn op_end_idempotent_when_called_twice() {
        let pipeline = Arc::new(TracePipeline::new());
        let emitter = EventEmitter::new(pipeline.clone(), "req-2");
        emitter.op_start("op", &vec!["main".into()], serde_json::json!({}));
        emitter.op_end("op", &vec!["main".into()], serde_json::json!({}), "ok", None, 0);
        emitter.op_end("op", &vec!["main".into()], serde_json::json!({}), "ok", None, 0);
        let events = pipeline.drain();
        // 1 OpStart + 1 OpEnd; the second OpEnd was silently dropped.
        assert_eq!(events.len(), 2);
    }

    #[test]
    fn group_guard_emits_start_and_end_on_drop() {
        let pipeline = Arc::new(TracePipeline::new());
        let emitter = EventEmitter::new(pipeline.clone(), "req-3");
        {
            let _g = emitter.group("preflight");
        }
        let events = pipeline.drain();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].kind, EventKind::GroupStart);
        assert_eq!(events[1].kind, EventKind::GroupEnd);
    }

    #[tokio::test]
    async fn annotate_reads_current_op_task_local() {
        let pipeline = Arc::new(TracePipeline::new());
        let emitter = EventEmitter::new(pipeline.clone(), "req-4");
        let scope_op = ("the_op".to_string(), vec!["main".into()]);
        CURRENT_OP
            .scope(scope_op, async {
                emitter.annotate("user_id", serde_json::json!("u-1"));
            })
            .await;
        let events = pipeline.drain();
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, EventKind::Annotation);
        assert_eq!(events[0].op_name.as_deref(), Some("the_op"));
    }
}
