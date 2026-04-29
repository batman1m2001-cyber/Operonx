//! Operonx Rust e2e bench — runs each `<name>.graph.json` produced by
//! `scripts/bench/generate.py` through the Rust engine, reports timing
//! stats per pattern.
//!
//! Usage (from the repo root):
//!
//! ```sh
//! cd scripts/bench && cargo run --release
//! cd scripts/bench && cargo run --release -- --runs 50 --warmup 3
//! ```

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

use operonx::{op, Operon};
use serde_json::{Map, Value};

// ── Bench ops — names must match the Python @op definitions in generate.py ─

#[op(name = "bench_noop")]
fn bench_noop(x: i64) -> Value {
    serde_json::json!({ "x": x })
}

#[op(name = "bench_fib")]
fn bench_fib(x: i64, fib_n: i64) -> Value {
    let modulo: u64 = 1_000_000_007;
    let mut a: u64 = 0;
    let mut b: u64 = 1;
    for _ in 0..fib_n {
        let tmp = b;
        b = (a + b) % modulo;
        a = tmp;
    }
    serde_json::json!({ "x": x, "fib": a })
}

#[op(name = "bench_matrix")]
fn bench_matrix(x: i64, size: i64) -> Value {
    let n = size.max(0) as usize;
    let a: Vec<Vec<f64>> = (0..n)
        .map(|i| (0..n).map(|j| (i + j) as f64).collect())
        .collect();
    let b: Vec<Vec<f64>> = (0..n)
        .map(|i| (0..n).map(|j| (i * j + 1) as f64).collect())
        .collect();
    let mut c = vec![vec![0.0; n]; n];
    for i in 0..n {
        for j in 0..n {
            let mut s = 0.0;
            for k in 0..n {
                s += a[i][k] * b[k][j];
            }
            c[i][j] = s;
        }
    }
    let trace: f64 = (0..n).map(|i| c[i][i]).sum();
    serde_json::json!({ "x": x, "trace": trace })
}

#[op(name = "combine_all")]
fn combine_all(_inputs: &Value) -> Value {
    // Aggregate up to five branch values; ignore those that aren't present.
    let mut parts = Vec::with_capacity(5);
    for key in &["r1", "r2", "r3", "r4", "r5"] {
        if let Some(v) = _inputs.get(*key) {
            if !v.is_null() {
                parts.push(v.clone());
            }
        }
    }
    let count = parts.len();
    serde_json::json!({ "combined": parts, "count": count })
}

#[op(name = "merge_two")]
fn merge_two(a: Value, b: Value) -> Value {
    serde_json::json!({ "merged": a.clone(), "x": a, "b": b })
}

#[op(name = "classify")]
fn classify(score: i64) -> Value {
    let grade = if score >= 90 {
        "excellent"
    } else if score >= 70 {
        "good"
    } else if score >= 50 {
        "average"
    } else {
        "fail"
    };
    serde_json::json!({ "grade": grade, "score": score })
}

#[op(name = "process_grade")]
fn process_grade(grade: String, score: i64) -> Value {
    serde_json::json!({ "result": format!("{grade}:{score}"), "x": score })
}

// ── CLI ──────────────────────────────────────────────────────────────────

struct Args {
    runs: usize,
    warmup: usize,
    /// Probe mode: skip timing, run each pattern once, emit
    /// `RESULT <name> <json>` lines so `parity.py` can diff outputs.
    probe: bool,
}

fn parse_args() -> Args {
    let mut runs = 200_usize;
    let mut warmup = 5_usize;
    let mut probe = false;
    let argv: Vec<String> = std::env::args().collect();
    let mut i = 1;
    while i < argv.len() {
        match argv[i].as_str() {
            "--runs" => {
                runs = argv.get(i + 1).and_then(|s| s.parse().ok()).unwrap_or(runs);
                i += 2;
            }
            "--warmup" => {
                warmup = argv.get(i + 1).and_then(|s| s.parse().ok()).unwrap_or(warmup);
                i += 2;
            }
            "--probe" => {
                probe = true;
                i += 1;
            }
            _ => i += 1,
        }
    }
    Args {
        runs,
        warmup,
        probe,
    }
}

