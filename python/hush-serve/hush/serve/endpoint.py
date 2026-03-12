"""Endpoint — binds a GraphOp to route configuration."""

from typing import List, Optional, Type, Union

from pydantic import BaseModel

from hush.core import GraphOp, Hush
from hush.core.tracing import Tracer
from hush.serve.config import EndpointConfig
from hush.serve.schema import build_request_model, build_response_model


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

        # env/resources already loaded by HushApp.__init__
        self.engine = Hush(graph, env=False)

        self.request_model: Type[BaseModel] = build_request_model(graph)
        self.response_model: Type[BaseModel] = build_response_model(graph)
