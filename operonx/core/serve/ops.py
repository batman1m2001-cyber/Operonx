"""`ingress` and `egress` — the transport, as ops.

Not `serve(in_channel, out_channel)`. Keeping the transport inside the
graph is what buys real spans, sweepable contexts and per-item tracing;
an async seam beside the graph buys none of them, and the callbot spent a
month learning that with its Channels. The convenience form of
``engine.serve()`` should compile down to these two ops rather than
becoming a second path with its own semantics.

Neither op names a resource. The run was minted by a transport, so it
already carries its session — :func:`current_session` finds it, and
there is nothing to wire. When the session is absent the graph was
started directly rather than served, which is what keeps an
`ingress`-bearing graph testable with a plain ``engine.start()``.
"""

from __future__ import annotations

from typing import Any

from operonx.core.loggings import LOGGER
from operonx.core.ops import op

from .protocol import current_session

__all__ = ["ingress", "egress"]


@op(bound="io", transient=True)
async def ingress(items=None):
    """Yield each item the session receives, one per item.

    ``transient=True`` is what makes per-item dispatch affordable: the
    item is delivered to its consumer and the cell released with the
    context, so a long-lived session does not retain everything that ever
    crossed it. Without it a call retains every packet it received —
    measured at 23 KB per item before the fix.

    The generator ending *is* end-of-input, and it is the only such
    signal. Downstream ops drain and the run completes on its own; nothing
    outside cancels it. That is deliberate — work that has to happen after
    the peer has gone, like writing a call record, only survives if the
    run is allowed to finish.

    `items` is an escape hatch for tests and for direct
    ``engine.start()``: an iterable there is used when no session exists.
    """
    session = current_session()
    if session is None:
        for item in (items or ()):
            yield {"item": item}
        return

    count = 0
    try:
        async for item in session.recv():
            count += 1
            yield {"item": item}
    finally:
        LOGGER.debug(f"[serve] ingress ended after {count} item(s)")


@op(bound="io")
async def egress(item=None) -> dict:
    """Write one item back to the session that minted this run.

    A missing session is not an error — the graph is simply not being
    served — but it is reported, because a graph that believes it is
    replying to someone and is not would otherwise look identical to one
    that succeeded.
    """
    session = current_session()
    if session is None:
        LOGGER.debug("[serve] egress with no session; item dropped")
        return {"sent": False}
    try:
        return {"sent": bool(await session.send(item))}
    except Exception as exc:                              # noqa: BLE001
        # `Session.send` is specified to report failure rather than raise,
        # and a transport that breaks that promise should lose its item,
        # not the run. A peer disappearing mid-reply is ordinary; the run
        # may still have a record to write.
        LOGGER.error(f"[serve] session.send raised: {type(exc).__name__}: {exc}")
        return {"sent": False}
