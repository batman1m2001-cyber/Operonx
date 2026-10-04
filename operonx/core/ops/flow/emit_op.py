"""EmitOp — first-class node for custom-stream events.

Visible graph node that pushes a payload to the caller's ``mode="custom"``
stream via ``MemoryState._notify_custom``. Fire-and-forget: if no
subscriber is listening the payload is dropped silently (no back-pressure).

Chosen shape (see docs/design/STATE_LOOP_REFACTOR_PLAN.md §Rejected #11):
    - a **visible graph node**, not a hidden ``emit(...)`` callable inside
      op bodies — payload is wired via a normal ref, channel via an init kwarg
    - subscribed to by anyone calling ``engine.stream(mode="custom")`` or
      directly via ``state.subscribe_custom(...)``
    - respects ``@op(exclude=/include=)`` filter just like any other op

Example::

    with GraphOp(name="pipeline") as g:
        call = call_llm(...)
        telem = EmitOp(payload=call["progress"], channel="ui")
        START >> call >> telem >> next_op >> END
"""

import inspect
from typing import Any, Optional

from operonx.core.configs.op_config import OpType
from operonx.core.ops.base import BaseOp
from operonx.core.runtime import _current_ctx
from operonx.core.states._scratch_var import _current_state_var
from operonx.core.states.cell import DEFAULT_CONTEXT
from operonx.core.utils.common import Param

__all__ = ["EmitOp"]


class EmitOp(BaseOp):
    """Op that emits a ``CustomEvent`` to ``mode="custom"`` subscribers.

    Args:
        payload: Value (typically a ``Ref``) to include in the emitted event.
        channel: Free-form string label used by consumers for filtering.
            Defaults to ``"default"``.

    Behaviour:
        - Fires once per invocation (per ctx for streaming/parallel graphs).
        - Fire-and-forget: no subscriber → payload dropped, no error.
        - Emits ``CustomEvent(step_id, op, ctx, channel, payload)`` via
          ``state._notify_custom`` — scheduler provides ``step_id`` and ``ctx``
          via ContextVar plumbing (matches ``SCRATCH``'s pattern).
    """

    type: OpType = "emit"

    __slots__ = ("channel",)

    def __init__(
        self,
        payload: Any = None,
        channel: str = "default",
        **kwargs,
    ):
        # Payload becomes a declared input — enables ref wiring.
        kwargs.setdefault("inputs", {})
        if payload is not None and "payload" not in kwargs["inputs"]:
            kwargs["inputs"]["payload"] = payload

        super().__init__(**kwargs)
        self.channel = channel

        # Auto-declare "payload" input if missing so the schema tracks it.
        if self.inputs is None:
            self.inputs = {}
        if "payload" not in self.inputs:
            self.inputs["payload"] = Param(type=Any, required=False, default=None)

        # No outputs — fire-and-forget.
        if self.outputs is None:
            self.outputs = {}

        self._set_core(self._emit_impl)

    async def _emit_impl(self, payload: Any = None) -> dict:
        """Notify the state's custom-event bus. Returns empty dict (no outputs)."""
        try:
            state = _current_state_var.get()
        except LookupError:
            # Running outside an active run (test fixture, dry validation).
            # Silently swallow — same shape as SCRATCH's outside-run reads.
            return {}

        # The invocation's ctx, from its frame. DEFAULT_CONTEXT when there is
        # none — matches EmitOp's fire-and-forget contract.
        ctx = _current_ctx() or DEFAULT_CONTEXT

        state._notify_custom(self.full_name, ctx, self.channel, payload)
        return {}


# Note: no @op-style shorthand factory. EmitOp is instantiated directly like
# BranchOp — it doesn't have a user-supplied function body.
