//! Custom Rust ops for Hush workflow examples.
//!
//! This crate demonstrates the recommended project convention: organize your
//! custom Rust ops by domain (math, text, crypto) in a single crate, then
//! reference them from Python workflows via `@op(rust="func_name")`.
//!
//! ## Project structure convention
//!
//! ```text
//! my-project/
//! ├── pyproject.toml              # Python workflows
//! ├── workflows/
//! │   ├── pipeline.py
//! │   └── agent.py
//! ├── rust_ops/                   # ONE crate for all custom Rust ops
//! │   ├── Cargo.toml
//! │   └── src/
//! │       ├── lib.rs
//! │       ├── math.rs             # Domain: math operations
//! │       ├── text.rs             # Domain: text processing
//! │       └── crypto.rs           # Domain: CPU-heavy crypto
//! ```
//!
//! ## Usage from Python
//!
//! ```python
//! # Python fallback runs when rush-core is not available.
//! # When mode="rust", the Rust implementation is used instead.
//! @op(rust="double")
//! def double(x: int):
//!     return {"result": x * 2}
//! ```

pub mod analytics;
pub mod crypto;
pub mod iteration;
pub mod math;
pub mod pipeline;
pub mod search;
pub mod streaming;
pub mod text;
