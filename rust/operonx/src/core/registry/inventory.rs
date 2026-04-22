//! Link-time op and resource registration via the [`inventory`] crate.
//!
//! Mirrors Python's "import-triggers-registration" pattern: every `#[op]`-
//! annotated Rust fn submits an [`OpEntry`] and every `#[resource]`-annotated
//! factory submits a [`ResourceEntry`]. At engine startup,
//! [`OperonBuilder::auto_register`](crate::core::engine::OperonBuilder::auto_register)
//! iterates the collected entries and installs them.
//!
//! Users never construct these types directly — the proc-macros in
//! [`operonx-macros`](../../../../operonx-macros/) do it for them. The structs
//! are `pub` only so macro-expanded code (which runs in the user's crate) can
//! reach the paths.

use std::any::Any;

/// Whether an [`OpEntry`] is a regular op or a generator op.
///
/// Informational — the scheduler's streaming behavior is driven by
/// `OpConfig.yields`, not this flag. Kept for parity with `#[op(generator)]`
/// and for tooling that wants to surface generator ops in catalogs.
#[derive(Debug, Copy, Clone, Eq, PartialEq)]
pub enum OpKind {
    /// Plain op — returns a single `Value`.
    Regular,
    /// Generator op — returns a `Value::Array`; downstream consumers iterate.
    Generator,
}

/// An auto-registered op entry, collected by [`inventory`] at link time.
///
/// Constructed by the `#[op]` proc-macro. The `op_fn` field is a plain
/// function pointer (`fn(&Value) -> Value`) — the auto-registration shim
/// wraps it into an async [`OpFunc`](super::op_registry::OpFunc) when
/// installing into the runtime registry.
pub struct OpEntry {
    /// Short name (the `name = "..."` attr, or the function's ident).
    pub name: &'static str,
    /// `module_path!()` at the site where `#[op]` was applied. Used for
    /// module-qualified disambiguation (e.g. `"my_crate::math::double"`).
    pub module_path: &'static str,
    /// Regular vs generator.
    pub kind: OpKind,
    /// Sync body. Errors are signalled by a `{"error": "..."}` shape inside
    /// the returned `Value` — same convention as Python's `@op` failures
    /// surfaced via the op's output payload.
    pub op_fn: fn(&serde_json::Value) -> serde_json::Value,
}

impl OpEntry {
    /// Build a regular-op entry. `const fn` so callers can submit entries
    /// from `inventory::submit!` blocks (which require const exprs).
    pub const fn new_op(
        name: &'static str,
        module_path: &'static str,
        op_fn: fn(&serde_json::Value) -> serde_json::Value,
    ) -> Self {
        OpEntry {
            name,
            module_path,
            kind: OpKind::Regular,
            op_fn,
        }
    }

    /// Build a generator-op entry. Currently uses the same fn-pointer shape;
    /// the `kind` field distinguishes the two.
    pub const fn new_gen(
        name: &'static str,
        module_path: &'static str,
        op_fn: fn(&serde_json::Value) -> serde_json::Value,
    ) -> Self {
        OpEntry {
            name,
            module_path,
            kind: OpKind::Generator,
            op_fn,
        }
    }

    /// Module-qualified name with the crate prefix stripped.
    ///
    /// `"my_crate::math"` + `"double"` → `"math::double"`. Root-module
    /// entries return the bare name unchanged.
    pub fn qualified_name(&self) -> String {
        match self.module_path.find("::") {
            Some(idx) => {
                let stripped = &self.module_path[idx + 2..];
                if stripped.is_empty() {
                    self.name.to_string()
                } else {
                    format!("{}::{}", stripped, self.name)
                }
            }
            None => self.name.to_string(),
        }
    }
}

inventory::collect!(OpEntry);

/// An auto-registered resource factory, collected by [`inventory`] at link
/// time.
///
/// Constructed by the `#[resource]` proc-macro. The factory produces a
/// `Box<dyn Any + Send + Sync>` — typically a typed resource newtype like
/// `LlmResource(Arc<dyn BaseLLM>)` — which downstream code downcasts at
/// dispatch time.
pub struct ResourceEntry {
    /// Registry key (e.g. `"openai_gpt4"`).
    pub name: &'static str,
    /// Factory closure. Receives the resource's JSON config snippet, returns
    /// the built resource as `Box<dyn Any + Send + Sync>`.
    pub factory: fn(&serde_json::Value) -> Box<dyn Any + Send + Sync>,
}

impl ResourceEntry {
    /// `const fn` ctor used by `inventory::submit!`.
    pub const fn new(
        name: &'static str,
        factory: fn(&serde_json::Value) -> Box<dyn Any + Send + Sync>,
    ) -> Self {
        ResourceEntry { name, factory }
    }
}

inventory::collect!(ResourceEntry);

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn qualified_name_strips_crate_prefix() {
        let e = OpEntry::new_op("double", "my_crate::math", |_| serde_json::Value::Null);
        assert_eq!(e.qualified_name(), "math::double");
    }

    #[test]
    fn qualified_name_handles_crate_root() {
        let e = OpEntry::new_op("double", "my_crate", |_| serde_json::Value::Null);
        assert_eq!(e.qualified_name(), "double");
    }

    #[test]
    fn qualified_name_deep_module() {
        let e = OpEntry::new_op("classify", "my_crate::a::b::c", |_| serde_json::Value::Null);
        assert_eq!(e.qualified_name(), "a::b::c::classify");
    }
}
