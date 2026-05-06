"""Operon core — workflow engine, ops, state, tracing, registry.

Public surface re-exported from the submodules below. For top-level
convenience imports (``from operonx import Operon``), see
:mod:`operonx.__init__`.

Example::

    from operonx.core import Operon, GraphOp, START, END, PARENT
    from operonx.core.ops import FuncOp

    with GraphOp(name="workflow") as graph:
        node = FuncOp(name="processor", ...)
        START >> node >> END

    engine = Operon(graph)
    result = await engine.run(inputs={"key": "value"})
    print(result["output"])   # workflow output
    print(result["$state"])   # MemoryState for debugging
"""

from operonx.core.configs import EdgeConfig, EdgeType
from operonx.core.engine import ExecutionHandle, Operon
from operonx.core.loggings import LOGGER
from operonx.core.media import Media
from operonx.core.middleware import Middleware
from operonx.core.ops import (
    END,
    PARENT,
    PENDING,
    SCRATCH,
    START,
    BaseOp,
    BranchOp,
    DummyOp,
    FuncOp,
    GraphOp,
    Interrupt,
    ParserOp,
    ParserType,
    ScratchAccessor,
    graph,
    op,
)
from operonx.core.registry import (
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
from operonx.core.states import Cell, MemoryState, Ref, ScratchRef, StateSchema
from operonx.core.utils import Param

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
    "SCRATCH",
    "ScratchAccessor",
    # Scheduler events
    "Interrupt",
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
    "ScratchRef",
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
