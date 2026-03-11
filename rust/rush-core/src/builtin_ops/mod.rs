//! Builtin op registry — dispatches op names to built-in op functions.
//!
//! All ops are defined in the `ops` submodule as plain functions.
//! No external crate, no FFI, no dynamic loading.

mod ops;

use serde_json::Value;

/// Call a builtin generator op by name.
/// Returns `Some(Vec<Value>)` — each item is one yield.
/// Returns `None` if the name is not a known generator.
pub fn call_generator(name: &str, inputs: &Value) -> Option<Vec<Value>> {
    let result = match name {
        "chunk_text" => ops::chunk_text(inputs),
        "range_gen" => ops::range_gen(inputs),
        // Example 05 generators
        "each_item_with_prefix" => ops::each_item_with_prefix(inputs),
        "each_number" => ops::each_number(inputs),
        "halve_until" => ops::halve_until(inputs),
        _ => return None,
    };
    Some(result)
}

/// Call a builtin op by name. Returns `Some(result)` if found, `None` if unknown.
pub fn call(name: &str, inputs: &Value) -> Option<Value> {
    let result = match name {
        // Core ops
        "double" => ops::double(inputs),
        "add" => ops::add(inputs),
        "hash_chain" => ops::hash_chain(inputs),
        "identity" => ops::identity(inputs),
        "multiply" => ops::multiply(inputs),
        "square" => ops::square(inputs),
        "increment" => ops::increment(inputs),
        "greet" => ops::greet(inputs),
        "greet_vi" => ops::greet_vi(inputs),
        "make_dict" => ops::make_dict(inputs),
        "to_upper" => ops::to_upper(inputs),
        "join_strings" => ops::join_strings(inputs),
        "dbl" => ops::dbl(inputs),
        "ok_op" => ops::ok_op(inputs),
        "make_list" => ops::make_list(inputs),
        "increment_counter" => ops::increment_counter(inputs),
        "accumulate" => ops::accumulate(inputs),
        "fib_step" => ops::fib_step(inputs),
        "tagged_op" => ops::tagged_op(inputs),
        "safe_op" => ops::safe_op(inputs),
        "multiply_values" => ops::multiply_values(inputs),
        "noop" => ops::noop(inputs),
        "passthrough" => ops::passthrough(inputs),
        "fail_op" => ops::fail_op(inputs),
        // String ops
        "string_concat" => ops::string_concat(inputs),
        "string_split" => ops::string_split(inputs),
        "string_template" => ops::string_template(inputs),
        // JSON ops
        "json_parse" => ops::json_parse(inputs),
        "json_extract" => ops::json_extract(inputs),
        "json_merge" => ops::json_merge(inputs),
        // Math ops
        "math_sum" => ops::math_sum(inputs),
        "math_mean" => ops::math_mean(inputs),
        "math_max" => ops::math_max(inputs),
        "math_min" => ops::math_min(inputs),
        // Benchmark ops
        "bench_noop" => ops::bench_noop(inputs),
        "classify" => ops::classify(inputs),
        "process_grade" => ops::process_grade(inputs),
        "aggregate" => ops::aggregate(inputs),
        "bench_transform" => ops::bench_transform(inputs),
        "merge_two" => ops::merge_two(inputs),
        "combine_all" => ops::combine_all(inputs),
        "cpu_hash_chain" => ops::cpu_hash_chain(inputs),
        "cpu_prime_sieve" => ops::cpu_prime_sieve(inputs),
        "cpu_matrix_mult" => ops::cpu_matrix_mult(inputs),
        "cpu_fibonacci" => ops::cpu_fibonacci(inputs),
        // Test support ops
        "grade_a" => ops::grade_a(inputs),
        "grade_b" => ops::grade_b(inputs),
        "grade_f" => ops::grade_f(inputs),
        "grade_a_mult" => ops::grade_a_mult(inputs),
        "grade_f_mult" => ops::grade_f_mult(inputs),
        "prefix" => ops::prefix(inputs),
        "sum_all" => ops::sum_all(inputs),
        "classify_value" => ops::classify_value(inputs),
        "multi_output" => ops::multi_output(inputs),
        "multi" => ops::multi(inputs),
        "compute" => ops::compute(inputs),
        "process_list" => ops::process_list(inputs),
        "measure" => ops::measure(inputs),
        "append_char" => ops::append_char(inputs),
        "collect_op" => ops::collect_op(inputs),
        "make_start" => ops::make_start(inputs),
        "format_grade" => ops::format_grade(inputs),
        "multiply_vf" => ops::multiply_vf(inputs),
        "accumulate_step" => ops::accumulate_step(inputs),
        "raise_op" => ops::raise_op(inputs),
        "maybe_fail" => ops::maybe_fail(inputs),
        // Example 05 ops
        "process_item_text" => ops::process_item_text(inputs),
        "square_named" => ops::square_named(inputs),
        "excellent" => ops::excellent(inputs),
        "good" => ops::good(inputs),
        "average_grade" => ops::average_grade(inputs),
        "fail_grade" => ops::fail_grade(inputs),
        // Pipeline ops (examples 01 & 02)
        "step_a" => ops::step_a(inputs),
        "step_b" => ops::step_b(inputs),
        "fetch_data" => ops::fetch_data(inputs),
        "transform_double" => ops::transform_double(inputs),
        "aggregate_data" => ops::aggregate_data(inputs),
        "clean_text" => ops::clean_text(inputs),
        "count_words" => ops::count_words(inputs),
        "summarize_stats" => ops::summarize_stats(inputs),
        _ => return None,
    };
    Some(result)
}
