"""Operon — high-performance workflow engine for AI applications.

Operon runs anything as a workflow — from IO-bound tasks like LLMs and
agents to CPU-bound workloads. Inspired by Airflow operators, it enforces
clear, consistent conventions for building scalable async workflows.

Example::

    from operon import Operon, GraphOp, START, END, PARENT, op

    @op
    def double(x: int):
        return {"result": x * 2}

    with GraphOp(name="workflow") as graph:
        step = double(x=PARENT["input"])
        START >> step >> END

    engine = Operon(graph)
    result = await engine.run(inputs={"input": 5})

Sub-namespaces:
- ``operon.core``       — engine, ops, state, tracing, registry
- ``operon.providers``  — LLM, embedding, reranker, ONNX backends
- ``operon.telemetry``  — Langfuse, OTEL, local tracers
"""

from operon.core import (
    END,
    PARENT,
    PENDING,
    START,
    BranchOp,
    FuncOp,
    GraphOp,
    LOGGER,
    Middleware,
    Operon,
    ParserOp,
    graph,
    op,
)

__version__ = "0.6.0"

__all__ = [
    # Engine
    "Operon",
    "Middleware",
    # Core op types
    "GraphOp",
    "BranchOp",
    "FuncOp",
    "ParserOp",
    # Decorators
    "op",
    "graph",
    # Markers
    "START",
    "END",
    "PARENT",
    "PENDING",
    # Logging
    "LOGGER",
    # Version
    "__version__",
]