// ── Bench plumbing ───────────────────────────────────────────────────────

#[derive(Debug)]
struct BenchResult {
    name: String,
    mean_ms: f64,
    p50_ms: f64,
    p99_ms: f64,
    min_ms: f64,
    max_ms: f64,
    status: BenchStatus,
}

#[derive(Debug)]
enum BenchStatus {
    Ok,
    Degenerate, // ran without runtime error but produced empty output → silent fail
}

fn bench_one(
    name: &str,
    graph_json: &str,
    inputs: Map<String, Value>,
    runs: usize,
    warmup: usize,
) -> Result<BenchResult, Box<dyn std::error::Error>> {
    let engine = Operon::builder(graph_json).auto_register().build()?;

    // First-run probe: detect silent failures (empty map, op-level error
    // keys) so misleadingly fast timings get flagged in the report.
    let probe = engine.run_json(inputs.clone(), None, None, None)?;
    let status = match probe.as_object() {
        Some(m) if m.is_empty() => BenchStatus::Degenerate,
        Some(m) if m.contains_key("error") => BenchStatus::Degenerate,
        _ => BenchStatus::Ok,
    };

    for _ in 0..warmup {
        let _ = engine.run_json(inputs.clone(), None, None, None);
    }

    let mut times_ms: Vec<f64> = Vec::with_capacity(runs);
    for _ in 0..runs {
        let start = Instant::now();
        let _ = engine.run_json(inputs.clone(), None, None, None);
        times_ms.push(start.elapsed().as_nanos() as f64 / 1_000_000.0);
    }

    let mut sorted = times_ms.clone();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let mean = times_ms.iter().sum::<f64>() / times_ms.len() as f64;
    let p50 = sorted[sorted.len() / 2];
    let p99 = sorted[((sorted.len() as f64) * 0.99) as usize].min(sorted[sorted.len() - 1]);
    let min = sorted[0];
    let max = sorted[sorted.len() - 1];

    Ok(BenchResult {
        name: name.to_string(),
        mean_ms: mean,
        p50_ms: p50,
        p99_ms: p99,
        min_ms: min,
        max_ms: max,
        status,
    })
}

fn truncate(s: &str, n: usize) -> String {
    let mut out: String = s.chars().take(n).collect();
    if s.chars().count() > n {
        out.push('…');
    }
    out
}

fn data_dir() -> PathBuf {
    let here = Path::new(env!("CARGO_MANIFEST_DIR"));
    here.join("data")
}

