use pyo3::prelude::*;

mod config;
mod engine;
pub(crate) mod error;
mod ops;
mod plugins;
mod refs;
mod runtime;
mod states;

use engine::Rush;

/// rush_core._native — Rust executor for the Hush workflow engine.
///
/// Rush: standalone engine (Builder-Executor architecture).
/// Plugin ops loaded at runtime from cdylib shared libraries.
#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Rush>()?;
    Ok(())
}
