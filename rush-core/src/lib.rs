use pyo3::prelude::*;

mod config;
mod engine;
mod ops;
mod refs;
mod runtime;
mod states;

use engine::Rush;
use ops::transform::func_op::RustFuncRegistry;

/// rush_core._native — Rust executor for the Hush workflow engine.
///
/// Rush: standalone engine (Builder-Executor architecture).
/// RustFuncRegistry: Rust-native op implementations.
#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Rush>()?;
    m.add_class::<RustFuncRegistry>()?;
    Ok(())
}
