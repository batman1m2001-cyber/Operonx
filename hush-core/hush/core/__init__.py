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

from hush.core.background import (
    BackgroundProcess,
    get_background,
    shutdown_background,
)
from hush.core.configs import (
    EdgeConfig,
    EdgeType,
)
from hush.core.engine import Hush
from hush.core.loggings import LOGGER
from hush.core.nodes import (
    END,
    PARENT,
    START,
    AsyncIterNode,
    BaseNode,
    BranchNode,
    CodeNode,
    DummyNode,
    Each,
    ForLoopNode,
    GraphNode,
    MapNode,
    ParserNode,
    ParserType,
    WhileLoopNode,
    aiter_,
    code_node,
    for_,
    map_,
    while_,
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
from hush.core.streams import (
    STREAM_SERVICE,
)
from hush.core.tracers import (
    MEDIA_KEY,
    BaseTracer,
    MediaAttachment,
    get_registered_tracers,
    register_tracer,
    serialize_media_attachments,
)
from hush.core.utils import Param

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
