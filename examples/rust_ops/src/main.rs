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
//! my-project/
//! ├── pyproject.toml              # Python workflows
//! ├── workflows/
//! │   ├── pipeline.py
//! │   └── agent.py
//! ├── rust_ops/                   # ONE crate for all custom Rust ops
//! │   ├── Cargo.toml
//! │   └── src/
//! │       ├── main.rs             # Server entry point (uses hush-serve lib)
//! │       ├── math.rs             # Domain: math operations
//! │       ├── text.rs             # Domain: text processing
//! │       └── crypto.rs           # Domain: CPU-heavy crypto
//! ```

mod analytics;
mod crypto;
mod iteration;
mod math;
mod pipeline;
mod search;
mod streaming;
mod text;

fn main() {
    init_logging();

    if let Err(e) = hush_serve::hush_ops!(hush_serve::HushServer::builder();
        ops: {
            // analytics
            "analyze_sentiment" => analytics::analyze_sentiment,
            "count_stats" => analytics::count_stats,
            "count_words" => analytics::count_words,
            "summarize_stats" => analytics::summarize_stats,
            "aggregate_stats" => analytics::aggregate_stats,
            "classify_by_count" => analytics::classify_by_count,
            "classify_by_score" => analytics::classify_by_score,
            "analyze_text" => analytics::analyze_text,
            "tokenize" => analytics::tokenize,
            "analyze_chunk" => analytics::analyze_chunk,
            "classify_intent" => analytics::classify_intent,
            "aggregate_data" => analytics::aggregate_data,
            "summarize_products" => analytics::summarize_products,
            // crypto
            "hash_chain" => crypto::hash_chain,
            // math
            "double" => math::double,
            "add" => math::add,
            "multiply" => math::multiply,
            "square" => math::square,
            "math_sum" => math::math_sum,
            "math_mean" => math::math_mean,
            "square_named" => math::square_named,
            "multiply_xy" => math::multiply_xy,
            "multiply_xy_tagged" => math::multiply_xy_tagged,
            "safe_divide" => math::safe_divide,
            "score_token" => math::score_token,
            "sum_list" => math::sum_list,
            "calc" => math::calc,
            // pipeline
            "step_a" => pipeline::step_a,
            "step_b" => pipeline::step_b,
            "merge_two" => pipeline::merge_two,
            "fetch_data" => pipeline::fetch_data,
            "transform_double" => pipeline::transform_double,
            "merge_analysis" => pipeline::merge_analysis,
            "merge_results" => pipeline::merge_results,
            "safe_process" => pipeline::safe_process,
            "filter_results" => pipeline::filter_results,
            "process_response_tool" => pipeline::process_response_tool,
            "update_history" => pipeline::update_history,
            "validate_input" => pipeline::validate_input,
            "with_fallback" => pipeline::with_fallback,
            "init_agent" => pipeline::init_agent,
            "process_agent_response" => pipeline::process_agent_response,
            "excellent" => pipeline::excellent,
            "good" => pipeline::good,
            "average_grade" => pipeline::average_grade,
            "fail_grade" => pipeline::fail_grade,
            "get_config" => pipeline::get_config,
            "handle_intent" => pipeline::handle_intent,
            "process_item_squared" => pipeline::process_item_squared,
            "select_answer" => pipeline::select_answer,
            "failing_op" => pipeline::failing_op,
            "retry_with_backoff" => pipeline::retry_with_backoff,
            // search
            "keyword_search" => search::keyword_search,
            "keyword_search_expanded" => search::keyword_search_expanded,
            "rrf_merge" => search::rrf_merge,
            "cosine_search" => search::cosine_search,
            "keyword_retrieve" => search::keyword_retrieve,
            "retrieve" => search::retrieve,
            // streaming
            "stt" => streaming::stt,
            // text
            "greet" => text::greet,
            "to_upper" => text::to_upper,
            "string_template" => text::string_template,
            "string_concat" => text::string_concat,
            "greet_vi" => text::greet_vi,
            "clean_text" => text::clean_text,
            "preprocess" => text::preprocess,
            "add_prefix" => text::add_prefix,
            "process_item_text" => text::process_item_text,
            "handle_success" => text::handle_success,
            "handle_error" => text::handle_error,
            "extract_keywords" => text::extract_keywords,
            "grade_to_message" => text::grade_to_message,
            "format_labeled" => text::format_labeled,
        },
        generators: {
            // iteration
            "each_item" => iteration::each_item,
            "each_item_with_prefix" => iteration::each_item_with_prefix,
            "each_number" => iteration::each_number,
            "each_token" => iteration::each_token,
            "each_x" => iteration::each_x,
            "each_y" => iteration::each_y,
            "each_fruit" => iteration::each_fruit,
            "each_value" => iteration::each_value,
            "halve_until" => iteration::halve_until,
            "halve_until_threshold" => iteration::halve_until_threshold,
            "outer_iter" => iteration::outer_iter,
            "inner_iter" => iteration::inner_iter,
            "each_query" => iteration::each_query,
            "each_outer" => iteration::each_outer,
            "each_inner" => iteration::each_inner,
            "emit_items" => iteration::emit_items,
            // streaming
            "chunk_text" => streaming::chunk_text,
            "async_counter" => streaming::async_counter,
            "customer_audio" => streaming::customer_audio,
            "vad" => streaming::vad,
            "tts" => streaming::tts,
        }
    )
    .from_cli()
    .serve() {
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
