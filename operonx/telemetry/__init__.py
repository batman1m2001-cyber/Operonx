"""Operon telemetry — V3 Consumer surface + Langfuse backend clients.

V3 tracing (the current design) records every op invocation into a
`WorkflowTrace` on the handle. `Consumer` subclasses read the trace
after the run — see :mod:`operonx.telemetry.consumers` for `Local` and
`Langfuse` implementations.

The Langfuse backend (`LangfuseClient`, `LangfuseConfig`,
`LangfusePromptManager`) is kept here so prompt management + the HTTP
client are usable independently of the trace pipeline. `LangfuseConsumer`
consumes a `LangfuseClient` via a `client_resource:` reference in
``resources.yaml``.

Example — wire a Local consumer via ResourceHub::

    # resources.yaml
    trace_local:
      default:
        root: /tmp/operonx_traces

    # your code
    from operonx import Operon, bootstrap
    bootstrap(resources="resources.yaml")
    engine = Operon(graph, trace="trace_local:default")

Example — mix ResourceHub keys + direct instances::

    engine = Operon(graph, trace=[
        "trace_langfuse:default",
        MyDebugConsumer(),
    ])

Prompt management (uses the Langfuse SDK directly)::

    from operonx.telemetry import LangfuseConfig, LangfusePromptManager

    pm = LangfusePromptManager(config=LangfuseConfig.from_env())
    prompt = pm["my-prompt"]
"""

# Import consumers first so their config types register with REGISTRY
# before anything else reads it.
import operonx.telemetry.consumers  # noqa: F401
from operonx.core.registry import REGISTRY
from operonx.telemetry.backends import (
    LangfuseClient,
    LangfuseConfig,
    LangfusePromptManager,
)

# Register the Langfuse client config for ResourceHub — used to be in
# the deleted `operonx.telemetry.plugin` module.
REGISTRY.register(LangfuseConfig, lambda c: LangfuseClient(c))


__all__ = [
    "LangfuseConfig",
    "LangfuseClient",
    "LangfusePromptManager",
]
