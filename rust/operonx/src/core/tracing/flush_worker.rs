//! [`FlushWorker`] — hand collected trace data to every registered tracer.
//!
//! Mirrors Python [`operon/core/tracing/flush_worker.py`](../../../../operon/core/tracing/flush_worker.py).
//! Python uses a `ThreadPoolExecutor`; Rust uses `tokio::task::spawn_blocking`
//! so `Tracer::flush()` implementations (which are sync per §4b.3) run off
//! the async runtime.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use tokio::task::JoinHandle;
use tracing::{error, warn};

use super::base::Tracer;
use super::models::{TraceData, TraceNode};

/// Dispatch handle — submit once per workflow run.
#[derive(Debug, Default)]
pub struct FlushWorker;

impl FlushWorker {
    pub fn new() -> Self {
        Self
    }

    /// Schedule the `collect → flush` pipeline on a blocking worker thread.
    ///
    /// Returns a `JoinHandle` that resolves once every tracer has been
    /// called. The handle's `Result` captures the **first** tracer error
    /// (subsequent errors are logged + swallowed to avoid blocking on one
    /// noisy sink).
    pub fn submit(
        &self,
        tracers: Vec<Arc<dyn Tracer>>,
        trace_data: TraceData,
    ) -> JoinHandle<Result<(), String>> {
        tokio::task::spawn_blocking(move || {
            let mut first_err: Option<String> = None;
            for tracer in &tracers {
                // Per-tracer pipeline: merge tags → sample streams → filter → flush.
                let merged_tags = merge_tags(&trace_data.tags, tracer.tags());
                let sampled = sample_stream_nodes(&trace_data.nodes, tracer.stream_trace_limit());
                let filtered = match tracer.trace_filter() {
                    Some(tf) => tf.apply(sampled),
                    None => sampled,
                };
                let payload = TraceData {
                    tags: merged_tags,
                    nodes: filtered,
                    ..trace_data.clone()
                };
                if let Err(e) = tracer.flush(&payload) {
                    error!(
                        "FlushWorker: tracer '{}' flush failed: {}",
                        tracer.name(),
                        e
                    );
                    if first_err.is_none() {
                        first_err = Some(e.to_string());
                    }
                }
            }
            match first_err {
                Some(e) => Err(e),
                None => Ok(()),
            }
        })
    }
}

/// Merge dynamic and static tags, preserving insertion order + deduping.
///
/// Static tags (from the tracer) come first; dynamic tags (from the run)
/// append any that aren't already present.
fn merge_tags(dynamic: &[String], static_tags: &[String]) -> Vec<String> {
    let mut out: Vec<String> = static_tags.to_vec();
    for tag in dynamic {
        if !out.iter().any(|t| t == tag) {
            out.push(tag.clone());
        }
    }
    out
}

/// Cap the number of top-level `stream_context` groups per generator parent.
/// `None` limit = pass-through.
fn sample_stream_nodes(nodes: &[TraceNode], limit: Option<usize>) -> Vec<TraceNode> {
    let Some(cap) = limit else {
        return nodes.to_vec();
    };
    // Find parents that contain generators.
    let gen_parents: HashSet<String> = nodes
        .iter()
        .filter(|n| n.kind == "generator")
        .filter_map(|n| n.parent_trace_key.clone())
        .collect();
    if gen_parents.is_empty() {
        return nodes.to_vec();
    }

    // Tally stream_context groups per generator parent; mark excess for removal.
    let mut counts: HashMap<String, usize> = HashMap::new();
    let mut remove_keys: HashSet<String> = HashSet::new();
    for n in nodes {
        if n.kind == "stream_context" {
            if let Some(p) = &n.parent_trace_key {
                if gen_parents.contains(p) {
                    let c = counts.entry(p.clone()).and_modify(|c| *c += 1).or_insert(1);
                    if *c > cap {
                        remove_keys.insert(n.trace_key.clone());
                    }
                }
            }
        }
    }
    if remove_keys.is_empty() {
        return nodes.to_vec();
    }

    // Cascade: drop every descendant of a removed wrapper.
    let mut children_of: HashMap<String, Vec<String>> = HashMap::new();
    for n in nodes {
        if let Some(p) = &n.parent_trace_key {
            children_of.entry(p.clone()).or_default().push(n.trace_key.clone());
        }
    }
    let mut queue: Vec<String> = remove_keys.iter().cloned().collect();
    while let Some(k) = queue.pop() {
        if let Some(children) = children_of.get(&k) {
            for child in children {
                if remove_keys.insert(child.clone()) {
                    queue.push(child.clone());
                }
            }
        }
    }

    nodes
        .iter()
        .filter(|n| !remove_keys.contains(&n.trace_key))
        .cloned()
        .collect()
}

/// Global singleton — lazy-initialized on first use.
pub fn global() -> &'static FlushWorker {
    use std::sync::OnceLock;
    static WORKER: OnceLock<FlushWorker> = OnceLock::new();
    WORKER.get_or_init(FlushWorker::new)
}

#[cfg(unused)]
fn _unused_warn<T>(_: T) {
    warn!("dead-code silencer");
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    #[derive(Debug)]
    struct Collector(Arc<Mutex<Vec<String>>>);

    impl Tracer for Collector {
        fn name(&self) -> &str {
            "collector"
        }
        fn flush(&self, trace: &TraceData) -> Result<(), crate::core::exceptions::OperonError> {
            self.0.lock().unwrap().push(trace.request_id.clone());
            Ok(())
        }
    }

    #[tokio::test]
    async fn flush_runs_every_tracer() {
        let bag = Arc::new(Mutex::new(Vec::new()));
        let w = FlushWorker::new();
        let td = TraceData {
            request_id: "r".into(),
            workflow_name: "wf".into(),
            ..Default::default()
        };
        let handle = w.submit(
            vec![
                Arc::new(Collector(bag.clone())) as Arc<dyn Tracer>,
                Arc::new(Collector(bag.clone())) as Arc<dyn Tracer>,
            ],
            td,
        );
        handle.await.unwrap().unwrap();
        assert_eq!(bag.lock().unwrap().len(), 2);
    }

    #[test]
    fn merge_tags_dedupes() {
        let merged = merge_tags(&["a".into(), "b".into()], &["a".into(), "c".into()]);
        assert_eq!(merged, vec!["a", "c", "b"]);
    }
}
