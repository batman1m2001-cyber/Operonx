//! Shared helpers for the Rust usage-guide demos.
//!
//! Every ``demo.rs`` pulls this file in via ``#[path = "../_common.rs"]``
//! (adjust the relative-path prefix per depth). The helpers here:
//!
//! 1. Load ``graph.json`` + ``inputs.json`` from the demo's own folder.
//! 2. Build the ``Operon`` engine **before** timing begins. Engine
//!    construction — JSON parsing, schema build, resource hub lookup,
//!    inventory registration, optional Langfuse attach — is explicitly
//!    excluded from the reported latency.
//! 3. Run one untimed warmup followed by N timed iterations.
//! 4. Save a structured report to
//!    ``examples/bench_results/<example>_rust.json``.
//!
//! Reports are consumable side-by-side with the Python-side reports.

#![allow(dead_code)]

use std::env;
use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

use serde_json::{json, Map, Value};

use operonx::{Operon, OperonBuilder};

// ── CLI args ────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct DemoArgs {
    pub runs: usize,
    pub langfuse: bool,
}

pub fn parse_args(example: &str) -> DemoArgs {
    let argv: Vec<String> = env::args().collect();
    let mut runs = 5usize;
    let mut langfuse = false;

    let mut i = 1;
    while i < argv.len() {
        match argv[i].as_str() {
            "--runs" => {
                runs = argv
                    .get(i + 1)
                    .and_then(|s| s.parse::<usize>().ok())
                    .unwrap_or_else(|| panic!("--runs requires a positive integer"));
                i += 2;
            }
            "--langfuse" => {
                langfuse = true;
                i += 1;
            }
            "-h" | "--help" => {
                println!(
                    "Usage: {} [--runs N] [--langfuse]\n\
                     \n\
                     Runs the Rust side of `{}` with one warmup + N timed\n\
                     iterations per scenario. Latencies saved to\n\
                     examples/bench_results/{}_rust.json.",
                    argv[0], example, example
                );
                std::process::exit(0);
            }
            other => panic!("unknown arg '{}' — pass --help for usage", other),
        }
    }
    DemoArgs { runs, langfuse }
}

// ── Path resolution ─────────────────────────────────────────────────────

/// Absolute path to the repo root (the directory that contains `rust/` and
/// `examples/`). Derived from `CARGO_MANIFEST_DIR` at compile time.
pub fn repo_root() -> PathBuf {
    // CARGO_MANIFEST_DIR = <repo>/rust/operonx; go up two levels.
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .expect("CARGO_MANIFEST_DIR has no grandparent")
        .to_path_buf()
}

pub fn example_dir(example: &str) -> PathBuf {
    repo_root().join("examples").join("rust").join(example)
}

// ── Engine builder ──────────────────────────────────────────────────────

/// Construct ``Operon`` from a graph-JSON string. All heavy init is done
/// here — timing begins **after** this returns.
///
/// Bootstraps `.env` + `ResourceHub` once per process via
/// [`operonx::bootstrap`] (mirrors Python `examples/python/_common.py`),
/// then constructs a pure-orchestrator engine. The bootstrap is
/// idempotent: subsequent calls reuse the installed hub.
pub fn build_engine(graph_json: &str, args: &DemoArgs) -> Result<Operon, Box<dyn Error>> {
    ensure_bootstrapped();

    let mut builder: OperonBuilder = Operon::builder(graph_json).auto_register();

    if args.langfuse {
        builder = attach_langfuse(builder)?;
    }

    Ok(builder.build()?)
}

fn ensure_bootstrapped() {
    use std::sync::Once;
    static ONCE: Once = Once::new();
    ONCE.call_once(|| {
        let resources = repo_root().join("resources.yaml");
        let mut opts = operonx::BootstrapOpts::new();
        if resources.exists() {
            opts = opts.resources(resources);
        }
        let _ = operonx::bootstrap(opts);
    });
}

#[cfg(feature = "langfuse")]
fn attach_langfuse(builder: OperonBuilder) -> Result<OperonBuilder, Box<dyn Error>> {
    use operonx::telemetry::backends::langfuse::LangfuseConfig;
    use operonx::telemetry::tracers::langfuse::LangfuseTracer;

    let config = LangfuseConfig::from_env()?;
    let tracer = LangfuseTracer::from_config(config, Vec::new());
    Ok(builder.tracer(Arc::new(tracer)))
}

#[cfg(not(feature = "langfuse"))]
fn attach_langfuse(_builder: OperonBuilder) -> Result<OperonBuilder, Box<dyn Error>> {
    Err("crate built without `langfuse` feature".into())
}

// ── Scenario loading helpers ────────────────────────────────────────────

/// Load a sibling JSON file (`graph.json`, `inputs.json`) and parse it.
pub fn load_json(example: &str, filename: &str) -> Result<Value, Box<dyn Error>> {
    let path = example_dir(example).join(filename);
    let text = fs::read_to_string(&path)
        .map_err(|e| format!("failed to read {}: {}", path.display(), e))?;
    Ok(serde_json::from_str(&text)?)
}

