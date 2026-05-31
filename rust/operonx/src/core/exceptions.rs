//! Unified exception types for Operon.
//!
//! Mirrors Python `operonx/core/exceptions.py`:
//!
//! - [`OpError`] — op-level error hierarchy (Parser / Code / Branch / Condition /
//!   Iteration / Prompt / Embedding / Rerank), matching Python's `OpError` subclasses.
//!   Each variant carries the same structured context fields Python carries, so
//!   callers can pattern-match the kind AND extract diagnostic detail.
//! - [`OperonError`] — top-level error type wrapping `OpError` plus engine-level
//!   categories (Provider, ResourceHub, Config, State, Runtime).
//!
//! Python's subclass hierarchy collapses to Rust enum variants here.
//! `matches!(err, OperonError::Op(OpError::Parser { .. }))` replaces
//! `isinstance(err, ParserError)`.
//!
//! ## Display format
//!
//! `Display` (and therefore `to_string()`) produce Python's multi-line layout:
//!
//! ```text
//! [PARSER] Invalid JSON syntax
//!   Error: <original>
//!   format: 'json'
//!   input: '{"broken": '
//! ```
//!
//! Substring tests covering `[TAG]`, the message body, the `Error: ...` line,
//! and `key: value` entries — the same shape Python tests in
//! `tests/internal/core/test_exceptions.py` assert against — pass on both sides.

use std::collections::BTreeMap;
use std::error::Error as StdError;
use std::fmt;
use std::path::PathBuf;

/// Truncate a string with a Python-style `...` suffix. Mirrors
/// [`operonx.core.exceptions._truncate`](../../../../../operonx/core/exceptions.py).
pub fn truncate(text: &str, max_length: usize) -> String {
    if text.chars().count() <= max_length {
        text.to_string()
    } else {
        let prefix: String = text.chars().take(max_length).collect();
        format!("{prefix}...")
    }
}

