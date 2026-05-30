//! Internal tests for `core::exceptions`.
//!
//! Mirrors [tests/internal/core/test_exceptions.py](../../../../../tests/internal/core/test_exceptions.py)
//! — same coverage shape (per-variant unit tests on construction, attribute access,
//! truncation, and Display string substring expectations).
//!
//! The architecture-parity contract: each Python `OpError` subclass has an
//! identically-shaped Rust enum variant carrying the same structured fields,
//! and the formatted `Display` string contains the same tag/message/context
//! lines so the substring assertions Python runs (`"[PARSER]" in msg`,
//! `"format:" in msg`, etc) succeed on Rust output too.
//!
//! Run via `cargo test -p operonx --test internal_core exceptions::`.

use std::collections::BTreeMap;

use operonx::core::exceptions::{truncate, OpError, OperonError};

// ============================================================
// truncate helper — mirrors TestTruncateHelper
// ============================================================

#[test]
fn truncate_short_text_unchanged() {
    assert_eq!(truncate("Hello World", 200), "Hello World");
}

#[test]
fn truncate_exact_length_unchanged() {
    let text: String = "x".repeat(200);
    let result = truncate(&text, 200);
    assert_eq!(result, text);
    assert_eq!(result.len(), 200);
}

#[test]
fn truncate_long_text_gets_ellipsis() {
    let text: String = "x".repeat(300);
    let result = truncate(&text, 200);
    assert_eq!(result, format!("{}...", "x".repeat(200)));
    assert_eq!(result.len(), 203);
}

#[test]
fn truncate_custom_max_length_respected() {
    assert_eq!(truncate("Hello World", 5), "Hello...");
}

// ============================================================
// OpError — TAG prefix + message + context Display shape
// ============================================================

#[test]
fn op_error_parser_basic_display_includes_tag_and_message() {
    let err = OpError::Parser {
        message: "Invalid JSON syntax".into(),
        input_text: r#"{"broken": "#.into(),
        format_type: "json".into(),
        original_error: None,
    };
    let msg = err.to_string();
    assert!(msg.contains("[PARSER]"), "want [PARSER] in: {msg}");
    assert!(msg.contains("Invalid JSON syntax"), "want body in: {msg}");
    assert!(msg.contains("format:"), "want format: in: {msg}");
    assert!(msg.contains("json"), "want json in: {msg}");
}

#[test]
fn op_error_parser_with_original_error_renders_error_line() {
    let err = OpError::Parser {
        message: "Failed to parse JSON".into(),
        input_text: r#"{"broken": "#.into(),
        format_type: "json".into(),
        original_error: Some("Expecting value: line 1 column 12 (char 11)".into()),
    };
    let msg = err.to_string();
    assert!(msg.contains("Error: Expecting value"), "want Error: line in: {msg}");
}

#[test]
fn op_error_parser_long_input_truncated() {
    let long_text: String = "x".repeat(500);
    let err = OpError::Parser {
        message: "Parse failed".into(),
        input_text: long_text,
        format_type: "json".into(),
        original_error: None,
    };
    let msg = err.to_string();
    assert!(msg.contains("..."), "want ... ellipsis in: {msg}");
}

#[test]
fn op_error_parser_field_access() {
    let err = OpError::Parser {
        message: "x".into(),
        input_text: "input".into(),
        format_type: "xml".into(),
        original_error: None,
    };
    if let OpError::Parser {
        input_text,
        format_type,
        ..
    } = &err
    {
        assert_eq!(input_text, "input");
        assert_eq!(format_type, "xml");
    } else {
        panic!("expected Parser variant");
    }
}

#[test]
fn op_error_code_display_includes_function_and_source() {
    let mut inputs = BTreeMap::new();
    inputs.insert("x".into(), "1".into());
    inputs.insert("y".into(), "2".into());
    let err = OpError::Code {
        message: "divide by zero".into(),
        function_name: "calculate_total".into(),
        source: "def calculate_total(x, y): return x / y".into(),
        inputs,
        original_error: Some("ZeroDivisionError".into()),
    };
    let msg = err.to_string();
    assert!(msg.contains("[CODE]"), "want [CODE] in: {msg}");
    assert!(msg.contains("divide by zero"), "want body in: {msg}");
    assert!(msg.contains("calculate_total"), "want fn name in: {msg}");
    assert!(msg.contains("Error: ZeroDivisionError"), "want Error: line in: {msg}");
}

#[test]
fn op_error_code_long_source_truncated() {
    let long_source: String = "x".repeat(500);
    let err = OpError::Code {
        message: "fail".into(),
        function_name: "f".into(),
        source: long_source,
        inputs: BTreeMap::new(),
        original_error: None,
    };
    let msg = err.to_string();
    assert!(msg.contains("..."), "want truncation in: {msg}");
}

#[test]
fn op_error_branch_display_includes_condition_and_candidates() {
    let mut inputs = BTreeMap::new();
    inputs.insert("score".into(), "invalid".into());
    let err = OpError::Branch {
        message: "Condition evaluation failed".into(),
        condition: "score >= 90".into(),
        inputs,
        candidates: vec!["excellent".into(), "good".into(), "fail".into()],
        original_error: Some("TypeError: unorderable types: str() >= int()".into()),
    };
    let msg = err.to_string();
    assert!(msg.contains("[BRANCH]"));
    assert!(msg.contains("score >= 90"));
    assert!(msg.contains("'excellent'") || msg.contains("excellent"));
    assert!(msg.contains("Error: TypeError"));
}

