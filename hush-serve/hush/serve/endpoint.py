"""Endpoint — binds a GraphOp to route configuration."""

from typing import List, Optional, Type, Union

from pydantic import BaseModel

from hush.core import GraphOp, Hush
from hush.core.tracing import Tracer
from hush.serve.config import EndpointConfig
from hush.serve.schema import build_request_model, build_response_model


def _discover_streaming_channels(graph: GraphOp) -> list:
    """Walk graph._ops recursively and find ops with stream=True.

    Returns channel names (op.identity format) that will push chunks
    to STREAM_SERVICE during execution.
    """
    channels = []

    def _walk(g, prefix: str = ""):
        ops = getattr(g, "_ops", {})
        for name, child_op in ops.items():
            full_name = f"{prefix}.{name}" if prefix else f"{g.name}.{name}"
            if getattr(child_op, "stream", False):
                channels.append(f"{full_name}[main]")
            if hasattr(child_op, "_ops") and child_op._ops:
                _walk(child_op, full_name)

    _walk(graph)
    return channels


class Endpoint:
    """A single API endpoint backed by a Hush workflow.

    Holds the compiled engine, auto-generated request/response models,
    and route configuration.
    """

    __slots__ = [
        "config",
        "engine",
        "graph",
        "request_model",
        "response_model",
        "streaming_channels",
        "tracer",
    ]

    def __init__(
        self,
        graph: GraphOp,
        config: EndpointConfig,
        tracer: Optional[Union[Tracer, List[Tracer]]] = None,
    ):
        self.graph = graph
        self.config = config
        self.tracer = tracer

        self.engine = Hush(graph, mode=config.mode)

        self.request_model: Type[BaseModel] = build_request_model(graph)
        self.response_model: Type[BaseModel] = build_response_model(graph)

        if config.stream is None:
            self.streaming_channels = _discover_streaming_channels(graph)
            config.stream = len(self.streaming_channels) > 0
        elif config.stream:
            self.streaming_channels = _discover_streaming_channels(graph)
        else:
            self.streaming_channels = []