/// Format a context value the way Python's `_format_message` does:
/// string values get `repr()`-style single quotes + `_truncate` truncation;
/// everything else uses Debug formatting. Best-effort parity — exact byte
/// equality with Python isn't required (tests use substring assertions).
fn fmt_context_value(value: &ContextValue) -> String {
    match value {
        ContextValue::Str(s) => {
            let q = format!("'{}'", s.replace('\'', "\\'"));
            truncate(&q, 200)
        }
        ContextValue::Int(n) => n.to_string(),
        ContextValue::Float(f) => f.to_string(),
        ContextValue::List(items) => {
            let inner: Vec<String> = items
                .iter()
                .map(|v| match v {
                    ContextValue::Str(s) => format!("'{s}'"),
                    other => fmt_context_value(other),
                })
                .collect();
            format!("[{}]", inner.join(", "))
        }
        ContextValue::Dict(m) => {
            let inner: Vec<String> = m
                .iter()
                .map(|(k, v)| format!("'{}': {}", k, fmt_context_value(v)))
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
        ContextValue::Bool(b) => b.to_string(),
        ContextValue::Null => "None".into(),
    }
}

/// Typed context value for structured fields. Bridges to JSON Value for
/// round-tripping with the JSON wire layer; kept narrow so Display formatting
/// matches Python's `repr()` shape for the common cases.
#[derive(Debug, Clone)]
pub enum ContextValue {
    Str(String),
    Int(i64),
    Float(f64),
    Bool(bool),
    Null,
    List(Vec<ContextValue>),
    Dict(BTreeMap<String, ContextValue>),
}

impl From<&str> for ContextValue {
    fn from(s: &str) -> Self {
        ContextValue::Str(s.to_string())
    }
}
impl From<String> for ContextValue {
    fn from(s: String) -> Self {
        ContextValue::Str(s)
    }
}
impl From<i64> for ContextValue {
    fn from(n: i64) -> Self {
        ContextValue::Int(n)
    }
}
impl From<usize> for ContextValue {
    fn from(n: usize) -> Self {
        ContextValue::Int(n as i64)
    }
}
impl From<bool> for ContextValue {
    fn from(b: bool) -> Self {
        ContextValue::Bool(b)
    }
}
impl<T: Into<ContextValue>> From<Vec<T>> for ContextValue {
    fn from(v: Vec<T>) -> Self {
        ContextValue::List(v.into_iter().map(Into::into).collect())
    }
}

/// Optional "original error" carried alongside the op-error variant — the
/// upstream cause of the failure. `Display` formats as Python's
/// `f"  Error: {self.original_error}"` line.
pub type OriginalError = Option<String>;

/// Op-level error categories, mirroring Python's `OpError` subclass hierarchy.
///
/// Each variant carries the structured context fields the matching Python
/// subclass takes, in the same names. Use `matches!(e, OpError::Parser { .. })`
/// to pattern-match by category; `if let OpError::Parser { input_text, format_type, .. } = e`
/// to extract specific fields.
#[derive(Debug, Clone)]
pub enum OpError {
    /// Mirrors Python `ParserError(message, input_text, format_type, original_error)`.
    Parser {
        message: String,
        input_text: String,
        format_type: String,
        original_error: OriginalError,
    },
    /// Mirrors Python `CodeError(message, function_name, source, inputs, original_error)`.
    Code {
        message: String,
        function_name: String,
        source: String,
        inputs: BTreeMap<String, String>,
        original_error: OriginalError,
    },
    /// Mirrors Python `BranchError(message, condition, inputs, candidates, original_error)`.
    Branch {
        message: String,
        condition: String,
        inputs: BTreeMap<String, String>,
        candidates: Vec<String>,
        original_error: OriginalError,
    },
    /// Mirrors Python `ConditionError(message, condition, inputs, iteration, phase, original_error)`.
    Condition {
        message: String,
        condition: String,
        inputs: BTreeMap<String, String>,
        iteration: Option<usize>,
        phase: String,
        original_error: OriginalError,
    },
    /// Mirrors Python `IterationError(message, iteration_index, loop_data, total_iterations, op_type, original_error)`.
    Iteration {
        message: String,
        iteration_index: usize,
        loop_data: BTreeMap<String, String>,
        total_iterations: usize,
        op_type: String,
        original_error: OriginalError,
    },
    /// Mirrors Python `PromptError(message, template, missing_vars, original_error)`.
    Prompt {
        message: String,
        template_type: String,
        template: String,
        missing_vars: Vec<String>,
        original_error: OriginalError,
    },
    /// Mirrors Python `EmbeddingError(message, resource, text_count, original_error)`.
    Embedding {
        message: String,
        resource: String,
        text_count: usize,
        original_error: OriginalError,
    },
    /// Mirrors Python `RerankError(message, resource, query, document_count, original_error)`.
    Rerank {
        message: String,
        resource: String,
        query: String,
        document_count: usize,
        original_error: OriginalError,
    },
}

impl OpError {
    /// Short tag matching Python's `op_type.upper()` — used as the `[TAG]`
    /// prefix in `Display` and as a discriminator for parity tests.
    pub fn tag(&self) -> &'static str {
        match self {
            OpError::Parser { .. } => "PARSER",
            OpError::Code { .. } => "CODE",
            OpError::Branch { .. } => "BRANCH",
            OpError::Condition { .. } => "WHILE",
            OpError::Iteration { .. } => "FOR",
            OpError::Prompt { .. } => "PROMPT",
            OpError::Embedding { .. } => "EMBEDDING",
            OpError::Rerank { .. } => "RERANK",
        }
    }

    /// Short lowercase kind matching Python's `op_type` field — useful for
    /// structured emission to telemetry / parity-fixture comparison.
    pub fn kind(&self) -> &'static str {
        match self {
            OpError::Parser { .. } => "parser",
            OpError::Code { .. } => "code",
            OpError::Branch { .. } => "branch",
            OpError::Condition { .. } => "while",
            OpError::Iteration { .. } => "for",
            OpError::Prompt { .. } => "prompt",
            OpError::Embedding { .. } => "embedding",
            OpError::Rerank { .. } => "rerank",
        }
    }

    /// User-supplied message (the first positional arg every constructor takes).
    pub fn message(&self) -> &str {
        match self {
            OpError::Parser { message, .. }
            | OpError::Code { message, .. }
            | OpError::Branch { message, .. }
            | OpError::Condition { message, .. }
            | OpError::Iteration { message, .. }
            | OpError::Prompt { message, .. }
            | OpError::Embedding { message, .. }
            | OpError::Rerank { message, .. } => message,
        }
    }

    /// Original upstream error, if one was attached.
    pub fn original_error(&self) -> Option<&str> {
        let oe = match self {
            OpError::Parser { original_error, .. }
            | OpError::Code { original_error, .. }
            | OpError::Branch { original_error, .. }
            | OpError::Condition { original_error, .. }
            | OpError::Iteration { original_error, .. }
            | OpError::Prompt { original_error, .. }
            | OpError::Embedding { original_error, .. }
            | OpError::Rerank { original_error, .. } => original_error,
        };
        oe.as_deref()
    }

    /// Variant-specific context as `(key, formatted-value)` pairs, in the same
    /// order Python emits them in `_format_message`. Returned as borrowed
    /// strings to avoid allocation in the no-context path.
    fn context_lines(&self) -> Vec<(String, String)> {
        match self {
            OpError::Parser {
                input_text,
                format_type,
                ..
            } => vec![
                (
                    "format".into(),
                    fmt_context_value(&ContextValue::Str(format_type.clone())),
                ),
                (
                    "input".into(),
                    fmt_context_value(&ContextValue::Str(input_text.clone())),
                ),
            ],
            OpError::Code {
                function_name,
                source,
                inputs,
                ..
            } => {
                let inputs_map: BTreeMap<String, ContextValue> = inputs
                    .iter()
                    .map(|(k, v)| (k.clone(), ContextValue::Str(truncate(v, 100))))
                    .collect();
                vec![
                    (
                        "function_name".into(),
                        fmt_context_value(&ContextValue::Str(function_name.clone())),
                    ),
                    (
                        "source".into(),
                        fmt_context_value(&ContextValue::Str(truncate(source, 300))),
                    ),
                    (
                        "inputs".into(),
                        fmt_context_value(&ContextValue::Dict(inputs_map)),
                    ),
                ]
            }
            OpError::Branch {
                condition,
                inputs,
                candidates,
                ..
            } => {
                let inputs_map: BTreeMap<String, ContextValue> = inputs
                    .iter()
                    .map(|(k, v)| (k.clone(), ContextValue::Str(truncate(v, 100))))
                    .collect();
                vec![
                    (
                        "condition".into(),
                        fmt_context_value(&ContextValue::Str(condition.clone())),
                    ),
                    (
                        "inputs".into(),
                        fmt_context_value(&ContextValue::Dict(inputs_map)),
                    ),
                    (
                        "candidates".into(),
                        fmt_context_value(&ContextValue::List(
                            candidates
                                .iter()
                                .map(|c| ContextValue::Str(c.clone()))
                                .collect(),
                        )),
                    ),
                ]
            }
            OpError::Condition {
                condition,
                inputs,
                iteration,
                phase,
                ..
            } => {
                let mut out = vec![
                    (
                        "condition".into(),
                        fmt_context_value(&ContextValue::Str(condition.clone())),
                    ),
                    (
                        "phase".into(),
                        fmt_context_value(&ContextValue::Str(phase.clone())),
                    ),
                ];
                if !inputs.is_empty() {
                    let inputs_map: BTreeMap<String, ContextValue> = inputs
                        .iter()
                        .map(|(k, v)| (k.clone(), ContextValue::Str(truncate(v, 100))))
                        .collect();
                    out.push((
                        "inputs".into(),
                        fmt_context_value(&ContextValue::Dict(inputs_map)),
                    ));
                }
                if let Some(i) = iteration {
                    out.push(("iteration".into(), i.to_string()));
                }
                out
            }
            OpError::Iteration {
                iteration_index,
                loop_data,
                total_iterations,
                ..
            } => {
                let data_map: BTreeMap<String, ContextValue> = loop_data
                    .iter()
                    .map(|(k, v)| (k.clone(), ContextValue::Str(truncate(v, 100))))
                    .collect();
                vec![
                    (
                        "iteration_index".into(),
                        format!("{}/{}", iteration_index, total_iterations),
                    ),
                    (
                        "loop_data".into(),
                        fmt_context_value(&ContextValue::Dict(data_map)),
                    ),
                ]
            }
            OpError::Prompt {
                template_type,
                template,
                missing_vars,
                ..
            } => {
                let mut out = vec![
                    (
                        "template_type".into(),
                        fmt_context_value(&ContextValue::Str(template_type.clone())),
                    ),
                    (
                        "template".into(),
                        fmt_context_value(&ContextValue::Str(truncate(template, 300))),
                    ),
                ];
                if !missing_vars.is_empty() {
                    out.push((
                        "missing_vars".into(),
                        fmt_context_value(&ContextValue::List(
                            missing_vars
                                .iter()
                                .map(|v| ContextValue::Str(v.clone()))
                                .collect(),
                        )),
                    ));
                }
                out
            }
            OpError::Embedding {
                resource,
                text_count,
                ..
            } => vec![
                (
                    "resource".into(),
                    fmt_context_value(&ContextValue::Str(resource.clone())),
                ),
                ("text_count".into(), text_count.to_string()),
            ],
            OpError::Rerank {
                resource,
                query,
                document_count,
                ..
            } => vec![
                (
                    "resource".into(),
                    fmt_context_value(&ContextValue::Str(resource.clone())),
                ),
                (
                    "query".into(),
                    fmt_context_value(&ContextValue::Str(truncate(query, 100))),
                ),
                ("document_count".into(), document_count.to_string()),
            ],
        }
    }
}

