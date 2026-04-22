//! `BaseOp` trait + `OpMeta` — the core op abstraction.
//!
//! Mirrors Python [`operon/core/ops/base.py`](../../../../operon/core/ops/base.py).
//! Each concrete op (FuncOp, BranchOp, ParserOp, LLMOp, etc.) is a Rust struct
//! that holds an [`OpMeta`] as its first field and implements [`BaseOp`]. Shared
//! behavior (input resolution, caching, timing, state writes) lives in the
//! default `run()` harness — see MIGRATION_rust.md §3a.2.
//!
//! # Phase 1 scope
//! Type definitions only. The `run()` harness (timing, cache check, ref
//! resolution, push-refs, middleware) is implemented as part of Phase 3/4
//! when the scheduler is wired up.

use std::collections::HashMap;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use super::cache::CacheConfig;
use crate::core::configs::op_config::{OpBound, OpType};
use crate::core::exceptions::OperonError;
use crate::core::states::cell::ContextId;
use crate::core::states::state::MemoryState;
use crate::core::utils::common::Param;

/// Shared metadata every op carries — populated from the op's serialized config.
///
/// Python fields (`name`, `full_name`, `type`, `enabled`, `verbose`, `stream`,
/// `bound`, `inputs`, `outputs`, `cache`, `delay`) land here. Python's `type:`
/// field becomes `kind:` in Rust to avoid the reserved-word clash
/// (see MIGRATION_rust.md §3a.7).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OpMeta {
    pub name: String,

    pub full_name: String,

    #[serde(rename = "type")]
    pub kind: OpType,

    #[serde(default = "default_true")]
    pub enabled: bool,

    #[serde(default)]
    pub verbose: bool,

    #[serde(default)]
    pub stream: bool,

    #[serde(default)]
    pub bound: OpBound,

    #[serde(default)]
    pub inputs: HashMap<String, Param>,

    #[serde(default)]
    pub outputs: HashMap<String, Param>,

    #[serde(default)]
    pub cache: Option<CacheConfig>,

    #[serde(default)]
    pub delay: f64,
}

fn default_true() -> bool {
    true
}

/// Execution context handed to each op's `exec_core`.
///
/// # Phase 1 shape
/// Only the fields needed to compile are present. The scheduler will grow this
/// with tracer handles, cancellation tokens, and the resource hub in Phase 3/4.
/// Mutability of [`MemoryState`] here assumes interior mutability on `Cell` —
/// planned refactor when the scheduler lands.
pub struct OpContext<'a> {
    pub state: &'a MemoryState,
    pub context: &'a ContextId,
}

/// Result of running an op.
///
/// Mirrors Python's return shape from `BaseOp._exec_core`:
/// - `Done(Value)` — a dict of outputs.
/// - `Pending` — `PENDING` sentinel; downstream ops are NOT triggered.
#[derive(Debug, Clone)]
pub enum OpResult {
    Done(Value),
    Pending,
}

/// The core trait every op implements.
///
/// The contract: each op exposes its [`OpMeta`] and an async `exec_core` that
/// takes resolved inputs and returns either an output `Value` dict, `None` for
/// "don't emit" (PENDING), or an error.
///
/// `run()` (the full lifecycle harness) will be provided as a default method
/// in Phase 3/4 once state mutability + ref resolution + cache hashing are
/// wired up.
#[async_trait]
pub trait BaseOp: Send + Sync {
    /// Access the op's shared metadata.
    fn meta(&self) -> &OpMeta;

    /// Execute the op's core logic.
    ///
    /// Inputs are already resolved from refs. Return value is the output dict
    /// (`Some(Value::Object(...))`) or `None` to emit `PENDING`.
    async fn exec_core(
        &self,
        inputs: Map<String, Value>,
        ctx: &OpContext<'_>,
    ) -> Result<Option<Value>, OperonError>;
}
