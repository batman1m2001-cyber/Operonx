"""Scheduler event types — `Frame`, `EOF`, `Interrupt`.

Lives outside ``operonx.core.ops.graph`` so importing it doesn't trigger
``graph/__init__.py`` (which loads ``graph_op`` -> ``base`` and would create
a circular import for any module under ``ops/`` that needs these types).
"""

from dataclasses import dataclass
from typing import Any, ClassVar


class _SelfContext:
    """Sentinel for ``Interrupt(ctx_to_cancel=…)`` meaning "my own ctx".

    Deliberately **not** a tuple. The scheduler substitutes the emitter's
    context before any sweep runs; if a code path ever forgets to, the
    sweep raises ``TypeError`` instead of matching every context — which
    is exactly the silent whole-run cancellation this sentinel replaced.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Interrupt.SELF"


SELF_CTX = _SelfContext()


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
    record the emitter for tracing; ``ctx_to_cancel`` is the target —
    typically the prior turn's ctx, stored in ``SCRATCH`` when long-running
    work began.

    ``ctx_to_cancel`` defaults to :data:`Interrupt.SELF`, which the
    scheduler resolves to the emitter's own context. Cancelling the whole
    run is spelled ``Interrupt.ALL`` and has to be asked for::

        Interrupt(reason="bad input")                    # this branch
        Interrupt(ctx_to_cancel=Interrupt.ALL, ...)      # everything

    The empty tuple used to be the default, and it is a prefix of every
    context — so omitting the argument discarded the entire run and
    returned cleanly, with no error anywhere for the caller to notice.

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
    ctx_to_cancel: Any = SELF_CTX
    reason: str = ""

    #: Resolved by the scheduler to the emitting op's own context.
    SELF: ClassVar[Any] = SELF_CTX
    #: Every context in the run. Destructive, so it must be written out.
    ALL: ClassVar[tuple] = ()
