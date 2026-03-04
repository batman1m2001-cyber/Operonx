//! Builtin op registry — dispatches op names to rush-ops-builtin functions.
//!
//! Direct function calls (rlib), no FFI or dynamic loading.

use serde_json::Value;

/// Call a builtin op by name. Returns `Some(result)` if found, `None` if unknown.
pub fn call(name: &str, inputs: &Value) -> Option<Value> {
    let result = match name {
        // Core ops
        "double" => rush_ops_builtin::double(inputs),
        "add" => rush_ops_builtin::add(inputs),
        "hash_chain" => rush_ops_builtin::hash_chain(inputs),
        "identity" => rush_ops_builtin::identity(inputs),
        "multiply" => rush_ops_builtin::multiply(inputs),
        "square" => rush_ops_builtin::square(inputs),
        "increment" => rush_ops_builtin::increment(inputs),
        "greet" => rush_ops_builtin::greet(inputs),
        "make_dict" => rush_ops_builtin::make_dict(inputs),
        "to_upper" => rush_ops_builtin::to_upper(inputs),
        "join_strings" => rush_ops_builtin::join_strings(inputs),
        "dbl" => rush_ops_builtin::dbl(inputs),
        "ok_op" => rush_ops_builtin::ok_op(inputs),
        "make_list" => rush_ops_builtin::make_list(inputs),
        "increment_counter" => rush_ops_builtin::increment_counter(inputs),
        "accumulate" => rush_ops_builtin::accumulate(inputs),
        "fib_step" => rush_ops_builtin::fib_step(inputs),
        "tagged_op" => rush_ops_builtin::tagged_op(inputs),
        "safe_op" => rush_ops_builtin::safe_op(inputs),
        "multiply_values" => rush_ops_builtin::multiply_values(inputs),
        "noop" => rush_ops_builtin::noop(inputs),
        "passthrough" => rush_ops_builtin::passthrough(inputs),
        "fail_op" => rush_ops_builtin::fail_op(inputs),
        // String ops
        "string_concat" => rush_ops_builtin::string_concat(inputs),
        "string_split" => rush_ops_builtin::string_split(inputs),
        "string_template" => rush_ops_builtin::string_template(inputs),
        // JSON ops
        "json_parse" => rush_ops_builtin::json_parse(inputs),
        "json_extract" => rush_ops_builtin::json_extract(inputs),
        "json_merge" => rush_ops_builtin::json_merge(inputs),
        // Math ops
        "math_sum" => rush_ops_builtin::math_sum(inputs),
        "math_mean" => rush_ops_builtin::math_mean(inputs),
        "math_max" => rush_ops_builtin::math_max(inputs),
        "math_min" => rush_ops_builtin::math_min(inputs),
        // Benchmark ops
        "bench_noop" => rush_ops_builtin::bench_noop(inputs),
        "classify" => rush_ops_builtin::classify(inputs),
        "process_grade" => rush_ops_builtin::process_grade(inputs),
        "aggregate" => rush_ops_builtin::aggregate(inputs),
        "bench_transform" => rush_ops_builtin::bench_transform(inputs),
        "merge_two" => rush_ops_builtin::merge_two(inputs),
        "combine_all" => rush_ops_builtin::combine_all(inputs),
        "cpu_hash_chain" => rush_ops_builtin::cpu_hash_chain(inputs),
        "cpu_prime_sieve" => rush_ops_builtin::cpu_prime_sieve(inputs),
        "cpu_matrix_mult" => rush_ops_builtin::cpu_matrix_mult(inputs),
        "cpu_fibonacci" => rush_ops_builtin::cpu_fibonacci(inputs),
        // Test support ops
        "grade_a" => rush_ops_builtin::grade_a(inputs),
        "grade_b" => rush_ops_builtin::grade_b(inputs),
        "grade_f" => rush_ops_builtin::grade_f(inputs),
        "grade_a_mult" => rush_ops_builtin::grade_a_mult(inputs),
        "grade_f_mult" => rush_ops_builtin::grade_f_mult(inputs),
        "prefix" => rush_ops_builtin::prefix(inputs),
        "sum_all" => rush_ops_builtin::sum_all(inputs),
        "classify_value" => rush_ops_builtin::classify_value(inputs),
        "multi_output" => rush_ops_builtin::multi_output(inputs),
        "multi" => rush_ops_builtin::multi(inputs),
        "compute" => rush_ops_builtin::compute(inputs),
        "process_list" => rush_ops_builtin::process_list(inputs),
        "measure" => rush_ops_builtin::measure(inputs),
        "append_char" => rush_ops_builtin::append_char(inputs),
        "collect_op" => rush_ops_builtin::collect_op(inputs),
        "make_start" => rush_ops_builtin::make_start(inputs),
        "format_grade" => rush_ops_builtin::format_grade(inputs),
        "multiply_vf" => rush_ops_builtin::multiply_vf(inputs),
        "accumulate_step" => rush_ops_builtin::accumulate_step(inputs),
        "raise_op" => rush_ops_builtin::raise_op(inputs),
        "maybe_fail" => rush_ops_builtin::maybe_fail(inputs),
        _ => return None,
    };
    Some(result)
}
