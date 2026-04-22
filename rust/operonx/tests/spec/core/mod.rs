//! Shared spec tests for `core` — mirrors `Operon/tests/spec/core/` fixture folders.
//!
//! Each submodule pulls in one fixture-driven test via [`run_fixture`]. The
//! JSON shapes live under `tests/spec/core/<area>/<name>/{graph,inputs,expected}.json`
//! and are intended to be read **identically** by the Python parity harness.

pub mod iteration;
pub mod ops;
pub mod scheduler;
pub mod state;
