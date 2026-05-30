//! `BranchOp` — conditional routing based on a ref-evaluated condition.
//!
//! Mirrors Python [`operonx/core/ops/flow/branch_op.py`](../../../../../operonx/core/ops/flow/branch_op.py).
//! At runtime, evaluates each `Branch` case in order and emits the first matching
//! target name via the `__branch_target__` output key; the scheduler uses this
//! to route downstream.
//!
//! # Phase 1 scope
//! Struct + trait impl skeleton. Full condition evaluation lands with the ref
//! evaluator in Phase 3/4.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::core::exceptions::{OpError, OperonError};
use crate::core::ops::base::{BaseOp, OpContext, OpMeta};
use crate::core::states::ref_::RefConfig;

/// One branch case — a condition ref + a target op name.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Branch {
    pub condition: RefConfig,
    pub target: String,
}

/// Branch-level config (Python's `BranchOp.cases` + `default`).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct BranchConfig {
    #[serde(default)]
    pub cases: Vec<Branch>,
    #[serde(default)]
    pub default: Option<String>,
}

/// `BranchOp` — routes to one of its cases based on condition evaluation.
pub struct BranchOp {
    pub meta: OpMeta,
    pub branch: BranchConfig,
}

#[async_trait]
impl BaseOp for BranchOp {
    fn meta(&self) -> &OpMeta {
        &self.meta
    }

    async fn exec_core(
        &self,
        _inputs: Map<String, Value>,
        _ctx: &OpContext<'_>,
    ) -> Result<Option<Value>, OperonError> {
        // Phase 3/4 will implement: walk `self.branch.cases`, evaluate each
        // `condition` ref via the ref evaluator, return the first match's
        // target name in `{"__branch_target__": target, "matched": "..."}`.
        Err(OperonError::Op(OpError::branch_msg(
            format!(
                "BranchOp::exec_core not yet implemented (phase 1 scaffold for {})",
                self.meta.full_name
            ),
            "",
        )))
    }
}
