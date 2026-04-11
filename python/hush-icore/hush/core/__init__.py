"""
Hush Core - Workflow Engine

A powerful async workflow orchestration framework.

Example:
    ```python
    from hush.core import Hush, GraphOp, START, END, PARENT
    from hush.core.ops import FuncOp

    # Define graph
    with GraphOp(name="my-workflow") as graph:
        node = FuncOp(name="processor", ...)
        START >> node >> END

    # Create engine and run
    engine = Hush(graph)
    result = await engine.run(inputs={"key": "value"})
    print(result["output"])   # workflow output
    print(result["$state"])   # access state for debugging
    ```
"""

from hush.core.configs import (
    EdgeConfig,
    EdgeType,
)
from hush.core.engine import Hush
from hush.core.loggings import LOGGER
from hush.core.media import Media
from hush.core.middleware import Middleware
from hush.core.ops import (
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
from hush.core.registry import (
    REGISTRY,
    CacheEntry,
    ConfigEntry,
    ConfigRegistry,
    ConfigStorage,
    HealthCheckResult,
    JsonConfigStorage,
    ResourceHub,
    YamlConfigStorage,
    get_hub,
    set_global_hub,
)
from hush.core.states import (
    Cell,
    MemoryState,
    Ref,
    StateSchema,
)
from hush.core.utils import Param

__version__ = "0.1.0"

__all__ = [
    # Main engine
    "Hush",
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
    "get_hub",
    "set_global_hub",
    "HealthCheckResult",
    "ConfigStorage",
    "YamlConfigStorage",
    "JsonConfigStorage",
]
