//! Closure-based op registry with module-qualified lookup.
//!
//! Supports both qualified names (`ml::sentiment::classify`) and bare name
//! fallback (`classify`). Panics on duplicate qualified names at startup.
//!
//! Implements the unified OpRegistry trait — wraps closures in ClosureOp/ClosureGen
//! so they go through the full Op::run() lifecycle (timing, caching, etc.).

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use hush_icore::config::BaseOpConfig;
use hush_icore::error::RushError;
use hush_icore::ops::op_trait::{Op, OpContext};
use hush_icore::registry::{GeneratorOp, OpRegistry};
use serde_json::{Map, Value};

type OpFn = Arc<dyn Fn(&Value) -> Value + Send + Sync>;
type GenFn = Arc<dyn Fn(&Value) -> Vec<Value> + Send + Sync>;

/// A registry that dispatches ops to native Rust closures.
///
/// Lookup order:
/// 1. Normalize input name: `.` → `::`
/// 2. Try qualified name match (e.g., `ml::sentiment::classify`)
/// 3. Try bare name fallback (e.g., `classify`)
/// 4. If bare name is ambiguous → error
/// 5. If no match → error with available ops list
pub struct FnRegistry {
    ops_qualified: HashMap<String, OpFn>,
    ops_bare: HashMap<String, OpFn>,
    ops_bare_ambiguous: HashSet<String>,
    generators_qualified: HashMap<String, GenFn>,
    generators_bare: HashMap<String, GenFn>,
    generators_bare_ambiguous: HashSet<String>,
    all_op_names: Vec<String>,
    all_gen_names: Vec<String>,
}

// =============================================================================
// ClosureOp / ClosureGen — wrap closures into Op/GeneratorOp traits
// =============================================================================

/// Wraps a `fn(&Value) -> Value` closure into a proper `Op`.
/// Goes through `Op::run()` lifecycle: timing, caching, store_result, push_output_refs.
struct ClosureOp<'a> {
    config: &'a BaseOpConfig,
    closure: OpFn,
}

impl Op for ClosureOp<'_> {
    fn op_config(&self) -> &BaseOpConfig {
        self.config
    }
    fn execute_core(
        &self,
        inputs: Map<String, Value>,
        _ctx: &OpContext,
    ) -> Result<Option<Value>, RushError> {
        Ok(Some((self.closure)(&Value::Object(inputs))))
    }
}

/// Wraps a `fn(&Value) -> Vec<Value>` closure into a proper `GeneratorOp`.
struct ClosureGen<'a> {
    config: &'a BaseOpConfig,
    closure: GenFn,
}

impl GeneratorOp for ClosureGen<'_> {
    fn op_config(&self) -> &BaseOpConfig {
        self.config
    }
    fn generate(
        &self,
        inputs: Map<String, Value>,
        _ctx: &OpContext,
    ) -> Result<Vec<Value>, RushError> {
        Ok((self.closure)(&Value::Object(inputs)))
    }
}

// =============================================================================
// FnRegistry implementation
// =============================================================================

impl FnRegistry {
    pub fn new() -> Self {
        FnRegistry {
            ops_qualified: HashMap::new(),
            ops_bare: HashMap::new(),
            ops_bare_ambiguous: HashSet::new(),
            generators_qualified: HashMap::new(),
            generators_bare: HashMap::new(),
            generators_bare_ambiguous: HashSet::new(),
            all_op_names: Vec::new(),
            all_gen_names: Vec::new(),
        }
    }

    /// Register a regular op by name (manual registration, no module path).
    pub fn register_op<F>(&mut self, name: &str, f: F)
    where
        F: Fn(&Value) -> Value + Send + Sync + 'static,
    {
        let arc: OpFn = Arc::new(f);
        self.ops_qualified.insert(name.to_string(), arc.clone());
        let bare = Self::bare_name(name).to_string();
        self.ops_bare.insert(bare, arc);
        self.all_op_names.push(name.to_string());
    }

    /// Register a generator op by name.
    pub fn register_generator<F>(&mut self, name: &str, f: F)
    where
        F: Fn(&Value) -> Vec<Value> + Send + Sync + 'static,
    {
        let arc: GenFn = Arc::new(f);
        self.generators_qualified.insert(name.to_string(), arc.clone());
        let bare = Self::bare_name(name).to_string();
        self.generators_bare.insert(bare, arc);
        self.all_gen_names.push(name.to_string());
    }

    /// Register a generator op that returns a JSON array `Value`.
    pub fn register_generator_value<F>(&mut self, name: &str, f: F)
    where
        F: Fn(&Value) -> Value + Send + Sync + 'static,
    {
        self.register_generator(name, move |inputs: &Value| match f(inputs) {
            Value::Array(items) => items,
            other => vec![other],
        });
    }

