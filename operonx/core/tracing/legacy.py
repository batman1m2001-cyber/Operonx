"""Legacy → new pipeline adapters.

Converts the old ``TraceFilter`` config into the new processor-chain
model so existing consumers (educa) can keep their TraceFilter
definitions while running under a ``TracePipeline``.

See ``docs/TRACING_REDESIGN_PLAN.md`` §6.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List

from operonx.core.tracing.pipeline import Processor

if TYPE_CHECKING:
    from operonx.core.tracing.trace_filter import TraceFilter

LOGGER = logging.getLogger("operonx.tracing")


def trace_filter_to_processors(tf: "TraceFilter") -> List[Processor]:
    """Convert a legacy ``TraceFilter`` into an equivalent processor chain.

    Drops fields that no longer apply under the flat event model
    (``preserve_children_of``, ``protected_types``, ``exclude_kinds``) —
    these existed to repair tree damage that filtering caused, but the
    new model does tree-building inside the exporter, after filtering,
    so deleting events never orphans anything.
    """
    from operonx.core.tracing.processors import (
        DropEmpty,
        DropOps,
        KeepOps,
        TruncateIO,
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
            "applies under the event-stream model.",
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
    return out
