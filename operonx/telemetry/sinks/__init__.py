"""Sink implementations for the V2 tracing surface.

A sink consumes ``TraceEvent`` stream produced by ``operonx.trace()`` and
renders it to some backend. This package ships:

- :class:`LangfuseSink` — send events to Langfuse as traces + observations.

Consumers can implement their own sink as a plain ``Callable[[TraceEvent], None]``.
"""

from operonx.telemetry.sinks.langfuse import LangfuseSink

__all__ = ["LangfuseSink"]
