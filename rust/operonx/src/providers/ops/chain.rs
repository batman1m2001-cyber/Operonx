//! `chat()` / `ask()` — graph-builder helpers for LLM workflows.
//!
//! Mirrors Python [`operon/providers/ops/chain.py`](../../../../../operon/providers/ops/chain.py).
//!
//! # What this module actually does
//!
//! Python's `chain.py` exposes `@graph`-decorated functions that a user
//! calls *at Python build time* to wire Prompt → LLM → Parser subgraphs:
//!
//! ```python
//! c = chat(resource="gpt-4o", template={"system": "...", "user": "{q}"}, q=PARENT["q"])
//! ```
//!
//! Rust is **runtime-only** per plan §1 — it consumes already-serialized
//! `GraphOp` JSON; there is no Rust `@graph` decorator, no `PARENT >> op >>`
//! build syntax, no context-manager graph tracking. The `@graph` machinery
//! is explicitly in the "drop" bucket per plan §9.
//!
//! Still, the port lists `chain.rs` alongside the runtime provider ops in
//! plan §11 Phase 6. To satisfy the letter of the plan *and* remain useful
//! to Rust-native callers building workflows programmatically, this module
//! ships two **builder helpers** that emit the same JSON shape Python's
//! decorator emits — so Rust can construct chat/ask subgraphs without
//! depending on Python at build time.
//!
//! The emitted JSON round-trips through [`GraphEnvelope::parse`](crate::core::engine::GraphEnvelope::parse)
//! and runs through the scheduler unchanged.

use serde_json::{json, Value};

/// Arguments for [`build_chat_graph`].
#[derive(Debug, Clone, Default)]
pub struct ChatArgs {
    /// Prompt template — string, `{system, user}` dict, or pre-built
    /// messages array. Same forms Python's `PromptOp` accepts.
    pub template: Option<Value>,
    /// Single resource key or a list of keys for load balancing.
    pub resource: Option<Value>,
    /// Load-balancing ratios (only when `resource` is a list).
    pub ratios: Option<Vec<f32>>,
    /// Ordered fallback chain tried when the primary errors.
    pub fallback: Option<Vec<String>>,
    /// Provider-specific `response_format` passthrough (e.g. JSON mode).
    pub response_format: Option<Value>,
    /// Soft delay (seconds) before dispatching the LLM step — inherits from
    /// Python's `LLMOp.delay`.
    pub delay: f64,
    /// Top-level graph name. Defaults to `"chat"`.
    pub name: String,
}

/// Arguments for [`build_ask_graph`].
#[derive(Debug, Clone, Default)]
pub struct AskArgs {
    /// Template — see [`ChatArgs::template`].
    pub template: Option<Value>,
    /// Resource key(s) — see [`ChatArgs::resource`].
    pub resource: Option<Value>,
    /// Load-balancing ratios.
    pub ratios: Option<Vec<f32>>,
    /// Fallback chain.
    pub fallback: Option<Vec<String>>,
    /// Required — list of `"name: type"` fields the parser should extract.
    pub fields: Vec<String>,
    /// Parser format — `"xml"` (default) or `"json"`.
    pub parser: String,
    /// Soft delay before the LLM step.
    pub delay: f64,
    /// Optional validators dict — maps each field to its allowed values.
    pub validators: Option<Value>,
    /// Provider-specific response-format passthrough.
    pub response_format: Option<Value>,
    /// Top-level graph name. Defaults to `"ask"`.
    pub name: String,
}

/// Build a Prompt → LLM chat graph as a JSON `Value` ready to feed
/// [`Operon::new`](crate::core::engine::Operon::new).
///
/// Mirrors Python's `chat()` output when serialized via
/// `Operon.export_config()`.
pub fn build_chat_graph(args: &ChatArgs) -> Value {
    let graph_name = if args.name.is_empty() { "chat" } else { &args.name };
    let prompt_full = format!("{}.prompt", graph_name);
    let llm_full = format!("{}.llm", graph_name);

    let mut prompt_inputs = serde_json::Map::new();
    if let Some(t) = &args.template {
        prompt_inputs.insert("template".into(), json!({"literal": t}));
    }

    let mut llm_inputs = serde_json::Map::new();
    llm_inputs.insert(
        "messages".into(),
        json!({
            "required": true,
            "ref": {"source": prompt_full, "var": "messages"}
        }),
    );
    if let Some(fmt) = &args.response_format {
        llm_inputs.insert("response_format".into(), json!({"literal": fmt}));
    }

    let mut llm_op = json!({
        "type": "llm",
        "name": "llm",
        "full_name": llm_full,
        "bound": "io",
        "delay": args.delay,
        "inputs": llm_inputs,
        "outputs": {
            "content": {"ref": {"source": "__PARENT__", "var": "content", "is_output": true}},
            "role": {"ref": {"source": "__PARENT__", "var": "role", "is_output": true}},
            "model_used": {"ref": {"source": "__PARENT__", "var": "model_used", "is_output": true}},
            "usage": {"ref": {"source": "__PARENT__", "var": "usage", "is_output": true}},
            "extras": {"ref": {"source": "__PARENT__", "var": "extras", "is_output": true}}
        }
    });
    attach_resource(&mut llm_op, &args.resource, &args.ratios, &args.fallback);

    json!({
        "schema_version": "1.0",
        "type": "graph",
        "name": graph_name,
        "full_name": graph_name,
        "entries": ["prompt"],
        "exits": ["llm"],
        "initial_ready_count": {"prompt": 0, "llm": 1},
        "compiled_adj": {
            "prompt": [["llm", false]],
            "llm": []
        },
        "inputs": {},
        "outputs": {
            "content": {}, "role": {}, "model_used": {}, "usage": {}, "extras": {}
        },
        "ops": {
            "prompt": {
                "type": "prompt",
                "name": "prompt",
                "full_name": prompt_full,
                "bound": "sync",
                "inputs": prompt_inputs,
                "outputs": {"messages": {}}
            },
            "llm": llm_op
        }
    })
}

