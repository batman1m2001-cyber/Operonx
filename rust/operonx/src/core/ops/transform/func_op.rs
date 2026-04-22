//! `FuncOp` — dispatch a Rust function registered via `#[op]`.
//!
//! Mirrors Python [`operon/core/ops/transform/func_op.py`](../../../../../operon/core/ops/transform/func_op.py).
//! On the Python side `FuncOp` wraps a Python callable. On Rust, the registered
//! function is looked up by `func_name` in the global `inventory` registry and
//! invoked.
//!
//! # Phase 1 scope
//! Struct + trait skeleton. The `inventory` dispatch wiring lands in Phase 6
//! alongside the `#[op]` macro's final implementation.
//!
//! # Per §4b.11
//! Python's `FuncOp.serialize()` emits `is_async` and `is_generator` flags —
//! the Rust dispatcher uses these to pick between sync call / `tokio::spawn` /
//! `GeneratorOp` dispatch.

use async_trait::async_trait;
use serde_json::{Map, Value};

use crate::core::exceptions::{OpError, OperonError};
use crate::core::ops::base::{BaseOp, OpContext, OpMeta};

/// FuncOp — user-registered Rust function, dispatched by name.
pub struct FuncOp {
    pub meta: OpMeta,

    /// Fully qualified Python name (e.g., `"my_ops.double"`) — the key used
    /// to look up the matching `#[op(name = "...")]` Rust fn in the inventory.
    pub func_name: String,

    /// Python `is_async` flag. Rust dispatcher routes async fns via `tokio::spawn`.
    pub is_async: bool,

    /// Python `is_generator` flag. Rust dispatcher routes generator fns to the
    /// `GeneratorOp` pathway (yields multiple Values → stream contexts).
    pub is_generator: bool,
}

#[async_trait]
impl BaseOp for FuncOp {
    fn meta(&self) -> &OpMeta {
        &self.meta
    }

    async fn exec_core(
        &self,
        _inputs: Map<String, Value>,
        _ctx: &OpContext<'_>,
    ) -> Result<Option<Value>, OperonError> {
        // Phase 6 will implement: look up `self.func_name` in the `#[op]` registry
        // (built on `inventory`), deserialize inputs into the fn's typed signature
        // (via the `#[op]` wrapper), invoke, serialize return → Value.
        Err(OperonError::Op(OpError::Code(format!(
            "FuncOp::exec_core not yet implemented (phase 1 scaffold for func_name={:?})",
            self.func_name
        ))))
    }
}