    /// Collect all `#[hush_op]`-annotated functions discovered by `inventory`.
    pub fn collect_from_inventory(&mut self) {
        for entry in inventory::iter::<crate::OpEntry> {
            let qualified = entry.qualified_name();
            let bare = entry.name.to_string();

            match entry.kind {
                crate::OpKind::Regular => {
                    let f = entry.op_fn;
                    let arc: OpFn = Arc::new(move |v| f(v));

                    // Qualified: must be unique
                    if self.ops_qualified.contains_key(&qualified) {
                        panic!(
                            "Duplicate op '{}': already registered. \
                             Each op must have a unique module-qualified name.",
                            qualified
                        );
                    }
                    self.ops_qualified.insert(qualified.clone(), arc.clone());
                    self.all_op_names.push(qualified);

                    // Bare: track ambiguity
                    if self.ops_bare_ambiguous.contains(&bare) {
                        // Already ambiguous, skip
                    } else if self.ops_bare.contains_key(&bare) {
                        // Second registration with same bare name → ambiguous
                        self.ops_bare.remove(&bare);
                        self.ops_bare_ambiguous.insert(bare);
                    } else {
                        self.ops_bare.insert(bare, arc);
                    }
                }
                crate::OpKind::Generator => {
                    let f = entry.gen_fn;
                    let arc: GenFn = Arc::new(move |v| match f(v) {
                        Value::Array(items) => items,
                        other => vec![other],
                    });

                    if self.generators_qualified.contains_key(&qualified) {
                        panic!(
                            "Duplicate generator '{}': already registered. \
                             Each generator must have a unique module-qualified name.",
                            qualified
                        );
                    }
                    self.generators_qualified.insert(qualified.clone(), arc.clone());
                    self.all_gen_names.push(qualified);

                    if self.generators_bare_ambiguous.contains(&bare) {
                        // skip
                    } else if self.generators_bare.contains_key(&bare) {
                        self.generators_bare.remove(&bare);
                        self.generators_bare_ambiguous.insert(bare);
                    } else {
                        self.generators_bare.insert(bare, arc);
                    }
                }
            }
        }

        self.all_op_names.sort();
        self.all_gen_names.sort();

        log::info!(
            "Auto-registered {} ops, {} generators",
            self.ops_qualified.len(),
            self.generators_qualified.len()
        );
    }

    /// Returns true if no ops or generators are registered.
    pub fn is_empty(&self) -> bool {
        self.ops_qualified.is_empty() && self.generators_qualified.is_empty()
    }

    /// Normalize a name: replace `.` with `::` for cross-language matching.
    fn normalize(name: &str) -> String {
        name.replace('.', "::")
    }

    /// Extract bare name (last segment after `::` or `.`).
    fn bare_name(name: &str) -> &str {
        name.rsplit("::").next()
            .unwrap_or_else(|| name.rsplit('.').next().unwrap_or(name))
    }

    /// Look up an op closure by name: qualified → suffix → bare fallback.
    fn lookup_op(&self, name: &str) -> LookupResult<&OpFn> {
        let normalized = Self::normalize(name);

        // 1. Exact qualified match
        if let Some(f) = self.ops_qualified.get(&normalized) {
            return LookupResult::Found(f);
        }

        // 2. Suffix match — bidirectional:
        //    a. query is suffix of registered: "workflow::each_item" matches "ex05::workflow::each_item"
        //    b. registered is suffix of query: "quality_scorer::_aggregate" matches "src::quality_scorer::_aggregate"
        if normalized.contains("::") {
            let query_suffix = format!("::{}", normalized);
            let matches: Vec<_> = self.ops_qualified.iter()
                .filter(|(k, _)| {
                    k.ends_with(&query_suffix) || *k == &normalized
                    || normalized.ends_with(&format!("::{}", k))
                })
                .collect();
            if matches.len() == 1 {
                return LookupResult::Found(matches[0].1);
            }
        }

        // 3. Bare name fallback
        let bare = Self::bare_name(name);
        if self.ops_bare_ambiguous.contains(bare) {
            let matches: Vec<_> = self.all_op_names.iter()
                .filter(|n| n.ends_with(&format!("::{}", bare)))
                .collect();
            return LookupResult::Ambiguous(bare.to_string(), matches.into_iter().cloned().collect());
        }
        if let Some(f) = self.ops_bare.get(bare) {
            return LookupResult::Found(f);
        }

        LookupResult::NotFound
    }

    /// Look up a generator closure by name: qualified → suffix → bare fallback.
    fn lookup_gen(&self, name: &str) -> LookupResult<&GenFn> {
        let normalized = Self::normalize(name);

        // 1. Exact qualified match
        if let Some(f) = self.generators_qualified.get(&normalized) {
            return LookupResult::Found(f);
        }

        // 2. Suffix match (bidirectional)
        if normalized.contains("::") {
            let query_suffix = format!("::{}", normalized);
            let matches: Vec<_> = self.generators_qualified.iter()
                .filter(|(k, _)| {
                    k.ends_with(&query_suffix) || *k == &normalized
                    || normalized.ends_with(&format!("::{}", k))
                })
                .collect();
            if matches.len() == 1 {
                return LookupResult::Found(matches[0].1);
            }
        }

        // 3. Bare name fallback
        let bare = Self::bare_name(name);
        if self.generators_bare_ambiguous.contains(bare) {
            let matches: Vec<_> = self.all_gen_names.iter()
                .filter(|n| n.ends_with(&format!("::{}", bare)))
                .collect();
            return LookupResult::Ambiguous(bare.to_string(), matches.into_iter().cloned().collect());
        }
        if let Some(f) = self.generators_bare.get(bare) {
            return LookupResult::Found(f);
        }

        LookupResult::NotFound
    }
}

