"""Legacy → new pipeline adapters.

Bridges from the old ``TraceFilter`` config to the new processor-chain
model so existing consumers can migrate one step at a time. Three migration
gateways:

  1. ``trace_filter_to_processors(tf)`` — convert a ``TraceFilter`` into an
     equivalent list of processors. Educa keeps its existing TraceFilter
     definitions and slots them into a new ``TracePipeline``.

  2. ``LegacyRewriter`` — wraps an old-style rewriter callable
     (``List[Dict] → List[Dict]``) so it can run inside the new processor
     chain. Useful only as a transitional step: rewriters expect a
     pre-built tree, not a flat event stream, and most cases collapse to
     ``GroupBy`` + ``Aggregate`` without needing this shim.

  3. ``LangfuseTracer.as_pipeline(...)`` (defined on the tracer class) —
     class method that returns a ``TracePipeline`` wired with
     ``LangfuseTreeExporter`` and the processors derived from any
     ``TraceFilter`` argument. The legacy constructor stays untouched
     (deprecated in T3, deleted in T4).

See ``docs/TRACING_REDESIGN_PLAN.md`` §6.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, Iterable, List

from operonx.core.tracing.events import TraceEvent
from operonx.core.tracing.pipeline import Processor

if TYPE_CHECKING:
    from operonx.core.tracing.trace_filter import TraceFilter

LOGGER = logging.getLogger("operonx.tracing")


def trace_filter_to_processors(tf: "TraceFilter") -> List[Processor]:
    """Convert a legacy ``TraceFilter`` into an equivalent processor chain.

    Drops fields that no longer apply under the flat event model
    (``preserve_children_of``, ``protected_types``) — these existed to
    repair tree damage that filtering caused, but the new model does
    tree-building inside the exporter, after filtering, so deleting
    events never orphans anything.

    ``rewriters`` are wrapped in ``LegacyRewriter`` if present, but a
    ``DeprecationWarning`` is logged — most rewriters can be replaced
    with a small ``GroupBy`` + ``Aggregate`` pair.

    ``exclude_kinds`` is dropped with a one-line warning. The legacy
    "kind" axis (``batch``, ``generator``, ``stream_context``, ...)
    described HOW a TraceNode was produced; the new ``EventKind`` axis
    describes WHAT happened (``op_start``, ``op_yield``, ...). The two
    don't map. Most callers using ``exclude_kinds`` were filtering out
    iteration scaffolding that the new event model doesn't emit at all.
    """
    from operonx.core.tracing.processors import (
        DropEmpty, DropOps, KeepOps, TruncateIO,
    )

    out: List[Processor] = []
    if tf.exclude_ops:
        out.append(DropOps(tf.exclude_ops))
    if tf.include_ops:
        out.append(KeepOps(tf.include_ops))
    if tf.skip_empty:
        out.append(DropEmpty())
    if tf.max_io_size and tf.max_io_size > 0:
        out.append(TruncateIO(tf.max_io_size))
    if tf.exclude_kinds:
        LOGGER.info(
            "trace_filter_to_processors: ignoring exclude_kinds=%s — the "
            "legacy 'kind' axis (batch/generator/stream_context) no longer "
            "applies under the event-stream model. Most uses of this field "
            "were filtering iteration scaffolding that the new model does "
            "not emit.",
            tf.exclude_kinds,
        )
    if tf.preserve_children_of:
        LOGGER.debug(
            "trace_filter_to_processors: dropping preserve_children_of=%s — "
            "no longer needed (flat event stream cannot orphan children).",
            tf.preserve_children_of,
        )
    if tf.protected_types and tf.protected_types != ["trace", "generation"]:
        LOGGER.debug(
            "trace_filter_to_processors: dropping protected_types=%s — "
            "exporters now decide observation taxonomy themselves.",
            tf.protected_types,
        )
    if tf.rewriters:
        import warnings
        warnings.warn(
            f"trace_filter_to_processors: wrapping {len(tf.rewriters)} "
            "rewriter(s) via LegacyRewriter. Rewriters consume a tree shape "
            "that doesn't exist in the new pipeline; consider migrating to "
            "GroupBy + Aggregate processors instead.",
            DeprecationWarning, stacklevel=2,
        )
        for r in tf.rewriters:
            out.append(LegacyRewriter(r))
    return out


class LegacyRewriter:
    """Wraps an old ``List[Dict] -> List[Dict]`` rewriter as a Processor.

    The rewriter is fed an empty list and its output is discarded — there
    is no automatic conversion from event stream to TraceNode tree. This
    class exists so the conversion call doesn't crash when a TraceFilter
    has rewriters set; users get a deprecation warning pointing them at
    GroupBy/Aggregate.

    Override ``__call__`` if you need actual interop. We don't ship that
    auto-conversion because the migration target (GroupBy + Aggregate) is
    cleaner anyway.
    """

    __slots__ = ("_fn",)

    def __init__(self, fn: Callable) -> None:
        self._fn = fn

    def __call__(self, events: Iterable[TraceEvent]) -> Iterable[TraceEvent]:
        # No-op pass-through. The legacy rewriter doesn't run.
        # See class docstring for why.
        for e in events:
            yield e
