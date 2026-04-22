//! Shared runtime utilities — `Param` struct.
//!
//! Mirrors Python `operon/core/utils/common.py`. Note: Python's `ensure_async`,
//! `extract_condition_variables`, and `fake_chunk_from` are Python-specific
//! (asyncio helpers, regex AST walk, test helpers) — not ported to Rust.
//!
//! At runtime on the Rust side, `Param` is a serde-compatible struct representing
//! one entry in `inputs` / `outputs` of a serialized op config. `value` splits
//! into `ref` (a [`RefConfig`](super::super::states::ref_::RefConfig)) or
//! `literal` (a JSON value) depending on whether the Python side wired a Ref
//! or a literal value during graph build — see `BaseOp._serialize_params` in
//! `operon/core/ops/base.py`.

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::states::ref_::RefConfig;

/// A single input/output parameter of an op.
///
/// Rust mirror of Python's `Param` dataclass. Python fields (`type`, `required`,
/// `default`, `description`, `value`) map onto Rust's serialized shape used in
/// `graph.serialize()`: `{default, required, ref, literal}`. Python's single
/// `value` union is split here into discriminated `ref_config` / `literal`
/// fields.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Param {
    /// Default value if neither `ref` nor `literal` is present.
    #[serde(default)]
    pub default: Option<Value>,

    /// Whether the param is required.
    #[serde(default)]
    pub required: bool,

    /// If set, the param's value is pulled from another op's state via this Ref.
    #[serde(rename = "ref", default)]
    pub ref_config: Option<RefConfig>,

    /// If set, the param's value is this literal constant.
    #[serde(default)]
    pub literal: Option<Value>,
}
