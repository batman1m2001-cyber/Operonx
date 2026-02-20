"""hush-runtime — High-performance Rust backend for the Hush workflow engine.

Phase 1: CompiledGraph for graph scheduling.
Phase 2: RustMemoryState for high-performance state management.
Phase 3: RustFuncRegistry for Rust-native op execution.

Usage:
    from hush_runtime import CompiledGraph, RustMemoryState, RustFuncRegistry, rust_op
    from hush_runtime import is_rust_available
"""

from hush_runtime._mode import ExecutionMode, is_rust_available

__all__ = ["ExecutionMode", "is_rust_available"]

# Conditionally export Rust types if the native module is available
try:
    from hush_runtime._native import (
        CompiledGraph,
        RunState,
        RustFuncRegistry,
        RustMemoryState,
    )

    __all__ += ["CompiledGraph", "RunState", "RustFuncRegistry", "RustMemoryState"]
except ImportError:
    RustFuncRegistry = None


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
