//! Rush benchmark runner — standalone binary for Python↔Rust comparison.
//!
//! Reads JSON benchmark requests from stdin, runs them through Rush::run_json(),
//! and outputs timing results as JSON to stdout.
//!
//! Protocol (one JSON object per line on stdin):
//!   {"config": <graph_config>, "inputs": <inputs>, "warmup": 5, "runs": 200}
//!
//! Output (one JSON object per line on stdout):
//!   {"mean_ms": f64, "p50_ms": f64, "p99_ms": f64, "min_ms": f64, "max_ms": f64}

use std::io::{self, BufRead, Write};
use std::sync::Arc;
use std::time::Instant;

use rush_core::engine::Rush;
use rush_core::registry::OpRegistry;
use serde_json::{json, Value};

// =============================================================================
// Benchmark ops (matching the Python @op(rust=...) definitions in bench_e2e.py)
// =============================================================================

fn bench_noop(inputs: &Value) -> Value {
    let x = inputs.get("x").cloned().unwrap_or(Value::Null);
    json!({"x": x})
}

fn classify(inputs: &Value) -> Value {
    let score = inputs["score"].as_i64().unwrap_or(0);
    let grade = if score >= 90 {
        "excellent"
    } else if score >= 70 {
        "good"
    } else if score >= 50 {
        "average"
    } else {
        "fail"
    };
    json!({"grade": grade, "score": score})
}

fn process_grade(inputs: &Value) -> Value {
    let grade = inputs["grade"].as_str().unwrap_or("");
    let score = inputs["score"].as_i64().unwrap_or(0);
    json!({"result": format!("{}:{}", grade, score)})
}

fn aggregate(inputs: &Value) -> Value {
    let results = inputs["results"].as_array();
    let len = results.map(|a| a.len()).unwrap_or(0);
    json!({"summary": len})
}

fn bench_transform(inputs: &Value) -> Value {
    let item = inputs["item"].as_str().unwrap_or("");
    let prefix = inputs["prefix"].as_str().unwrap_or("");
    json!({"output": format!("{}-{}", prefix, item)})
}

fn merge_two(inputs: &Value) -> Value {
    let a = inputs.get("a").cloned().unwrap_or(Value::Null);
    json!({"merged": a, "x": a})
}

fn combine_all(inputs: &Value) -> Value {
    let mut parts = Vec::new();
    for key in &["r1", "r2", "r3", "r4", "r5"] {
        if let Some(v) = inputs.get(*key) {
            if !v.is_null() {
                parts.push(v.clone());
            }
        }
    }
    let count = parts.len();
    json!({"combined": parts, "count": count})
}

fn cpu_hash_chain(inputs: &Value) -> Value {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};

    let x = inputs["x"].as_i64().unwrap_or(0);
    let iterations = inputs["iterations"].as_i64().unwrap_or(1000) as usize;
    let mut val = x as u64;
    for _ in 0..iterations {
        let mut hasher = DefaultHasher::new();
        val.hash(&mut hasher);
        val = hasher.finish();
    }
    json!({"hash": format!("{:016x}", val), "x": x})
}

fn cpu_prime_sieve(inputs: &Value) -> Value {
    let limit = inputs["limit"].as_i64().unwrap_or(1000) as usize;
    let mut sieve = vec![true; limit + 1];
    if limit >= 1 {
        sieve[0] = false;
        if limit >= 2 {
            sieve[1] = false;
        }
    }
    let sqrt_limit = (limit as f64).sqrt() as usize;
    for i in 2..=sqrt_limit {
        if sieve[i] {
            let mut j = i * i;
            while j <= limit {
                sieve[j] = false;
                j += i;
            }
        }
    }
    let count: usize = sieve.iter().filter(|&&b| b).count();
    json!({"prime_count": count})
}

fn cpu_matrix_mult(inputs: &Value) -> Value {
    let size = inputs["size"].as_i64().unwrap_or(10) as usize;
    let mut a = vec![vec![0.0f64; size]; size];
    let mut b = vec![vec![0.0f64; size]; size];
    let mut c = vec![vec![0.0f64; size]; size];
    for i in 0..size {
        for j in 0..size {
            a[i][j] = (i + j) as f64;
            b[i][j] = (i * j + 1) as f64;
        }
    }
    for i in 0..size {
        for j in 0..size {
            let mut s = 0.0;
            for k in 0..size {
                s += a[i][k] * b[k][j];
            }
            c[i][j] = s;
        }
    }
    let trace: f64 = (0..size).map(|i| c[i][i]).sum();
    json!({"trace": trace})
}

