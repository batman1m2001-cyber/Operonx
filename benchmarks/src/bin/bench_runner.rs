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
use std::time::Instant;

use rush_core::engine::Rush;
use serde_json::Value;

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut stdout = stdout.lock();

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
                let err = serde_json::json!({"error": format!("Invalid JSON: {}", e)});
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

        // Create engine
        let engine = match Rush::new(&config_json) {
            Ok(e) => e,
            Err(e) => {
                let err = serde_json::json!({"error": format!("Rush::new failed: {}", e)});
                let _ = writeln!(stdout, "{}", err);
                let _ = stdout.flush();
                continue;
            }
        };

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

        let result = serde_json::json!({
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
