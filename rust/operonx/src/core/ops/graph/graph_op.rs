//! `GraphOp` — container op that holds and dispatches a DAG of child ops.
//!
//! Mirrors Python [`operonx/core/ops/graph/graph_op.py`](../../../../../operonx/core/ops/graph/graph_op.py).
//! The Python class owns both the graph metadata *and* an embedded scheduler.
//! Per plan §4a, Rust splits those concerns:
//! - `GraphOp` (this file) — metadata wrapper around a serialized [`OpConfig`].
//! - [`task_scheduler`](super::task_scheduler) — the runtime event loop.
//!
//! # Phase 4 scope
//! Minimal wrapper. Nested-graph execution will land alongside the ref
//! evaluator in a later pass; until then the top-level scheduler handles only
//! flat graphs (see [`task_scheduler::GraphScheduler::execute_op`]).

use std::sync::Arc;

use async_trait::async_trait;
use serde_json::{Map, Value};

use crate::core::configs::op_config::{OpConfig, OpType};
use crate::core::exceptions::{OpError, OperonError};
use crate::core::ops::base::{BaseOp, OpContext, OpMeta};

/// Graph container — holds a parsed [`OpConfig`] of kind [`OpType::Graph`].
///
/// Instances are constructed only when a parent scheduler dispatches into a
/// nested graph. For the root graph the engine consults the [`OpConfig`]
/// directly via [`task_scheduler::GraphScheduler`](super::task_scheduler::GraphScheduler).
pub struct GraphOp {
    pub meta: OpMeta,
    pub config: Arc<OpConfig>,
}

impl GraphOp {
    /// Wrap an already-parsed [`OpConfig`]. Errors if `config.kind != Graph`.
    pub fn new(config: Arc<OpConfig>) -> Result<Self, OperonError> {
        if !matches!(config.kind, OpType::Graph) {
            return Err(OperonError::Config(format!(
                "GraphOp requires type=\"graph\"; got {:?}",
                config.kind
            )));
        }
        let meta = OpMeta {
            name: config.name.clone(),
            full_name: config.full_name.clone(),
            kind: config.kind,
            enabled: config.enabled,
            verbose: config.verbose,
            stream: config.stream,
            bound: config.bound,
            inputs: config
                .inputs
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect(),
            outputs: config
                .outputs
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect(),
            cache: config.cache.clone(),
            delay: config.delay,
        };
        Ok(Self { meta, config })
    }
}

#[async_trait]
impl BaseOp for GraphOp {
    fn meta(&self) -> &OpMeta {
        &self.meta
    }

    async fn exec_core(
        &self,
        _inputs: Map<String, Value>,
        _ctx: &OpContext<'_>,
    ) -> Result<Option<Value>, OperonError> {
        Err(OperonError::Op(OpError::Code(format!(
            "nested GraphOp::exec_core not yet implemented (Phase 4 scaffolding for '{}')",
            self.meta.full_name
        ))))
    }
}
