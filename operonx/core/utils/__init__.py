"""Shared utilities for the Operon engine.

- ``auto_name``, ``register_skip``, ``unique_name`` — auto-naming for ops
- ``get_current``, ``_current_graph`` — current-graph context tracking
- ``Param`` — input/output parameter descriptor for ops
- ``verify_data`` — data validation
- ``raise_error`` — error raising helper
- ``extract_condition_variables`` — extract variable names from a condition string
- ``fake_chunk_from`` — construct fake chunk responses (testing)
- ``YamlModel`` — base class for YAML-serializable configs
"""

from .auto_name import auto_name, register_skip, unique_name
from .common import (
    Param,
    extract_condition_variables,
    fake_chunk_from,
    raise_error,
    verify_data,
)
from .context import _current_graph, get_current
from .yaml_model import YamlModel

__all__ = [
    "auto_name",
    "register_skip",
    "unique_name",
    "get_current",
    "_current_graph",
    "Param",
    "verify_data",
    "raise_error",
    "extract_condition_variables",
    "fake_chunk_from",
    "YamlModel",
]
