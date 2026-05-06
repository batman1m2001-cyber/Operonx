//! `ScratchRef` — declarative reference to a key in the per-call scratch space.
//!
//! Mirrors Python [`operonx/core/states/scratch_ref.py`](../../../../../operonx/core/states/scratch_ref.py).
//! Appears in serialized op `inputs` as `{"scratch": {"key": "..."}}` (alongside
//! `ref` / `literal`). Resolved at runtime by `InputResolver::Scratch` →
//! `RuntimeState.scratch.get(key)`.

use serde::{Deserialize, Serialize};

/// Marker pointing at a key in the call's scratch dict.
///
/// At graph-build time on the Python side, `SCRATCH["k"]` returns a `ScratchRef`
/// instance; `_serialize_params()` writes it as `{"scratch": {"key": "k"}}`.
/// On the Rust side, the same JSON deserializes into this struct on `Param`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ScratchRef {
    pub key: String,
}
