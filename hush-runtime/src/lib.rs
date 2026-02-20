use pyo3::prelude::*;

mod ops;
mod states;

use ops::graph::graph_op::{CompiledGraph, RunState};
use ops::transform::func_op::RustFuncRegistry;
use states::state::RustMemoryState;

/// hush_runtime._native — Rust extension module for Hush workflow engine.
///
/// Phase 1: CompiledGraph for high-performance graph scheduling.
/// Phase 2: RustMemoryState for high-performance state management.
/// Phase 3: RustFuncRegistry for Rust-native op execution.
#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<CompiledGraph>()?;
    m.add_class::<RunState>()?;
    m.add_class::<RustMemoryState>()?;
    m.add_class::<RustFuncRegistry>()?;
    Ok(())
}
