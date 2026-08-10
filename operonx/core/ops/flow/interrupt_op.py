"""InterruptOp — first-class node for HITL suspend/resume.

Visible graph node that suspends its own execution, emits an
``InterruptEvent`` to the caller via ``MemoryState._notify_interrupt``,
and awaits a resume value posted by the caller via
``state._interrupt_responses[interrupt_id] = value`` (typically arranged
by an ``engine.stream()`` consumer's ``run.resume(value)`` call).

Chosen shape (see docs/design/STATE_LOOP_REFACTOR_PLAN.md §Rejected #10):
    - a **visible graph node**, not a hidden ``interrupt(...)`` callable
      inside op bodies — payload is wired via a normal ref, response
      exposed as an op output for downstream refs
    - suspension point is transparent in the DAG
    - respects ``@op(exclude=/include=)`` filter just like any other op

Example::

    with GraphOp(name="pipeline") as g:
        call    = call_llm(...)
        approve = InterruptOp(payload=call["plan"])
        do_it   = execute(plan=approve["response"])
        START >> call >> approve >> do_it >> END

    # Caller side:
    async for evt in engine.stream(inputs, mode="updates"):
        if isinstance(evt, InterruptEvent):
            answer = ask_human(evt.payload)
            run.resume(answer)
"""

import asyncio
import uuid
from typing import Any

from operonx.core.configs.op_config import OpType
from operonx.core.ops.base import BaseOp
from operonx.core.states._scratch_var import _current_state_var
from operonx.core.utils.common import Param

__all__ = ["InterruptOp"]


class InterruptOp(BaseOp):
    """Op that suspends until the caller posts a resume value.

    Args:
        payload: Value (typically a ``Ref``) sent to the caller via
            :class:`~operonx.checkpoint.InterruptEvent`.
        timeout: Optional wall-clock seconds. Falsey (0/None) = wait forever.

    Behaviour:
        - Emits ``InterruptEvent(step_id, op, ctx, payload, interrupt_id)``
          when dispatched.
        - Awaits ``state._interrupt_responses[interrupt_id]`` (a
          ``dict[str, Future]``) — the caller resolves this future.
        - Returns ``{"response": <value>}`` — downstream refs like
          ``approve["response"]`` read the answer.
        - Also exposes ``timed_out`` (bool) and ``interrupt_id`` (str) outputs.

    Design note:
        This op piggybacks on the state's interrupt bus. Full scheduler
        integration (Phase 2b3) wires ``engine.stream()`` to auto-subscribe
        a listener that hands out ``interrupt_id`` → ``asyncio.Future``
        pairs and lets ``run.resume(value)`` resolve them.
    """

    type: OpType = "interrupt"

    __slots__ = ("timeout",)

    def __init__(
        self,
        payload: Any = None,
        timeout: float = 0,
        **kwargs,
    ):
        kwargs.setdefault("inputs", {})
        if payload is not None and "payload" not in kwargs["inputs"]:
            kwargs["inputs"]["payload"] = payload

        super().__init__(**kwargs)
        self.timeout = timeout

        if self.inputs is None:
            self.inputs = {}
        if "payload" not in self.inputs:
            self.inputs["payload"] = Param(type=Any, required=False, default=None)

        # Declared outputs — downstream ops read approve["response"] etc.
        if self.outputs is None:
            self.outputs = {}
        for out_name in ("response", "timed_out", "interrupt_id"):
            if out_name not in self.outputs:
                self.outputs[out_name] = Param(type=Any, required=False, default=None)

        self._set_core(self._interrupt_impl)

    async def _interrupt_impl(self, payload: Any = None) -> dict:
        """Emit InterruptEvent, block until resume, return the response."""
        try:
            state = _current_state_var.get()
        except LookupError:
            # Outside a run — no bus, no resume. Return timed_out immediately.
            interrupt_id = uuid.uuid4().hex
            return {"response": None, "timed_out": True, "interrupt_id": interrupt_id}

        interrupt_id = uuid.uuid4().hex

        # Ensure the response bus exists on the state (created lazily so ops
        # that never interrupt don't carry the dict).
        if not hasattr(state, "_interrupt_responses") or state._interrupt_responses is None:
            # Attach as instance attribute (state uses __slots__ so this
            # requires a lazy-attach helper on MemoryState — see state.py).
            state._register_interrupt_bus()

        # Reserve the future BEFORE emitting so a fast-responding caller
        # can't miss the slot.
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        state._interrupt_responses[interrupt_id] = future

        # Emit the InterruptEvent via the state's bus.
        try:
            from operonx.core.workflow_trace import _current_op_ctx

            ctx = _current_op_ctx.get()
        except (ImportError, LookupError):
            from operonx.core.states.cell import DEFAULT_CONTEXT

            ctx = DEFAULT_CONTEXT

        state._notify_interrupt(self.full_name, ctx, payload, interrupt_id)

        # Await resume — optionally with timeout.
        timed_out = False
        try:
            if self.timeout and self.timeout > 0:
                response = await asyncio.wait_for(future, timeout=self.timeout)
            else:
                response = await future
        except asyncio.TimeoutError:
            timed_out = True
            response = None
        finally:
            # Clean up the reservation so the response bus stays lean.
            state._interrupt_responses.pop(interrupt_id, None)

        return {
            "response": response,
            "timed_out": timed_out,
            "interrupt_id": interrupt_id,
        }
