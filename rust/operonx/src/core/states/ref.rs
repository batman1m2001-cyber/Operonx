//! `Ref` — variable references with chainable transforms.
//!
//! Mirrors Python `operon/core/states/ref.py`. The serde-facing structs mirror
//! the output of `Ref.serialize()` in Python (see `BaseOp._serialize_params`).
//!
//! # Phase 1 scope
//! This file carries the serde data model only: [`RefConfig`], [`RefTransform`],
//! [`RefArg`], [`StreamPolicy`]. The transform **evaluation** logic (walking
//! state, applying arithmetic/boolean/getitem transforms) will land alongside
//! [`MemoryState`](super::state) in a subsequent step — the two depend on each
//! other and are easier to land together.

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Stream-scheduling policy for an input Ref.
///
/// Mirrors Python `StreamPolicy` dataclass:
/// - `collect=true` → buffer all stream items and dispatch downstream as a list
///   when the source EOFs.
/// - `parallel=true` → all stream items dispatch immediately (optionally capped
///   by `parallel_max`; 0 means unlimited).
/// - Both false (default) → sequential: one item at a time.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StreamPolicy {
    #[serde(default)]
    pub collect: bool,
    #[serde(default)]
    pub parallel: bool,
    /// 0 means unlimited; otherwise the max concurrent parallel items.
    #[serde(default)]
    pub parallel_max: u32,
}

/// Serialized Ref — source op + variable + chain of transforms.
///
/// Matches the JSON shape emitted by Python's `Ref.serialize()`:
/// ```json
/// {
///   "source": "op_name",
///   "var":    "var_name",
///   "transforms": [ ["getitem", ["key"]], ["eq", ["hello"]], ... ],
///   "is_output": false
/// }
/// ```
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RefConfig {
    pub source: String,
    pub var: String,
    #[serde(default)]
    pub transforms: Vec<RefTransform>,
    #[serde(default)]
    pub is_output: bool,

    /// Optional stream-dispatch policy for this input — backwards-compatible
    /// default is `None`, meaning the scheduler uses sequential dispatch.
    /// Python's `Ref.serialize()` currently does **not** emit this field; it
    /// is scheduled for a follow-up update to Python serialization (see
    /// MIGRATION_rust.md §4). Rust-native callers constructing graph JSON
    /// may set it directly to opt into parallel/collect dispatch.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stream_policy: Option<StreamPolicy>,
}

/// One transform in a Ref's chain. Python emits `[name, [args...]]`; we use
/// a custom serde shape to keep that on the wire while exposing a nicer Rust API.
#[derive(Debug, Clone)]
pub struct RefTransform {
    pub name: String,
    pub args: Vec<RefArg>,
}

impl Serialize for RefTransform {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeTuple;
        let mut tup = serializer.serialize_tuple(2)?;
        tup.serialize_element(&self.name)?;
        tup.serialize_element(&self.args)?;
        tup.end()
    }
}

impl<'de> Deserialize<'de> for RefTransform {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let (name, args) = <(String, Vec<RefArg>)>::deserialize(deserializer)?;
        Ok(RefTransform { name, args })
    }
}

/// Argument to a [`RefTransform`].
///
/// Python emits either a literal JSON value OR `{"__ref__": RefConfig}` for a
/// nested Ref (e.g., boolean operators combining two Refs).
#[derive(Debug, Clone)]
pub enum RefArg {
    Literal(Value),
    NestedRef(Box<RefConfig>),
}

impl Serialize for RefArg {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        match self {
            RefArg::Literal(v) => v.serialize(serializer),
            RefArg::NestedRef(r) => {
                use serde::ser::SerializeMap;
                let mut map = serializer.serialize_map(Some(1))?;
                map.serialize_entry("__ref__", r)?;
                map.end()
            }
        }
    }
}

impl<'de> Deserialize<'de> for RefArg {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let value = Value::deserialize(deserializer)?;
        if let Value::Object(map) = &value {
            if let Some(inner) = map.get("__ref__") {
                let nested: RefConfig = serde_json::from_value(inner.clone())
                    .map_err(serde::de::Error::custom)?;
                return Ok(RefArg::NestedRef(Box::new(nested)));
            }
        }
        Ok(RefArg::Literal(value))
    }
}
