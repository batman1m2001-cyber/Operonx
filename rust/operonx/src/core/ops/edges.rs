//! Sentinel op names — `START`, `END`, `PARENT`.
//!
//! Mirrors Python [`operonx/core/ops/_edges.py`](../../../../operonx/core/ops/_edges.py).
//! In Python, `START`, `END`, `PARENT` are module-level [`DummyOp`] singletons
//! referenced via `>>` / `~` authoring syntax. They serialize into graph JSON as
//! string op names — `"__START__"`, `"__END__"`, `"__PARENT__"`.
//!
//! Rust only ever sees the serialized graph, so the authoring layer (`DummyOp`,
//! `SoftEdge`, `@graph`, `>>` operator) is **not** ported. These constants are
//! the only thing Rust needs: the scheduler/validator checks adjacency entries
//! against these names to recognize entry, exit, and parent-input edges.

/// Entry-point sentinel. Every edge `START → op` marks `op` as an entry.
pub const START: &str = "__START__";

/// Exit sentinel. Every edge `op → END` marks `op` as a terminal output source.
pub const END: &str = "__END__";

/// Parent-graph-input sentinel. References of the form `PARENT["key"]` serialize
/// with `source: "__PARENT__"`; the scheduler resolves these against the
/// enclosing graph's input state.
pub const PARENT: &str = "__PARENT__";

/// `true` if `name` is one of the three sentinel op names.
#[inline]
pub fn is_sentinel(name: &str) -> bool {
    matches!(name, START | END | PARENT)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sentinels_match_python_literals() {
        assert_eq!(START, "__START__");
        assert_eq!(END, "__END__");
        assert_eq!(PARENT, "__PARENT__");
    }

    #[test]
    fn is_sentinel_recognizes_all_three() {
        assert!(is_sentinel(START));
        assert!(is_sentinel(END));
        assert!(is_sentinel(PARENT));
        assert!(!is_sentinel("user_op"));
    }
}
