"""
Operon Core - Workflow Engine

A powerful async workflow orchestration framework.

Example:
    ```python
    from operon.core import Operon, GraphOp, START, END, PARENT
    from operon.core.ops import FuncOp

    # Define graph
    with GraphOp(name="my-workflow") as graph:
        node = FuncOp(name="processor", ...)
        START >> node >> END

    # Create engine and run
    engine = Operon(graph)
    result = await engine.run(inputs={"key": "value"})
    print(result["output"])   # workflow output
    print(result["$state"])   # access state for debugging
    ```
"""

from operon.core.configs import (
    EdgeConfig,
    EdgeType,
)
from operon.core.engine import Operon
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
from operon.core.states import (
    Cell,
    MemoryState,
    Ref,
    StateSchema,
)
from operon.core.utils import Param

__version__ = "0.1.0"

__all__ = [
    # Main engine
    "Operon",
    "Middleware",
    # Nodes
    "BaseOp",
    "DummyOp",
    "GraphOp",
    "BranchOp",
    "FuncOp",
    "op",
    "graph",
    "ParserOp",
    "ParserType",
    # Markers
    "START",
    "END",
    "PARENT",
    "PENDING",
    # State
    "StateSchema",
    "MemoryState",
    # Config
    "EdgeConfig",
    "EdgeType",
    # Schema
    "Param",
    # Media (for multimodal trace extraction)
    "Media",
    # Logging
    "LOGGER",
    # Registry
    "ResourceHub",
    "ConfigRegistry",
    "ConfigEntry",
    "REGISTRY",
    "CacheEntry",
    "HealthCheckResult",
    "ConfigStorage",
    "YamlConfigStorage",
    "JsonConfigStorage",
]
