"""Operon — high-performance workflow engine for AI applications.

Operon runs anything as a workflow — from IO-bound tasks like LLMs and
agents to CPU-bound workloads. Inspired by Airflow operators, it enforces
clear, consistent conventions for building scalable async workflows.

Example::

    from operonx import Operon, GraphOp, START, END, PARENT, op

    @op
    def double(x: int):
        return {"result": x * 2}

    with GraphOp(name="workflow") as graph:
        step = double(x=PARENT["input"])
        START >> step >> END

    engine = Operon(graph)
    result = await engine.run(inputs={"input": 5})

Sub-namespaces:
- ``operonx.core``       — engine, ops, state, tracing, registry
- ``operonx.providers``  — LLM, embedding, reranker, ONNX backends
- ``operonx.telemetry``  — Langfuse, OTEL, local tracers
"""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Optional, Union

from operonx.core import (
    END,
    LOGGER,
    PARENT,
    PENDING,
    SCRATCH,
    START,
    BranchOp,
    FuncOp,
    GraphOp,
    Interrupt,
    Operon,
    ParserOp,
    graph,
    op,
)
from operonx.core.registry import (
    BOOTSTRAP_ENV_PATHS,
    ResourceHub,
)

try:
    __version__ = version("operonx")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"


def bootstrap(
    *,
    resources: Optional[Union[str, Path]] = None,
    env: bool = True,
) -> Optional[ResourceHub]:
    """One-line setup for ``.env`` and :class:`ResourceHub`.

    - When ``env`` is ``True`` (default), load ``./.env`` from CWD using
      ``python-dotenv`` (non-override; existing env wins). The path is
      recorded in ``BOOTSTRAP_ENV_PATHS`` for later diagnostic messages.
    - When ``resources`` is a path, install the hub via
      :meth:`ResourceHub.from_yaml`.
    - When ``resources`` is ``None``, call :meth:`ResourceHub.auto` —
      which checks ``./resources.yaml`` and warns on miss.
    - Idempotent: if a hub is already installed, return it unchanged.

    Returns the installed hub, or ``None`` if no ``resources.yaml`` was
    found and none was provided. Pure-compute graphs that don't need a
    hub can ignore the return value.
    """
    if env:
        _load_env_into_bootstrap()

    if ResourceHub._instance is not None:
        return ResourceHub._instance

    if resources is not None:
        hub = ResourceHub.from_yaml(resources)
        ResourceHub.set_instance(hub)
        return hub

    return ResourceHub.auto()


def _load_env_into_bootstrap() -> None:
    """Load ``./.env`` from CWD if it exists; record the path.

    Records the absolute path in ``BOOTSTRAP_ENV_PATHS`` regardless of
    whether the file existed — this is what the env-var error message
    surfaces, so users see exactly where ``.env`` was searched.
    """
    env_path = (Path.cwd() / ".env").resolve()
    if env_path not in BOOTSTRAP_ENV_PATHS:
        BOOTSTRAP_ENV_PATHS.append(env_path)
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path, override=False)


__all__ = [
    # Engine
    "Operon",
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
    "SCRATCH",
    # Scheduler events
    "Interrupt",
    # Logging
    "LOGGER",
    # Setup
    "bootstrap",
    "ResourceHub",
    # Version
    "__version__",
]