impl fmt::Display for OpError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        writeln!(f, "[{}] {}", self.tag(), self.message())?;
        if let Some(orig) = self.original_error() {
            writeln!(f, "  Error: {}", orig)?;
        }
        let lines = self.context_lines();
        for (i, (k, v)) in lines.iter().enumerate() {
            if i == lines.len() - 1 {
                write!(f, "  {}: {}", k, v)?;
            } else {
                writeln!(f, "  {}: {}", k, v)?;
            }
        }
        Ok(())
    }
}

impl StdError for OpError {}

// ── Convenience constructors — terse `OpError::code("msg", "fn", ...)` style ──

impl OpError {
    /// Short-form constructor: fill optional structured fields with defaults.
    /// Mirrors the common case where a caller has just a message + the op's
    /// own name. For richer context use the struct literal form directly.
    pub fn code_msg(message: impl Into<String>, function_name: impl Into<String>) -> Self {
        OpError::Code {
            message: message.into(),
            function_name: function_name.into(),
            source: String::new(),
            inputs: BTreeMap::new(),
            original_error: None,
        }
    }

    pub fn parser_msg(message: impl Into<String>, format_type: impl Into<String>) -> Self {
        OpError::Parser {
            message: message.into(),
            input_text: String::new(),
            format_type: format_type.into(),
            original_error: None,
        }
    }