/// Load inputs.json as a Map (the shape `Operon::run_json` expects).
pub fn load_inputs(example: &str) -> Result<Map<String, Value>, Box<dyn Error>> {
    let v = load_json(example, "inputs.json")?;
    match v {
        Value::Object(m) => Ok(m),
        other => Err(format!("inputs.json must be an object, got {:?}", other).into()),
    }
}

/// Build a name-suffixed copy of the graph JSON so Langfuse traces split
/// `<base>` → `<base>_rust` without colliding with the Python run. Every
/// nested `ref.source` / `full_name` that previously pointed at the old
/// graph name is rewritten to the new one.
pub fn rename_graph(graph: Value, name_suffix: &str) -> Value {
    let old_name = graph
        .get("name")
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .unwrap_or_else(|| "main".to_string());
    let new_name = format!("{}{}", old_name, name_suffix);

    fn walk(node: Value, old: &str, new: &str) -> Value {
        match node {
            Value::Object(m) => {
                let mut out = Map::new();
                for (k, v) in m {
                    if (k == "source" || k == "full_name") && v.is_string() {
                        let s = v.as_str().unwrap();
                        if s == old {
                            out.insert(k, Value::String(new.to_string()));
                        } else if let Some(rest) = s.strip_prefix(&format!("{}.", old)) {
                            out.insert(k, Value::String(format!("{}.{}", new, rest)));
                        } else {
                            out.insert(k, v);
                        }
                    } else {
                        out.insert(k, walk(v, old, new));
                    }
                }
                Value::Object(out)
            }
            Value::Array(arr) => Value::Array(arr.into_iter().map(|v| walk(v, old, new)).collect()),
            other => other,
        }
    }

    let mut rewritten = walk(graph, &old_name, &new_name);
    if let Some(obj) = rewritten.as_object_mut() {
        obj.insert("name".into(), Value::String(new_name));
    }
    rewritten
}

// ── Reporter ────────────────────────────────────────────────────────────

pub struct BenchReporter {
    example: String,
    scenarios: Map<String, Value>,
}

impl BenchReporter {
    pub fn new(example: impl Into<String>) -> Self {
        Self {
            example: example.into(),
            scenarios: Map::new(),
        }
    }

    /// Run one warmup + `runs` timed iterations of the closure and record
    /// per-run latencies plus summary stats.
    pub fn record<F>(
        &mut self,
        name: &str,
        runs: usize,
        mut step: F,
    ) -> Result<Value, Box<dyn Error>>
    where
        F: FnMut() -> Result<Value, Box<dyn Error>>,
    {
        // Warmup (untimed for the reported p50/p95/mean, but we record its
        // raw duration so the user can compare engine-first-call cost).
        let t0 = Instant::now();
        let result = step()?;
        let warmup_ms = t0.elapsed().as_secs_f64() * 1000.0;

        let mut latencies = Vec::with_capacity(runs);
        let mut last = result;
        for _ in 0..runs {
            let t0 = Instant::now();
            last = step()?;
            latencies.push(t0.elapsed().as_secs_f64() * 1000.0);
        }

        let stats = summary(&latencies);
        println!(
            "  [{}] p50={:.2}ms p95={:.2}ms mean={:.2}ms warmup={:.2}ms runs={}",
            name, stats.p50, stats.p95, stats.mean, warmup_ms, runs,
        );

        self.scenarios.insert(
            name.to_string(),
            json!({
                "warmup_ms": warmup_ms,
                "runs_ms": latencies,
                "p50_ms": stats.p50,
                "p95_ms": stats.p95,
                "mean_ms": stats.mean,
                "output": last.clone(),
            }),
        );
        Ok(last)
    }

    pub fn save(self) -> Result<PathBuf, Box<dyn Error>> {
        let dir = repo_root().join("examples").join("bench_results");
        fs::create_dir_all(&dir)?;
        let path = dir.join(format!("{}_rust.json", self.example));
        let now = chrono::Utc::now().to_rfc3339();
        let payload = json!({
            "example": self.example,
            "language": "rust",
            "timestamp": now,
            "scenarios": self.scenarios,
        });
        fs::write(&path, serde_json::to_string_pretty(&payload)?)?;
        println!(
            "  → report: examples/bench_results/{}_rust.json",
            self.example
        );
        Ok(path)
    }
}

struct Summary {
    p50: f64,
    p95: f64,
    mean: f64,
}

fn summary(xs: &[f64]) -> Summary {
    if xs.is_empty() {
        return Summary {
            p50: 0.0,
            p95: 0.0,
            mean: 0.0,
        };
    }
    let mut sorted: Vec<f64> = xs.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = sorted.len();
    let p50 = sorted[n / 2];
    let p95_idx = (((n as f64 - 1.0) * 0.95).round() as usize).clamp(0, n - 1);
    let p95 = sorted[p95_idx];
    let mean = xs.iter().sum::<f64>() / n as f64;
    Summary { p50, p95, mean }
}
