//! FlushWorker — background task pool for trace collection and flushing.
//!
//! Mirrors Python's `hush.core.tracing.flush_worker.FlushWorker`.
//! Both collect (CPU-bound) and flush (I/O-bound) run in background
//! tokio tasks, never blocking the caller.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};

use serde_json::Value;
use tokio::task::JoinHandle;

use crate::config::GraphConfig;
use crate::states::state::EngineState;
use crate::tracing::collector::TraceCollector;
use crate::tracing::tracer::Tracer;

/// Background task pool for trace collection and flushing.
///
/// `submit()` spawns a tokio task that collects trace data from state
/// and flushes to all registered tracers. Engine.run_json() calls this
/// and returns immediately.
///
/// Uses interior mutability (Mutex) so `submit()` takes `&self`,
/// allowing the engine to remain `&self` for `run_json()`.
pub struct FlushWorker {
    handles: Mutex<Vec<JoinHandle<()>>>,
}

impl FlushWorker {
    pub fn new() -> Self {
        FlushWorker {
            handles: Mutex::new(Vec::new()),
        }
    }

    /// Submit a collect-and-flush task. Non-blocking — returns immediately.
    pub fn submit(
        &self,
        tracers: Vec<Arc<dyn Tracer>>,
        config: Arc<GraphConfig>,
        state: Arc<EngineState>,
    ) {
        let handle = tokio::spawn(async move {
            // 1. Collect (CPU-bound) — run in blocking thread
            let trace_data = {
                let config = config.clone();
                let state = state.clone();
                match tokio::task::spawn_blocking(move || {
                    let collector = TraceCollector::new(&config);
                    collector.collect(&state, true)
                })
                .await
                {
                    Ok(data) => data,
                    Err(e) => {
                        eprintln!("[rush-tracing] Failed to collect trace data: {e}");
                        return;
                    }
                }
            };

            // 2. Flush to each tracer with merged tags and sampling
            for tracer in &tracers {
                let merged_tags = merge_tags(&trace_data, &tracer.tags());
                let limit = tracer.stream_trace_limit();
                let sampled_nodes = sample_stream_nodes(&trace_data, limit);

                let mut data = trace_data.clone();
                if let Some(obj) = data.as_object_mut() {
                    obj.insert("tags".into(), Value::Array(
                        merged_tags.iter().map(|t| Value::String(t.clone())).collect()
                    ));
                    obj.insert("nodes".into(), sampled_nodes);
                }

                let tracer = tracer.clone();
                // Flush is typically I/O-bound — run in blocking thread
                let _ = tokio::task::spawn_blocking(move || {
                    if let Err(e) = tracer.flush(data) {
                        eprintln!("[rush-tracing] Failed to flush: {e}");
                    }
                })
                .await;
            }
        });
        self.handles.lock().unwrap().push(handle);
    }

    /// Wait for all pending flushes to complete.
    pub async fn wait(&self) {
        let handles: Vec<JoinHandle<()>> = {
            let mut locked = self.handles.lock().unwrap();
            locked.drain(..).collect()
        };
        for handle in handles {
            let _ = handle.await;
        }
    }
}

/// Merge dynamic tags (from state) and static tags (from tracer), deduped.
fn merge_tags(trace_data: &Value, static_tags: &[String]) -> Vec<String> {
    let mut merged: Vec<String> = static_tags.to_vec();
    if let Some(dynamic) = trace_data.get("tags").and_then(|v| v.as_array()) {
        for tag in dynamic {
            if let Some(s) = tag.as_str() {
                if !merged.iter().any(|t| t == s) {
                    merged.push(s.to_string());
                }
            }
        }
    }
    merged
}

/// Apply stream_trace_limit sampling to context groups per generator.
fn sample_stream_nodes(trace_data: &Value, limit: Option<usize>) -> Value {
    let nodes = match trace_data.get("nodes").and_then(|v| v.as_array()) {
        Some(arr) => arr,
        None => return Value::Array(vec![]),
    };

    let limit = match limit {
        Some(l) => l,
        None => return Value::Array(nodes.clone()),
    };

    // Find parents that contain generators
    let mut gen_parents: HashSet<String> = HashSet::new();
    for n in nodes {
        if n.get("kind").and_then(|v| v.as_str()) == Some("generator") {
            if let Some(parent) = n.get("parent_trace_key").and_then(|v| v.as_str()) {
                gen_parents.insert(parent.to_string());
            }
        }
    }

    if gen_parents.is_empty() {
        return Value::Array(nodes.clone());
    }

    // Count top-level stream_context groups per generator parent
    let mut counts: HashMap<String, usize> = HashMap::new();
    let mut remove_keys: HashSet<String> = HashSet::new();

    for n in nodes {
        if n.get("kind").and_then(|v| v.as_str()) == Some("stream_context") {
            if let Some(parent) = n.get("parent_trace_key").and_then(|v| v.as_str()) {
                if gen_parents.contains(parent) {
                    let count = counts.entry(parent.to_string()).or_insert(0);
                    *count += 1;
                    if *count > limit {
                        if let Some(key) = n.get("trace_key").and_then(|v| v.as_str()) {
                            remove_keys.insert(key.to_string());
                        }
                    }
                }
            }
        }
    }

    if remove_keys.is_empty() {
        return Value::Array(nodes.clone());
    }

    // Cascade: remove all descendants of removed context groups
    let mut children_map: HashMap<String, Vec<String>> = HashMap::new();
    for n in nodes {
        if let (Some(parent), Some(key)) = (
            n.get("parent_trace_key").and_then(|v| v.as_str()),
            n.get("trace_key").and_then(|v| v.as_str()),
        ) {
            children_map
                .entry(parent.to_string())
                .or_default()
                .push(key.to_string());
        }
    }

    let mut queue: Vec<String> = remove_keys.iter().cloned().collect();
    while let Some(k) = queue.pop() {
        if let Some(children) = children_map.get(&k) {
            for child in children {
                if remove_keys.insert(child.clone()) {
                    queue.push(child.clone());
                }
            }
        }
    }

    let filtered: Vec<Value> = nodes
        .iter()
        .filter(|n| {
            n.get("trace_key")
                .and_then(|v| v.as_str())
                .map_or(true, |k| !remove_keys.contains(k))
        })
        .cloned()
        .collect();

    Value::Array(filtered)
}