/// Build a Prompt → LLM → Parser ask graph as JSON.
///
/// Mirrors Python's `ask()` output. Panics if `fields` is empty (same as
/// Python raising `TypeError`).
pub fn build_ask_graph(args: &AskArgs) -> Value {
    assert!(
        !args.fields.is_empty(),
        "build_ask_graph: `fields` is required — matches Python's TypeError"
    );
    let graph_name = if args.name.is_empty() { "ask" } else { &args.name };
    let prompt_full = format!("{}.prompt", graph_name);
    let llm_full = format!("{}.llm", graph_name);
    let parser_full = format!("{}.parser", graph_name);
    let parser_fmt = if args.parser.is_empty() {
        "xml"
    } else {
        &args.parser
    };

    let mut prompt_inputs = serde_json::Map::new();
    if let Some(t) = &args.template {
        prompt_inputs.insert("template".into(), json!({"literal": t}));
    }

    let mut llm_inputs = serde_json::Map::new();
    llm_inputs.insert(
        "messages".into(),
        json!({
            "required": true,
            "ref": {"source": prompt_full, "var": "messages"}
        }),
    );
    if let Some(fmt) = &args.response_format {
        llm_inputs.insert("response_format".into(), json!({"literal": fmt}));
    }

    let mut llm_op = json!({
        "type": "llm",
        "name": "llm",
        "full_name": llm_full,
        "bound": "io",
        "delay": args.delay,
        "inputs": llm_inputs,
        "outputs": {"content": {}}
    });
    attach_resource(&mut llm_op, &args.resource, &args.ratios, &args.fallback);

    let mut parser_inputs = serde_json::Map::new();
    parser_inputs.insert(
        "text".into(),
        json!({
            "required": true,
            "ref": {"source": llm_full, "var": "content"}
        }),
    );
    if let Some(v) = &args.validators {
        parser_inputs.insert("validators".into(), json!({"literal": v}));
    }
    parser_inputs.insert("format".into(), json!({"literal": parser_fmt}));
    parser_inputs.insert("extract".into(), json!({"literal": args.fields}));

    let parser_op = json!({
        "type": "parser",
        "name": "parser",
        "full_name": parser_full,
        "bound": "sync",
        "inputs": parser_inputs,
        "outputs": {"*": {"ref": {"source": "__PARENT__", "var": "*", "is_output": true}}}
    });

    json!({
        "schema_version": "1.0",
        "type": "graph",
        "name": graph_name,
        "full_name": graph_name,
        "entries": ["prompt"],
        "exits": ["parser"],
        "initial_ready_count": {"prompt": 0, "llm": 1, "parser": 1},
        "compiled_adj": {
            "prompt": [["llm", false]],
            "llm":    [["parser", false]],
            "parser": []
        },
        "inputs": {},
        "outputs": {},
        "ops": {
            "prompt": {
                "type": "prompt",
                "name": "prompt",
                "full_name": prompt_full,
                "bound": "sync",
                "inputs": prompt_inputs,
                "outputs": {"messages": {}}
            },
            "llm": llm_op,
            "parser": parser_op
        }
    })
}

fn attach_resource(
    op: &mut Value,
    resource: &Option<Value>,
    ratios: &Option<Vec<f32>>,
    fallback: &Option<Vec<String>>,
) {
    let obj = op.as_object_mut().expect("op is an object literal");
    if let Some(r) = resource {
        obj.insert("resource".into(), r.clone());
    }
    if let Some(rt) = ratios {
        obj.insert("ratios".into(), json!(rt));
    }
    if let Some(fb) = fallback {
        obj.insert("fallback".into(), json!(fb));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::engine::GraphEnvelope;

    #[test]
    fn chat_graph_round_trips_through_envelope() {
        let g = build_chat_graph(&ChatArgs {
            template: Some(json!({"system": "ok", "user": "hi"})),
            resource: Some(json!("gpt-4o")),
            ..Default::default()
        });
        let env = GraphEnvelope::parse(&g.to_string()).unwrap();
        assert_eq!(env.config.name, "chat");
        assert_eq!(env.config.ops.len(), 2);
        assert!(env.config.ops.contains_key("prompt"));
        assert!(env.config.ops.contains_key("llm"));
    }

    #[test]
    fn ask_graph_requires_fields() {
        let args = AskArgs {
            template: Some(json!("q")),
            ..Default::default()
        };
        let result = std::panic::catch_unwind(|| build_ask_graph(&args));
        assert!(result.is_err(), "empty `fields` must panic");
    }

    #[test]
    fn ask_graph_round_trips() {
        let g = build_ask_graph(&AskArgs {
            template: Some(json!("Classify: {speech}")),
            resource: Some(json!("claude-haiku")),
            fields: vec!["result: str".into()],
            parser: "xml".into(),
            ..Default::default()
        });
        let env = GraphEnvelope::parse(&g.to_string()).unwrap();
        assert_eq!(env.config.ops.len(), 3);
        assert!(env.config.ops.contains_key("parser"));
    }
}
