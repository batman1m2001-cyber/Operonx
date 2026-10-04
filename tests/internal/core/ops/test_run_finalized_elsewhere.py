"""An op's generator closed outside its own task finishes its cleanup quietly.

When a run is cancelled while a generator op is suspended at a yield, its
``BaseOp.run`` generator is abandoned by the cancelled pump and closed later
by the event loop's async-generator finalizer, in another task and another
``contextvars.Context``. ``run()``'s ``finally`` reset its ``_current_op_ctx``
token there, and ``ContextVar.reset`` refuses a token from another context:
every such cancellation reported ``ValueError: <Token ...> was created in a
different Context`` to the loop's exception handler, and the rest of the
``finally`` never ran.
"""

import asyncio
import gc

from operonx import END, START, GraphOp, Operon, op
from operonx.core.workflow_trace import _current_op_ctx


async def test_generator_closed_by_the_finalizer_reports_nothing():
    reported = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, ctx: reported.append(ctx))
    released = asyncio.Event()
    try:

        @op
        async def endless():
            i = 0
            try:
                while True:
                    yield {"i": i}
                    i += 1
            finally:
                released.set()

        @op
        async def stuck(i: int) -> dict:
            await asyncio.sleep(10)
            return {"o": i}

        with GraphOp(name="parked") as g:
            s = endless()
            w = stuck(i=s["i"].sequential(max_pending=2))
            START >> s >> w >> END

        handle = Operon(g).start(inputs={})
        await asyncio.sleep(0.1)  # the producer is parked after its 3rd yield
        handle.cancel()
        await asyncio.wait_for(released.wait(), timeout=5)
        await asyncio.sleep(0.05)
        gc.collect()
        await asyncio.sleep(0.05)
    finally:
        loop.set_exception_handler(previous)

    assert [repr(c.get("exception") or c.get("message")) for c in reported] == []
    assert _current_op_ctx.get() is None  # nothing leaked into the caller's context