fn collect_patterns(data: &Path) -> Result<Vec<(String, PathBuf, PathBuf)>, std::io::Error> {
    // Discover patterns by scanning for *.graph.json with a matching .inputs.json.
    // Order: try to honour a known stable list first, then pick up anything else.
    let known = [
        "linear_50",
        "linear_100",
        "linear_200",
        "linear_500",
        "fib_chain_10x500",
        "fib_chain_20x500",
        "parallel_5",
        "parallel_10",
        "parallel_20",
        "matrix_chain_5x30",
        "matrix_chain_5x60",
        "matrix_chain_10x40",
        "matrix_chain_5x100",
        "cpu_contention_3h_10l_30m",
        "cpu_contention_5h_10l_30m",
        "nested_2",
        "nested_5",
        "nested_10",
        "branching_5",
        "branching_10",
        "production_3",
        "production_5",
    ];

    let mut found: HashMap<String, (PathBuf, PathBuf)> = HashMap::new();
    for entry in fs::read_dir(data)? {
        let entry = entry?;
        let path = entry.path();
        let fname = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
        if let Some(stem) = fname.strip_suffix(".graph.json") {
            let inputs = path.with_file_name(format!("{stem}.inputs.json"));
            if inputs.exists() {
                found.insert(stem.to_string(), (path.clone(), inputs));
            }
        }
    }

    let mut ordered: Vec<(String, PathBuf, PathBuf)> = Vec::new();
    for k in known.iter() {
        if let Some((g, i)) = found.remove(*k) {
            ordered.push(((*k).to_string(), g, i));
        }
    }
    let mut leftover: Vec<_> = found.into_iter().collect();
    leftover.sort_by(|a, b| a.0.cmp(&b.0));
    for (k, (g, i)) in leftover {
        ordered.push((k, g, i));
    }
    Ok(ordered)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = parse_args();
    let data = data_dir();
    if !data.exists() {
        eprintln!(
            "  data dir missing: {} — run `uv run python scripts/bench/generate.py` first.",
            data.display()
        );
        std::process::exit(1);
    }
    let patterns = collect_patterns(&data)?;
    if patterns.is_empty() {
        eprintln!("  no *.graph.json found in {} — run generate.py first.", data.display());
        std::process::exit(1);
    }

    if args.probe {
        // Output-parity mode: build engine once per pattern, run once,
        // emit a single `RESULT <name> <json>` line. Consumed by
        // `scripts/bench/parity.py`.
        for (name, graph_path, inputs_path) in patterns {
            let graph_json = fs::read_to_string(&graph_path)?;
            let inputs_value: Value = serde_json::from_str(&fs::read_to_string(&inputs_path)?)?;
            let inputs = inputs_value.as_object().cloned().unwrap_or_else(Map::new);
            let engine = Operon::builder(&graph_json).auto_register().build()?;
            let out = engine.run_json(inputs, None, None, None)?;
            println!("RESULT {name} {out}");
        }
        return Ok(());
    }

    println!(
        "  runs={}, warmup={}, patterns={}\n",
        args.runs,
        args.warmup,
        patterns.len()
    );
    println!(
        "  {:>26} | {:>9} | {:>9} | {:>9} | {:>9} | {:>9} | {:>10}",
        "pattern", "mean", "p50", "p99", "min", "max", "status"
    );
    println!(
        "  {:-<26}-+-{:-<9}-+-{:-<9}-+-{:-<9}-+-{:-<9}-+-{:-<9}-+-{:-<10}",
        "", "", "", "", "", "", ""
    );

    let mut degenerate: Vec<String> = Vec::new();
    let mut errored: Vec<String> = Vec::new();

    for (name, graph_path, inputs_path) in patterns {
        let graph_json = fs::read_to_string(&graph_path)?;
        let inputs_value: Value = serde_json::from_str(&fs::read_to_string(&inputs_path)?)?;
        let inputs = inputs_value
            .as_object()
            .cloned()
            .unwrap_or_else(Map::new);

        match bench_one(&name, &graph_json, inputs, args.runs, args.warmup) {
            Ok(r) => {
                let status = match r.status {
                    BenchStatus::Ok => "ok",
                    BenchStatus::Degenerate => {
                        degenerate.push(r.name.clone());
                        "DEGENERATE"
                    }
                };
                println!(
                    "  {:>26} | {:7.3}ms | {:7.3}ms | {:7.3}ms | {:7.3}ms | {:7.3}ms | {:>10}",
                    r.name, r.mean_ms, r.p50_ms, r.p99_ms, r.min_ms, r.max_ms, status
                );
            }
            Err(e) => {
                errored.push(format!("{name}: {e}"));
                println!("  {:>26} | ERROR: {}", name, truncate(&e.to_string(), 80));
            }
        }
    }

    if !degenerate.is_empty() || !errored.is_empty() {
        println!();
        if !degenerate.is_empty() {
            println!(
                "  ⚠️  {} pattern(s) ran with empty/error output (timings are bogus): {}",
                degenerate.len(),
                degenerate.join(", ")
            );
        }
        if !errored.is_empty() {
            println!("  ⚠️  {} pattern(s) failed at engine build time:", errored.len());
            for line in &errored {
                println!("       {}", truncate(line, 200));
            }
        }
    }

    Ok(())
}
