"""rush-core — High-performance Rust backend for the Hush workflow engine.

Builder-Executor architecture:
- Rush: standalone engine that loads serialized graph configs and executes them
- RustFuncRegistry: Rust-native op implementations for CPU-bound workloads

Usage:
    from rush_core import Rush, RustFuncRegistry
    from rush_core import is_rust_available
"""

from rush_core._mode import ExecutionMode, is_rust_available

__all__ = ["ExecutionMode", "is_rust_available"]

# Conditionally export Rust types if the native module is available
try:
    from rush_core._native import (
        RustFuncRegistry,
        Rush,
    )

    __all__ += ["RustFuncRegistry", "Rush"]
except ImportError:
    RustFuncRegistry = None
    Rush = None
