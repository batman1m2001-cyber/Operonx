//! Unified op registry — single extension point for all op dispatch.
//!
//! Mirrors Python's model where every op is a BaseOp subclass.
//! Consumers implement `OpRegistry` to provide ops; hush-icore's scheduler
//! calls `create_op()` to get a `Box<dyn Op>` and runs it.
//!
//! Three registries compose via `CompositeRegistry`:
//!   - CoreRegistry (hush-icore): parser, branch
//!   - ProviderRegistry (hush-providers): llm, embedding, rerank, prompt, onnx
//!   - FnRegistry (hush-serve): #[hush_op] user functions

use std::sync::Arc;

use serde_json::{Map, Value};

use crate::config::BaseOpConfig;
use crate::error::RushError;
use crate::ops::op_trait::{Op, OpContext};

/// Unified op dispatch — resolves any op config to an executable Op.
///
/// Each crate implements this for its own ops. `CompositeRegistry` chains
/// them so the scheduler has a single dispatch point: `registry.create_op()`.
pub trait OpRegistry: Send + Sync {
    /// Create an Op from config. Returns None if this registry doesn't handle it.
    ///
    /// The returned Op borrows from config (lifetime 'a) — constructed per dispatch,
    /// used once, dropped. Zero heap allocation for stack-sized ops.
    fn create_op<'a>(&self, config: &'a BaseOpConfig) -> Option<Box<dyn Op + 'a>>;

    /// Create a generator from config. Returns None if not a generator or not recognized.
    ///
    /// Generators return `Vec<Value>` (eager). Future: lazy channel-based iteration.
    fn create_generator<'a>(
        &self,
        config: &'a BaseOpConfig,
    ) -> Option<Box<dyn GeneratorOp + 'a>> {
        let _ = config;
        None
    }

    /// List all op names this registry can handle (for error messages).
    fn available_ops(&self) -> Vec<String> {
        vec![]
    }
}

/// A generator op that yields multiple values.
///
/// Separate from `Op` because generators have different lifecycle:
/// no cache, no store_result per item, no push_output_refs — the scheduler
/// manages stream contexts and stores results per-yield.
pub trait GeneratorOp: Send + Sync {
    fn op_config(&self) -> &BaseOpConfig;

    /// Execute the generator, returning all yielded values.
    ///
    /// Each item in the Vec becomes a stream context in the scheduler.
    fn generate(
        &self,
        inputs: Map<String, Value>,
        ctx: &OpContext,
    ) -> Result<Vec<Value>, RushError>;
}

/// Chain of registries — first match wins.
///
/// Composes CoreRegistry + ProviderRegistry + FnRegistry into one dispatch point.
pub struct CompositeRegistry {
    registries: Vec<Arc<dyn OpRegistry>>,
}

impl CompositeRegistry {
    pub fn new(registries: Vec<Arc<dyn OpRegistry>>) -> Self {
        CompositeRegistry { registries }
    }
}

impl OpRegistry for CompositeRegistry {
    fn create_op<'a>(&self, config: &'a BaseOpConfig) -> Option<Box<dyn Op + 'a>> {
        for reg in &self.registries {
            if let Some(op) = reg.create_op(config) {
                return Some(op);
            }
        }
        None
    }

    fn create_generator<'a>(
        &self,
        config: &'a BaseOpConfig,
    ) -> Option<Box<dyn GeneratorOp + 'a>> {
        for reg in &self.registries {
            if let Some(gen) = reg.create_generator(config) {
                return Some(gen);
            }
        }
        None
    }

    fn available_ops(&self) -> Vec<String> {
        self.registries
            .iter()
            .flat_map(|r| r.available_ops())
            .collect()
    }
}

/// Core registry — handles built-in op types that live in hush-icore.
pub struct CoreRegistry;

impl OpRegistry for CoreRegistry {
    fn create_op<'a>(&self, config: &'a BaseOpConfig) -> Option<Box<dyn Op + 'a>> {
        match config.op_type.as_str() {
            "parser" => Some(Box::new(
                crate::ops::transform::parser_op::ParserOp::new(config),
            )),
            "branch" => Some(Box::new(
                crate::ops::flow::branch_op::BranchOp::new(config),
            )),
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Minimal test registry for unit tests.
    struct TestRegistry {
        ops: std::collections::HashMap<String, fn(&Value) -> Value>,
    }

    impl TestRegistry {
        fn new() -> Self {
            TestRegistry {
                ops: std::collections::HashMap::new(),
            }
        }
        fn register(&mut self, name: &str, f: fn(&Value) -> Value) {
            self.ops.insert(name.to_string(), f);
        }
    }

    impl OpRegistry for TestRegistry {
        fn create_op<'a>(&self, config: &'a BaseOpConfig) -> Option<Box<dyn Op + 'a>> {
            // Test registry doesn't create ops — it's for testing CompositeRegistry
            let _ = config;
            None
        }

        fn available_ops(&self) -> Vec<String> {
            self.ops.keys().cloned().collect()
        }
    }

    #[test]
    fn test_composite_registry_available_ops() {
        let mut r1 = TestRegistry::new();
        r1.register("a", |_| json!({}));
        let mut r2 = TestRegistry::new();
        r2.register("b", |_| json!({}));

        let composite = CompositeRegistry::new(vec![Arc::new(r1), Arc::new(r2)]);
        let ops = composite.available_ops();
        assert!(ops.contains(&"a".to_string()));
        assert!(ops.contains(&"b".to_string()));
    }
}