    pub fn branch_msg(message: impl Into<String>, condition: impl Into<String>) -> Self {
        OpError::Branch {
            message: message.into(),
            condition: condition.into(),
            inputs: BTreeMap::new(),
            candidates: Vec::new(),
            original_error: None,
        }
    }

    pub fn prompt_msg(message: impl Into<String>) -> Self {
        OpError::Prompt {
            message: message.into(),
            template_type: String::new(),
            template: String::new(),
            missing_vars: Vec::new(),
            original_error: None,
        }
    }

    pub fn embedding_msg(
        message: impl Into<String>,
        resource: impl Into<String>,
        text_count: usize,
    ) -> Self {
        OpError::Embedding {
            message: message.into(),
            resource: resource.into(),
            text_count,
            original_error: None,
        }
    }

    pub fn rerank_msg(
        message: impl Into<String>,
        resource: impl Into<String>,
        document_count: usize,
    ) -> Self {
        OpError::Rerank {
            message: message.into(),
            resource: resource.into(),
            query: String::new(),
            document_count,
            original_error: None,
        }
    }
}

/// Top-level error type for all Operon operations.
///
/// Wraps [`OpError`] for op-level failures and adds engine-level categories.
#[derive(Debug, Clone, thiserror::Error)]
pub enum OperonError {
    #[error("{0}")]
    Op(#[from] OpError),

    #[error("provider error: {0}")]
    Provider(String),

    #[error("resource hub: {0}")]
    ResourceHub(String),

    #[error("config: {0}")]
    Config(String),

    #[error("state: {0}")]
    State(String),

    #[error("runtime: {0}")]
    Runtime(String),

    #[error("schema: unsupported schema_version {0}; expected {expected}", expected = SUPPORTED_SCHEMA_VERSION)]
    UnsupportedSchema(String),

    /// `${VAR}` reference in `resources.yaml` could not be resolved.
    ///
    /// Distinct from [`OperonError::ResourceHub`] / [`OperonError::Config`] so
    /// callers can match specifically on missing-env-var setup problems and
    /// give the user a tailored remediation hint. Mirrors Python's
    /// `EnvVarUnsetError` (subclass of `RuntimeError`).
    #[error(
        "resource '{key}' references unset environment variable {var:?}\n  source: {source_path:?}\n  .env paths searched: {env_paths:?}"
    )]
    EnvVarUnset {
        var: String,
        key: String,
        source_path: Option<PathBuf>,
        env_paths: Vec<PathBuf>,
    },
}

/// The serialized graph schema version Rust accepts. Bump on breaking changes.
pub const SUPPORTED_SCHEMA_VERSION: &str = "1.0";

// ── From impls for common upstream errors ─────────────────────────────────

impl From<serde_json::Error> for OperonError {
    fn from(e: serde_json::Error) -> Self {
        OperonError::Config(e.to_string())
    }
}

impl From<serde_yaml::Error> for OperonError {
    fn from(e: serde_yaml::Error) -> Self {
        OperonError::Config(e.to_string())
    }
}

impl From<std::io::Error> for OperonError {
    fn from(e: std::io::Error) -> Self {
        OperonError::Runtime(e.to_string())
    }
}

/// Convenience alias for `Result<T, OperonError>`.
pub type Result<T> = std::result::Result<T, OperonError>;

/// Boxed dynamic error for cases where the source type is opaque (e.g., provider HTTP errors).
pub type BoxError = Box<dyn StdError + Send + Sync + 'static>;
