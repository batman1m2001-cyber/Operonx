"""Execution mode configuration for Hush runtime."""

from enum import Enum


class ExecutionMode(str, Enum):
    """Execution backend mode."""

    PYTHON = "python"
    RUST = "rust"


def is_rust_available() -> bool:
    """Check if the Rust native extension is available."""
    try:
        from hush_runtime._native import CompiledGraph  # noqa: F401

        return True
    except ImportError:
        return False
