"""Concrete V3 workflow-trace consumers.

* :class:`LocalConsumer` — generic disk layout (`meta.json`,
  `nodes.jsonl`, `view.txt`, `media/`). Works for any operonx
  workflow. See ``docs/TRACING_V3_DESIGN.md`` §4.
* :class:`LangfuseConsumer` — batch-ship the trace to Langfuse at end
  of call. See ``docs/TRACING_V3_DESIGN.md`` §6/8.

Importing this module registers both consumer types with the
:mod:`operonx.core.registry` ``REGISTRY``, so a ``resources.yaml``
entry like ``trace_local: {default: {…}}`` resolves to a
:class:`LocalConsumer` instance via
``ResourceHub.instance().get("trace_local:default")``.

App-specific subclasses (`CallbotLocalConsumer`, `N8nViewConsumer`,
…) live in the calling app and register their own configs the same
way.
"""

from operonx.core.registry import REGISTRY
from operonx.telemetry.consumers.langfuse import (
    LangfuseConsumer,
    LangfuseConsumerConfig,
    _create_langfuse_consumer,
)
from operonx.telemetry.consumers.local import (
    LocalConsumer,
    LocalConsumerConfig,
    _create_local_consumer,
)

__all__ = [
    "LangfuseConsumer",
    "LangfuseConsumerConfig",
    "LocalConsumer",
    "LocalConsumerConfig",
]


# Register at import time — side effect that mirrors
# `operonx/telemetry/plugin.py`. Guarded because tests may reset the
# registry between runs; re-register is idempotent per config class.
REGISTRY.register(LocalConsumerConfig, _create_local_consumer)
REGISTRY.register(LangfuseConsumerConfig, _create_langfuse_consumer)
