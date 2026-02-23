"""rush-core — High-performance Rust backend for the Hush workflow engine.

Builder-Executor architecture:
- Rush: standalone engine that loads serialized graph configs and executes them
- Plugin ops: loaded at runtime from cdylib shared libraries via @op(rust="path::func")

Usage:
    from rush_core import Rush
    from rush_core import is_rust_available
"""

from rush_core._mode import ExecutionMode, is_rust_available

__all__ = ["ExecutionMode", "is_rust_available"]

# Conditionally export Rust types if the native module is available
try:
    from rush_core._native import Rush

    __all__ += ["Rush"]
except ImportError:
    Rush = None
