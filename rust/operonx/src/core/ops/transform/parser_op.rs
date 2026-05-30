//! `ParserOp` — extract structured data from JSON / XML / YAML text.
//!
//! Mirrors Python [`operonx/core/ops/transform/parser_op.py`](../../../../../operonx/core/ops/transform/parser_op.py).
//! Parses `input_text` according to `parse_as` (json/xml/yaml) and returns either
//! the full parsed value or a dot-path–extracted subvalue per field spec.
//!
//! # Phase 1 scope
//! Struct + trait skeleton. Full parsing + dot-path extraction lands in Phase 4.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::core::exceptions::{OpError, OperonError};
use crate::core::ops::base::{BaseOp, OpContext, OpMeta};

/// Parser format.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ParserType {
    Json,
    Xml,
    Yaml,
}

/// One extracted field — Python's `ExtractField`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExtractField {
    pub name: String,
    #[serde(default)]
    pub path: Option<String>,
    #[serde(default)]
    pub required: bool,
}

/// `ParserOp` — parse input text into structured data.
pub struct ParserOp {
    pub meta: OpMeta,
    pub parse_as: ParserType,
    pub extract_fields: Vec<ExtractField>,
}

#[async_trait]
impl BaseOp for ParserOp {
    fn meta(&self) -> &OpMeta {
        &self.meta
    }

    async fn exec_core(
        &self,
        _inputs: Map<String, Value>,
        _ctx: &OpContext<'_>,
    ) -> Result<Option<Value>, OperonError> {
        // Phase 4: read input_text from `_inputs`, strip code fences if present,
        // parse via serde_json / quick-xml / serde_yaml per `self.parse_as`,
        // apply dot-path extraction per `self.extract_fields`, return map.
        Err(OperonError::Op(OpError::parser_msg(
            format!(
                "ParserOp::exec_core not yet implemented (phase 1 scaffold for {})",
                self.meta.full_name
            ),
            "unknown",
        )))
    }
}
