//! Example Rust ops server for Hush workflows.
//!
//! Demonstrates building a custom binary with hush-serve as a library.
//! All ops are compiled in — no cdylib/FFI plugin needed.
//!
//! ## Build & Run
//!
//! ```bash
//! cd examples/rust_ops && cargo build --release
//! ```
//!
//! ## Project structure convention
//!
//! ```text
//! rust_ops/
//! ├── Cargo.toml
//! └── src/
//!     ├── main.rs
//!     ├── macros.rs
//!     ├── ex01_hello_world/       (greet, upper, merge, ...)
//!     ├── ex02_data_pipeline/     (fetch_data, transform, aggregate, ...)
//!     ├── ex03_llm_chat/          (clean_text is in ex02)
//!     ├── ex04_llm_advanced/      (process_response, update_history)
//!     ├── ex05_loops_and_branches/(each_item, square, excellent, ...)
//!     ├── ex06_tracing/           (analyze_text, classify)
//!     ├── ex07_embeddings_and_rag/(retrieve)
//!     ├── ex08_error_handling/    (failing, safe_divide, retry_with_backoff, ...)
//!     ├── ex09_agent_workflow/    (init_agent, process_response)
//!     ├── ex10_multi_model/       (is_simple, compare, select)
//!     ├── ex11_parallel_advanced/ (analyze_sentiment, extract_keywords, ...)
//!     ├── ex12_rag_advanced/      (search_original, search_expanded, rrf_merge)
//!     ├── ex13_graph/             (double, add)
//!     ├── ex14_streaming_tracing/ (chunk_text, analyze_chunk, async_counter, ...)
//!     └── ex15_callbot_streaming/ (customer_audio, vad, stt, tts, ...)
//! ```

mod ex01_hello_world;
mod ex02_data_pipeline;
mod ex03_llm_chat;
mod ex04_llm_advanced;
mod ex05_loops_and_branches;
mod ex06_tracing;
mod ex07_embeddings_and_rag;
mod ex08_error_handling;
mod ex09_agent_workflow;
mod ex10_multi_model;
mod ex11_parallel_advanced;
mod ex12_rag_advanced;
mod ex13_graph;
mod ex14_streaming_tracing;
mod ex15_callbot_streaming;

fn main() {
    init_logging();

    if let Err(e) = hush_serve::HushServer::builder()
        .auto_register()
        .from_cli()
        .serve()
    {
        eprintln!("Error: {}", e);
        std::process::exit(1);
    }
}

fn init_logging() {
    if std::env::var("RUST_LOG").is_ok() {
        env_logger::init();
    } else {
        let filter = match std::env::var("LOG_LEVEL") {
            Ok(level) => match level.to_uppercase().as_str() {
                "DEBUG" => "hush=debug",
                "INFO" => "hush=info",
                "WARNING" | "WARN" => "hush=warn",
                "ERROR" | "CRITICAL" => "hush=error",
                _ => "hush=warn",
            }
            .to_string(),
            Err(_) => "hush=warn".to_string(),
        };
        env_logger::Builder::new().parse_filters(&filter).init();
    }
}
