"""rush-core — High-performance Rust backend for the Hush workflow engine.

Builder-Executor architecture:
- Rush: standalone engine that loads serialized graph configs and executes them
- RustFuncRegistry: Rust-native op implementations for CPU-bound workloads

Usage:
    from rush_core import Rush, RustFuncRegistry, rust_op
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


def rust_op(rust_name: str):
    """Link a Python @op to its Rust implementation.

    When a function decorated with @rust_op is used as an op's core,
    BaseOp.run() will dispatch to the Rust registry instead of calling
    the Python function.

    Args:
        rust_name: The registered Rust op name (e.g. "rust_double")

    Example:
        @rust_op("rust_double")
        @op
        def double(x: int):
            return {"result": x * 2}  # Python fallback
    """

    def decorator(func):
        func._rust_op_name = rust_name
        # Also set on the inner function if @op wrapped it,
        # since FuncOp.core = the inner function (not the @op wrapper).
        if hasattr(func, "__wrapped__"):
            func.__wrapped__._rust_op_name = rust_name
        return func

    return decorator


__all__ += ["rust_op"]
