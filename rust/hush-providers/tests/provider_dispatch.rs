//! Integration tests for provider op dispatch — config validation, transform ops,
//! and type mismatch errors. No HTTP calls.
//!
//! Covers:
//! - execute_transform (prompt, parser) — CPU-only, no config needed
//! - Config type mismatch errors (wrong ProviderConfig variant for op_type)
//! - Unknown op_type errors
//! - is_native_provider_op / is_native_transform_op detection

use hush_providers::config::embedding::EmbeddingConfig;
use hush_providers::config::reranking::RerankingConfig;
use hush_providers::config::{LLMProviderConfig, ProviderConfig};
use hush_providers::ops;
use serde_json::json;

// =============================================================================
// execute_transform: prompt ops (no HTTP, no config)
// =============================================================================

#[test]
fn test_transform_prompt_string_template() {
    let result = ops::execute_transform(
        "prompt",
        json!({
            "template": "Hello {name}, you are {role}.",
            "name": "Alice",
            "role": "admin"
        }),
    )
    .unwrap();

    let messages = result["messages"].as_array().unwrap();
    assert_eq!(messages.len(), 1);
    assert_eq!(messages[0]["role"], "user");
    assert_eq!(messages[0]["content"], "Hello Alice, you are admin.");
}

#[test]
fn test_transform_prompt_dict_template() {
    let result = ops::execute_transform(
        "prompt",
        json!({
            "template": {
                "system": "You are a {role}.",
                "user": "Tell me about {topic}."
            },
            "role": "helpful assistant",
            "topic": "Rust"
        }),
    )
    .unwrap();

    let messages = result["messages"].as_array().unwrap();
    assert_eq!(messages.len(), 2);
    assert_eq!(messages[0]["role"], "system");
    assert_eq!(messages[0]["content"], "You are a helpful assistant.");
    assert_eq!(messages[1]["role"], "user");
    assert_eq!(messages[1]["content"], "Tell me about Rust.");
}

#[test]
fn test_transform_prompt_with_history() {
    let result = ops::execute_transform(
        "prompt",
        json!({
            "template": {
                "system": "You are helpful.",
                "user": "Current question"
            },
            "conversation_history": [
                {"role": "user", "content": "Previous question"},
                {"role": "assistant", "content": "Previous answer"}
            ]
        }),
    )
    .unwrap();

    let messages = result["messages"].as_array().unwrap();
    assert_eq!(messages.len(), 4);
    assert_eq!(messages[0]["role"], "system");
    assert_eq!(messages[1]["content"], "Previous question");
    assert_eq!(messages[2]["content"], "Previous answer");
    assert_eq!(messages[3]["content"], "Current question");
}

// =============================================================================
// execute_transform: parser ops (no HTTP, no config)
// =============================================================================

// Parser tests moved to hush-icore (ops/transform/parser_op.rs)
// since ParserOp now lives in hush-icore, matching Python.

#[test]
fn test_transform_parser_json_via_icore() {
    let result = hush_icore::ops::transform::parser_op::execute(json!({
        "text": r#"{"name": "Alice", "age": 30}"#,
        "parser_format": "json",
        "parser_extract": ["name: str", "age: int"]
    }))
    .unwrap();

    assert_eq!(result["name"], "Alice");
    assert_eq!(result["age"], 30);
}

#[test]
fn test_transform_parser_xml_via_icore() {
    let result = hush_icore::ops::transform::parser_op::execute(json!({
        "text": "<answer>42</answer><confident>true</confident>",
        "parser_format": "xml",
        "parser_extract": ["answer: int", "confident: bool"]
    }))
    .unwrap();

    assert_eq!(result["answer"], 42);
    assert_eq!(result["confident"], true);
}

#[test]
fn test_transform_parser_yaml_via_icore() {
    let result = hush_icore::ops::transform::parser_op::execute(json!({
        "text": "name: Alice\nscore: 95.5",
        "parser_format": "yaml",
        "parser_extract": ["name: str", "score: float"]
    }))
    .unwrap();

    assert_eq!(result["name"], "Alice");
    assert_eq!(result["score"], 95.5);
}

// =============================================================================
// Unknown op_type errors
// =============================================================================

#[test]
fn test_transform_unknown_op_type() {
    let result = ops::execute_transform("unknown", json!({"text": "hello"}));
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(
        err.message.contains("Unknown transform op type"),
        "Expected 'Unknown transform op type' error, got: {}",
        err.message
    );
}

#[tokio::test]
async fn test_execute_unknown_op_type() {
    let config = ProviderConfig::Embedding(EmbeddingConfig {
        api_type: "openai".to_string(),
        api_key: None,
        base_url: None,
        model: None,
        embed_batch_size: None,
        dimensions: None,
    });
    let result = ops::execute("unknown_op", json!({}), &config).await;
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(
        err.message.contains("Unknown provider op type"),
        "Expected 'Unknown provider op type' error, got: {}",
        err.message
    );
}

// =============================================================================
// Config type mismatch — LLM op with Embedding config
// =============================================================================

#[tokio::test]
async fn test_llm_op_with_embedding_config_fails() {
    let config = ProviderConfig::Embedding(EmbeddingConfig {
        api_type: "openai".to_string(),
        api_key: None,
        base_url: None,
        model: None,
        embed_batch_size: None,
        dimensions: None,
    });
    let result = ops::execute("llm", json!({"messages": []}), &config).await;
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(
        err.message.contains("LLM op requires LLM provider config"),
        "Expected LLM config mismatch error, got: {}",
        err.message
    );
}

#[tokio::test]
async fn test_embedding_op_with_reranking_config_fails() {
    let config = ProviderConfig::Reranking(RerankingConfig {
        api_type: "cohere".to_string(),
        api_key: None,
        api_version: None,
        base_url: None,
        model: None,
    });
    let result = ops::execute("embedding", json!({"texts": ["hello"]}), &config).await;
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(
        err.message
            .contains("Embedding op requires Embedding provider config"),
        "Expected Embedding config mismatch error, got: {}",
        err.message
    );
}

#[tokio::test]
async fn test_rerank_op_with_llm_config_fails() {
    let config = ProviderConfig::LLM(LLMProviderConfig {
        configs: vec![],
        resources: vec![],
        ratios: vec![],
        fallback_configs: vec![],
        fallback: vec![],
        batch_mode: false,
    });
    let result = ops::execute(
        "rerank",
        json!({"query": "test", "documents": ["a", "b"]}),
        &config,
    )
    .await;
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(
        err.message
            .contains("Rerank op requires Reranking provider config"),
        "Expected Reranking config mismatch error, got: {}",
        err.message
    );
}