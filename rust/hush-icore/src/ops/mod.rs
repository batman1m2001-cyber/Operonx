//! Op types — mirrors Python's `hush/core/ops/` package.
//!
//! hush-icore ops (core engine):
//! - op_trait: Op trait (= Python's BaseOp)
//! - transform/func_op: FuncOp (= Python's @op decorated functions)
//! - transform/parser_op: ParserOp (= Python's ParserOp)
//! - flow/branch_op: BranchOp (= Python's BranchOp)
//! - graph/graph_op: GraphOp + scheduler (= Python's GraphOp)
//!
//! Provider ops (LlmOp, EmbeddingOp, etc.) live in hush-providers/src/ops/,
//! mirroring Python's hush-providers/hush/providers/ops/.

pub mod op_trait;
pub mod base;
pub mod cache;
pub mod transform;
pub mod flow;
pub mod graph;