fn cpu_fibonacci(inputs: &Value) -> Value {
    let n = inputs["n"].as_i64().unwrap_or(10) as u64;
    let modulo: u64 = 1_000_000_007;
    let mut a: u64 = 0;
    let mut b: u64 = 1;
    for _ in 0..n {
        let tmp = b;
        b = (a + b) % modulo;
        a = tmp;
    }
    json!({"fib": a})
}

// =============================================================================
// BenchRegistry — implements OpRegistry for benchmark ops
// =============================================================================

struct BenchRegistry;

impl OpRegistry for BenchRegistry {
    fn call(&self, name: &str, inputs: &Value) -> Option<Value> {
        let result = match name {
            "bench_noop" => bench_noop(inputs),
            "classify" => classify(inputs),
            "process_grade" => process_grade(inputs),
            "aggregate" => aggregate(inputs),
            "bench_transform" => bench_transform(inputs),
            "merge_two" => merge_two(inputs),
            "combine_all" => combine_all(inputs),
            "cpu_hash_chain" => cpu_hash_chain(inputs),
            "cpu_prime_sieve" => cpu_prime_sieve(inputs),
            "cpu_matrix_mult" => cpu_matrix_mult(inputs),
            "cpu_fibonacci" => cpu_fibonacci(inputs),
            _ => return None,
        };
        Some(result)
    }

    fn call_generator(&self, _name: &str, _inputs: &Value) -> Option<Vec<Value>> {
        None
    }
}

// =============================================================================
// Main
// =============================================================================

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut stdout = stdout.lock();

    let registry = Arc::new(BenchRegistry);

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };

        if line.trim().is_empty() {
            continue;
        }

        let request: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(e) => {
                let err = json!({"error": format!("Invalid JSON: {}", e)});
                let _ = writeln!(stdout, "{}", err);
                let _ = stdout.flush();
                continue;
            }
        };

        let config = &request["config"];
        let inputs = request["inputs"].clone();
        let warmup = request["warmup"].as_u64().unwrap_or(5) as usize;
        let runs = request["runs"].as_u64().unwrap_or(200) as usize;

        let config_json = serde_json::to_string(config).unwrap();

        // Create engine with BenchRegistry
        let mut engine = match Rush::new(&config_json) {
            Ok(e) => e,
            Err(e) => {
                let err = json!({"error": format!("Rush::new failed: {}", e)});
                let _ = writeln!(stdout, "{}", err);
                let _ = stdout.flush();
                continue;
            }
        };
        engine.set_registry(registry.clone());

        // Warmup
        for _ in 0..warmup {
            let _ = engine.run_json(inputs.clone(), None, None, None);
        }

        // Timed runs
        let mut times_ns: Vec<u128> = Vec::with_capacity(runs);
        for _ in 0..runs {
            let start = Instant::now();
            let _ = engine.run_json(inputs.clone(), None, None, None);
            times_ns.push(start.elapsed().as_nanos());
        }

        // Compute statistics
        let times_ms: Vec<f64> = times_ns.iter().map(|&ns| ns as f64 / 1_000_000.0).collect();
        let mut sorted = times_ms.clone();
        sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());

        let mean = times_ms.iter().sum::<f64>() / times_ms.len() as f64;
        let p50 = sorted[sorted.len() / 2];
        let p99_idx = ((sorted.len() as f64) * 0.99) as usize;
        let p99 = sorted[p99_idx.min(sorted.len() - 1)];
        let min = sorted[0];
        let max = sorted[sorted.len() - 1];

        let result = json!({
            "mean_ms": mean,
            "p50_ms": p50,
            "p99_ms": p99,
            "min_ms": min,
            "max_ms": max,
        });

        let _ = writeln!(stdout, "{}", result);
        let _ = stdout.flush();
    }
}
