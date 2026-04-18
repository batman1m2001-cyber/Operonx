"""Operon core — workflow engine, ops, state, tracing, registry.

Public surface re-exported from the submodules below. For top-level
convenience imports (``from operon import Operon``), see
:mod:`operon.__init__`.

Example::

    from operon.core import Operon, GraphOp, START, END, PARENT
    from operon.core.ops import FuncOp

    with GraphOp(name="workflow") as graph:
        node = FuncOp(name="processor", ...)
        START >> node >> END

    engine = Operon(graph)
    result = await engine.run(inputs={"key": "value"})
    print(result["output"])   # workflow output
    print(result["$state"])   # MemoryState for debugging
"""

from operon.core.configs import EdgeConfig, EdgeType
from operon.core.engine import ExecutionHandle, Operon
from operon.core.loggings import LOGGER
from operon.core.media import Media
from operon.core.middleware import Middleware
from operon.core.ops import (
    END,
    PARENT,
    PENDING,
    START,
    BaseOp,
    BranchOp,
    DummyOp,
    FuncOp,
    GraphOp,
    ParserOp,
    ParserType,
    graph,
    op,
)
from operon.core.registry import (
    REGISTRY,
    CacheEntry,
    ConfigEntry,
    ConfigRegistry,
    ConfigStorage,
    HealthCheckResult,
    JsonConfigStorage,
    ResourceHub,
    YamlConfigStorage,
)
from operon.core.states import Cell, MemoryState, Ref, StateSchema
from operon.core.utils import Param

__all__ = [
    # Engine
    "Operon",
    "ExecutionHandle",
    "Middleware",
    # Op base / markers
    "BaseOp",
    "DummyOp",
    "START",
    "END",
    "PARENT",
    "PENDING",
    # Op types
    "GraphOp",
    "BranchOp",
    "FuncOp",
    "ParserOp",
    "ParserType",
    # Decorators
    "op",
    "graph",
    # State
    "StateSchema",
    "MemoryState",
    "Ref",
    "Cell",
    # Configs / params
    "EdgeConfig",
    "EdgeType",
    "Param",
    # Multimodal
    "Media",
    # Logging
    "LOGGER",
    # Registry
    "ResourceHub",
    "ConfigRegistry",
    "ConfigEntry",
    "CacheEntry",
    "REGISTRY",
    "HealthCheckResult",
    "ConfigStorage",
    "YamlConfigStorage",
    "JsonConfigStorage",
]