enum LookupResult<T> {
    Found(T),
    Ambiguous(String, Vec<String>),
    NotFound,
}

impl Default for FnRegistry {
    fn default() -> Self {
        Self::new()
    }
}

impl OpRegistry for FnRegistry {
    fn create_op<'a>(&self, config: &'a BaseOpConfig) -> Option<Box<dyn Op + 'a>> {
        // Only handle "code" ops (user-defined functions)
        if config.op_type != "code" || config.is_generator {
            return None;
        }
        let func_name = config.func_name.as_deref().unwrap_or(&config.name);
        match self.lookup_op(func_name) {
            LookupResult::Found(f) => Some(Box::new(ClosureOp {
                config,
                closure: f.clone(),
            })),
            LookupResult::Ambiguous(bare, matches) => {
                log::error!(
                    "Ambiguous op '{}': found in multiple modules {:?}. Use fully qualified name.",
                    bare, matches
                );
                None
            }
            LookupResult::NotFound => None,
        }
    }

    fn create_generator<'a>(
        &self,
        config: &'a BaseOpConfig,
    ) -> Option<Box<dyn GeneratorOp + 'a>> {
        if !config.is_generator {
            return None;
        }
        let func_name = config.func_name.as_deref().unwrap_or(&config.name);
        match self.lookup_gen(func_name) {
            LookupResult::Found(f) => Some(Box::new(ClosureGen {
                config,
                closure: f.clone(),
            })),
            LookupResult::Ambiguous(bare, matches) => {
                log::error!(
                    "Ambiguous generator '{}': found in multiple modules {:?}. Use fully qualified name.",
                    bare, matches
                );
                None
            }
            LookupResult::NotFound => None,
        }
    }

    fn available_ops(&self) -> Vec<String> {
        let mut all = self.all_op_names.clone();
        all.extend(self.all_gen_names.iter().map(|n| format!("{} (generator)", n)));
        all
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use hush_icore::config::OpBound;
    use serde_json::json;

    /// Build a minimal BaseOpConfig for testing.
    fn test_config(func_name: &str, is_generator: bool) -> BaseOpConfig {
        BaseOpConfig {
            op_type: "code".to_string(),
            full_name: format!("g.{}", func_name),
            name: func_name.to_string(),
            func_name: Some(func_name.to_string()),
            is_async: false,
            enabled: true,
            verbose: false,
            stream: false,
            is_generator,
            bound: OpBound::Cpu,
            inputs: vec![],
            outputs: vec![],
            branch_config: None,
            inner_graph: None,
            provider_config: None,
            contain_generation: false,
            cache: None,
        }
    }

    #[test]
    fn test_fn_registry_create_op() {
        let mut reg = FnRegistry::new();
        reg.register_op("double", |v| {
            let x = v["x"].as_i64().unwrap_or(0);
            json!({"result": x * 2})
        });

        let config = test_config("double", false);
        assert!(reg.create_op(&config).is_some());
    }

    #[test]
    fn test_fn_registry_generator() {
        let mut reg = FnRegistry::new();
        reg.register_generator("range", |v| {
            let n = v["n"].as_i64().unwrap_or(0);
            (0..n).map(|i| json!({"value": i})).collect()
        });

        let config = test_config("range", true);
        assert!(reg.create_generator(&config).is_some());
    }

    #[test]
    fn test_dot_to_colon_normalization() {
        let mut reg = FnRegistry::new();
        reg.register_op("ml::sentiment::classify", |_| json!({"ok": true}));

        let mut config = test_config("ml.sentiment.classify", false);
        config.func_name = Some("ml.sentiment.classify".to_string());
        assert!(reg.create_op(&config).is_some());
    }

    #[test]
    fn test_bare_name_fallback() {
        let mut reg = FnRegistry::new();
        reg.register_op("ml::sentiment::classify", |_| json!({"ok": true}));

        let mut config = test_config("classify", false);
        config.func_name = Some("classify".to_string());
        assert!(reg.create_op(&config).is_some());
    }

    #[test]
    fn test_available_ops() {
        let mut reg = FnRegistry::new();
        reg.register_op("math::double", |_| json!({}));
        reg.register_op("ml::classify", |_| json!({}));

        let available = reg.available_ops();
        assert!(available.contains(&"math::double".to_string()));
        assert!(available.contains(&"ml::classify".to_string()));
    }
}