#[test]
fn op_error_condition_compile_phase() {
    let err = OpError::Condition {
        message: "Failed to compile".into(),
        condition: "counter >= ((".into(),
        inputs: BTreeMap::new(),
        iteration: None,
        phase: "compile".into(),
        original_error: Some("SyntaxError".into()),
    };
    let msg = err.to_string();
    assert!(msg.contains("[WHILE]"));
    assert!(msg.contains("phase:"));
    assert!(msg.contains("compile"));
}

#[test]
fn op_error_condition_eval_phase_with_iteration() {
    let err = OpError::Condition {
        message: "Stop condition evaluation failed".into(),
        condition: "counter >= 5".into(),
        inputs: BTreeMap::new(),
        iteration: Some(3),
        phase: "eval".into(),
        original_error: None,
    };
    let msg = err.to_string();
    assert!(msg.contains("iteration: 3"));
}

#[test]
fn op_error_iteration_for_loop_index_format() {
    let mut data = BTreeMap::new();
    data.insert("item".into(), "value".into());
    let err = OpError::Iteration {
        message: "Iteration 2 failed".into(),
        iteration_index: 2,
        loop_data: data,
        total_iterations: 10,
        op_type: "for".into(),
        original_error: Some("KeyError".into()),
    };
    let msg = err.to_string();
    assert!(msg.contains("[FOR]"));
    assert!(msg.contains("2/10"), "want iteration_index 2/10 in: {msg}");
}

#[test]
fn op_error_prompt_missing_vars_render() {
    let err = OpError::Prompt {
        message: "Missing template variable".into(),
        template_type: "str".into(),
        template: "Hello {name}, your order {order_id}".into(),
        missing_vars: vec!["order_id".into()],
        original_error: None,
    };
    let msg = err.to_string();
    assert!(msg.contains("[PROMPT]"));
    assert!(msg.contains("missing_vars"));
    assert!(msg.contains("order_id"));
}

#[test]
fn op_error_embedding_includes_resource_and_count() {
    let err = OpError::Embedding {
        message: "Backend failed".into(),
        resource: "bge-m3".into(),
        text_count: 100,
        original_error: Some("ConnectionError".into()),
    };
    let msg = err.to_string();
    assert!(msg.contains("[EMBEDDING]"));
    assert!(msg.contains("bge-m3"));
    assert!(msg.contains("text_count: 100"));
}

#[test]
fn op_error_rerank_includes_resource_query_count() {
    let err = OpError::Rerank {
        message: "Invalid document type".into(),
        resource: "bge-m3".into(),
        query: "search query".into(),
        document_count: 50,
        original_error: Some("TypeError".into()),
    };
    let msg = err.to_string();
    assert!(msg.contains("[RERANK]"));
    assert!(msg.contains("bge-m3"));
    assert!(msg.contains("search query"));
    assert!(msg.contains("document_count: 50"));
}

// ============================================================
// Discriminator methods — kind() / tag() — used by parity tests
// ============================================================

#[test]
fn op_error_kind_discriminator() {
    let cases: Vec<(OpError, &str)> = vec![
        (OpError::parser_msg("x", "json"), "parser"),
        (OpError::code_msg("x", "f"), "code"),
        (OpError::branch_msg("x", "c"), "branch"),
        (
            OpError::Condition {
                message: "x".into(),
                condition: "c".into(),
                inputs: BTreeMap::new(),
                iteration: None,
                phase: "eval".into(),
                original_error: None,
            },
            "while",
        ),
        (
            OpError::Iteration {
                message: "x".into(),
                iteration_index: 0,
                loop_data: BTreeMap::new(),
                total_iterations: 1,
                op_type: "for".into(),
                original_error: None,
            },
            "for",
        ),
        (OpError::prompt_msg("x"), "prompt"),
        (OpError::embedding_msg("x", "r", 1), "embedding"),
        (OpError::rerank_msg("x", "r", 1), "rerank"),
    ];
    for (err, expected) in cases {
        assert_eq!(err.kind(), expected, "kind() mismatch for {err:?}");
    }
}

#[test]
fn op_error_pattern_match_via_matches_macro() {
    let err = OpError::parser_msg("x", "json");
    assert!(matches!(err, OpError::Parser { .. }));
    assert!(!matches!(err, OpError::Code { .. }));
}

// ============================================================
// OperonError integration — From<OpError>, pattern match through
// ============================================================

#[test]
fn operon_error_from_op_error_preserves_variant() {
    let op_err = OpError::parser_msg("invalid", "json");
    let err: OperonError = op_err.into();
    assert!(matches!(err, OperonError::Op(OpError::Parser { .. })));
}

#[test]
fn operon_error_display_through_op_wrapper_shows_tag() {
    let err: OperonError = OpError::parser_msg("invalid", "json").into();
    let msg = err.to_string();
    assert!(msg.contains("[PARSER]"), "want [PARSER] through OperonError: {msg}");
    assert!(msg.contains("invalid"));
}
