"""Scheduler event types — `Frame`, `EOF`, `Interrupt`.

Lives outside ``operonx.core.ops.graph`` so importing it doesn't trigger
``graph/__init__.py`` (which loads ``graph_op`` -> ``base`` and would create
a circular import for any module under ``ops/`` that needs these types).
"""

from dataclasses import dataclass


@dataclass
class Frame:
    """One result yielded by an op during execution.

    Created by ``Scheduler._pump()`` for every ``(ctx, result)`` tuple that
    ``op.run()`` yields. User code never constructs Frame directly — just
    ``return`` or ``yield`` from an ``@op`` function.
    """

    op: str
    ctx: tuple
    result: dict


@dataclass
class EOF:
    """Marker that an op's async generator has exhausted.

    Created by ``Scheduler._pump()`` after ``op.run()`` stops yielding
    (i.e. the underlying function returned or its generator was exhausted).
    User code never yields EOF — it is emitted implicitly when the op finishes.
    """

    op: str
    ctx: tuple


@dataclass
class Interrupt:
    """In-band scheduler cancellation event.

    Returned/yielded by user op bodies to cancel queued frames + in-flight
    tasks at ``ctx_to_cancel`` (and its descendants). ``op`` and ``ctx``
    record the emitter for tracing; ``ctx_to_cancel`` is the explicit target
    — typically the prior turn's ctx, stored in ``SCRATCH`` when long-running
    work began.

    The scheduler:
        1. Drops Frame/EOF items at ctx_to_cancel from the queue.
        2. Cancels in-flight ``_pump`` tasks at ctx_to_cancel and descendants
           (skipping the emitter to avoid self-cancel).
        3. Clears bookkeeping (ready/seq_active/seq_origins/collect_bufs).
        4. Forwards a synthetic ``("__interrupt__", emitter_ctx, {...})``
           tuple to ``output_queue`` so ``ExecutionHandle`` consumers see it.

    Best-effort: data already pushed to consumer-owned queues (e.g. a
    user-supplied ``asyncio.Queue``) is NOT drained — consumer must handle
    that itself (see plan §4.6a).
    """

    op: str = ""
    ctx: tuple = ()
    ctx_to_cancel: tuple = ()
    reason: str = ""
