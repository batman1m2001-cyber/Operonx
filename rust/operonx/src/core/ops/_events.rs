//! Scheduler event types — `Frame`, `Interrupt`, `EOF`.
//!
//! Mirrors Python [`operonx/core/ops/_events.py`](../../../../../operonx/core/ops/_events.py).
//! These are the **public, typed** wire shapes user code (`#[op]` bodies) returns
//! and that consumers read off `ExecutionHandle`. Internal scheduler events
//! (`SchedulerEvent::Frame { ... }` etc.) live next to the scheduler — these
//! types here are what crosses the API boundary.
//!
//! The most important one is `Interrupt`: an `#[op]` body returns
//! `Interrupt { ctx_to_cancel: [...], reason: "..." }.into()` to in-band cancel
//! queued frames + in-flight tasks at the target context tree. The scheduler
//! recognises the canonical `{"__interrupt__": {...}}` JSON shape and turns
//! it into a `SchedulerEvent::Interrupt`, which is then forwarded to the
//! handle as a synthetic frame on the public channel.
//!
//! See `core::engine::ExecutionHandle::{interrupts, scratch}` for the
//! reader side.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

/// Context tuple shape, mirroring `crate::core::states::cell::ContextId`.
///
/// Imported separately so this module stays low-level (no scheduler dep).
pub type ContextTuple = Vec<String>;

/// Wire-format key the scheduler looks for to recognise an Interrupt return
/// value. Public so `parse_interrupt` in the scheduler can reference it from
/// one place instead of hard-coding the string.
pub const INTERRUPT_KEY: &str = "__interrupt__";

/// In-band scheduler cancellation event.
///
/// Returned from a user op body to cancel queued frames + in-flight tasks
/// at `ctx_to_cancel` (and its descendants). `op` and `ctx` record the
/// emitter for tracing; `ctx_to_cancel` is the explicit target — typically
/// the prior turn's ctx, stored in `SCRATCH` when long-running work began.
///
/// The scheduler:
///   1. Drops Frame/EOF items at descendants of `ctx_to_cancel` from the queue.
///   2. Cancels in-flight tasks at descendants of `ctx_to_cancel` (skipping
///      the emitter to avoid self-cancel).
///   3. Forwards a synthetic `("__interrupt__", emitter_ctx, {...})` frame
///      to the public sender so `ExecutionHandle` consumers see it.
///
/// **Best-effort:** data already pushed to consumer-owned queues (e.g. a
/// user `tokio::sync::mpsc`) is NOT drained — consumer handles that itself.
///
/// # Returning from `#[op]`
///
/// ```ignore
/// use operonx::{op, Interrupt};
/// use serde_json::Value;
///
/// #[op(name = "cancel_turn")]
/// fn cancel_turn(_inputs: serde_json::Map<String, Value>) -> Value {
///     Interrupt {
///         ctx_to_cancel: vec!["main".into(), "turn_1".into()],
///         reason: "user spoke again".into(),
///         ..Default::default()
///     }
///     .into()
/// }
/// ```
///
/// # Reading from the handle
///
/// ```ignore
/// let interrupts = handle.interrupts();
/// for irq in interrupts {
///     println!("interrupted at {:?}: {}", irq.ctx_to_cancel, irq.reason);
/// }
/// ```
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct Interrupt {
    /// The op that emitted the Interrupt. Filled by the scheduler when the
    /// synthetic frame is built; user code may leave this empty.
    #[serde(default)]
    pub op: String,

    /// The context the emitter ran in. Same source-of-truth as `op` — set
    /// by the scheduler.
    #[serde(default)]
    pub ctx: ContextTuple,

    /// The target context tree to cancel. Set by the user op body.
    pub ctx_to_cancel: ContextTuple,

    /// Free-form reason string. Surfaces in tracing + diagnostics.
    #[serde(default)]
    pub reason: String,
}

impl Interrupt {
    /// Convenience constructor for the common case: target ctx + reason.
    pub fn new(ctx_to_cancel: impl Into<ContextTuple>, reason: impl Into<String>) -> Self {
        Self {
            op: String::new(),
            ctx: Vec::new(),
            ctx_to_cancel: ctx_to_cancel.into(),
            reason: reason.into(),
        }
    }

    /// Produce the canonical wire-format `Value` an op body returns to
    /// trigger an Interrupt: `{"__interrupt__": {<this Interrupt as JSON>}}`.
    pub fn into_frame_value(self) -> Value {
        let inner = serde_json::to_value(&self).unwrap_or(Value::Null);
        let mut wrapper = Map::new();
        wrapper.insert(INTERRUPT_KEY.into(), inner);
        Value::Object(wrapper)
    }

    /// Parse the canonical wire shape back into an `Interrupt`. Returns
    /// `None` if `value` doesn't have the `{"__interrupt__": {...}}` shape.
    ///
    /// Used by `ExecutionHandle::interrupts()` to walk buffered frames.
    pub fn from_frame_value(value: &Value) -> Option<Self> {
        let inner = value.as_object()?.get(INTERRUPT_KEY)?;
        serde_json::from_value(inner.clone()).ok()
    }
}

impl From<Interrupt> for Value {
    fn from(i: Interrupt) -> Self {
        i.into_frame_value()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn interrupt_round_trips_through_canonical_json() {
        let irq = Interrupt::new(vec!["main".to_string(), "turn_1".into()], "user spoke");
        let v: Value = irq.clone().into();
        assert_eq!(
            v,
            json!({
                "__interrupt__": {
                    "op": "",
                    "ctx": [],
                    "ctx_to_cancel": ["main", "turn_1"],
                    "reason": "user spoke"
                }
            })
        );
        let parsed = Interrupt::from_frame_value(&v).expect("parse round-trip");
        assert_eq!(parsed, irq);
    }

    #[test]
    fn from_frame_value_returns_none_on_non_interrupt_shapes() {
        assert!(Interrupt::from_frame_value(&json!({"other": 1})).is_none());
        assert!(Interrupt::from_frame_value(&json!("not an object")).is_none());
        assert!(Interrupt::from_frame_value(&json!({"__interrupt__": "not a map"})).is_none());
    }

    #[test]
    fn from_frame_value_handles_minimal_shape() {
        let v = json!({"__interrupt__": {"ctx_to_cancel": ["x"]}});
        let irq = Interrupt::from_frame_value(&v).expect("parse minimal");
        assert_eq!(irq.ctx_to_cancel, vec!["x".to_string()]);
        assert_eq!(irq.reason, "");
        assert_eq!(irq.op, "");
    }
}
