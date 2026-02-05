"""
Hush Core - Workflow Engine

A powerful async workflow orchestration framework.

Example:
    ```python
    from hush.core import Hush, GraphNode, START, END, PARENT
    from hush.core.nodes import CodeNode

    # Define graph
    with GraphNode(name="my-workflow") as graph:
        node = CodeNode(name="processor", ...)
        START >> node >> END

    # Create engine and run
    engine = Hush(graph)
    result = await engine.run(inputs={"key": "value"})
    print(result["output"])   # workflow output
    print(result["$state"])   # access state for debugging
    ```
"""

from hush.core.engine import Hush
from hush.core.nodes import (
    BaseNode,
    DummyNode,
    GraphNode,
    BranchNode,
    Each,
    ForLoopNode,
    for_,
    MapNode,
    map_,
    WhileLoopNode,
    while_,
    AsyncIterNode,
    aiter_,
    CodeNode,
    code_node,
    ParserNode,
    ParserType,
    START,
    END,
    PARENT,
)
from hush.core.states import (
    StateSchema,
    MemoryState,
    Ref,
    Cell,
)
from hush.core.configs import (
    EdgeConfig,
    EdgeType,
)
from hush.core.utils import Param
from hush.core.streams import (
    STREAM_SERVICE,
)
from hush.core.loggings import LOGGER
from hush.core.registry import (
    ResourceHub,
    ConfigRegistry,
    ConfigEntry,
    REGISTRY,
    CacheEntry,
    get_hub,
    set_global_hub,
    HealthCheckResult,
    ConfigStorage,
    YamlConfigStorage,
    JsonConfigStorage,
)
from hush.core.tracers import (
    BaseTracer,
    register_tracer,
    get_registered_tracers,
    MEDIA_KEY,
    MediaAttachment,
    serialize_media_attachments,
)
from hush.core.background import (
    get_background,
    shutdown_background,
    BackgroundProcess,
)

__version__ = "0.1.0"

__all__ = [
    # Main engine
    "Hush",
    # Nodes
    "BaseNode",
    "DummyNode",
    "GraphNode",
    "BranchNode",
    "Each",
    "ForLoopNode",
    "for_",
    "MapNode",
    "map_",
    "WhileLoopNode",
    "while_",
    "AsyncIterNode",
    "aiter_",
    "CodeNode",
    "code_node",
    "ParserNode",
    "ParserType",
    # Markers
    "START",
    "END",
    "PARENT",
    # State
    "StateSchema",
    "BaseState",
    "MemoryState",
    "RedisState",
    # Config
    "NodeConfig",
    "NodeType",
    "EdgeConfig",
    "EdgeType",
    # Schema
    "Param",
    # Streams
    "STREAM_SERVICE",
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
    # Tracers
    "BaseTracer",
    "register_tracer",
    "get_registered_tracers",
    "MEDIA_KEY",
    "MediaAttachment",
    "serialize_media_attachments",
    # Background
    "get_background",
    "shutdown_background",
    "BackgroundProcess",
]
