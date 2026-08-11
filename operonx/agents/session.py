"""Multi-turn sessions — carrying a conversation across runs.

One `Operon.run()` is one *exchange*: the user says something, the agent
loops until it answers. A session is several of those sharing a history.

The history is held here rather than in a checkpointer because those
solve different problems. A checkpointer records every cell write for
replay and debugging; a session needs only the messages, and threading
them through is a list, not a store. Binding a checkpointer for
conversation continuity would be paying for a flight recorder to
remember what someone said.

Approval still belongs to the caller. `send()` takes an `on_approval`
callback and wires the interrupt bus for the duration of the run, so a
session works with gated tools without the caller re-deriving the harness
each turn.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

from operonx.agents.graphs.react import agent_result
from operonx.core.engine import Operon

__all__ = ["AgentSession"]


class AgentSession:
    """A conversation with an agent, across several turns.

    Args:
        agent: A **built** graph from ``build_react_agent(...)(messages=None)``.
            Built once and reused — rebuilding per turn re-runs graph
            construction and the cycle rewrite for no benefit.
        system: Optional system prompt, prepended once. It stays first
            for the life of the session, which is what lets a provider
            cache the prefix.
        checkpointer: Optional. Recorded for inspection via
            :attr:`checkpointer`; **not** how history is carried. Bind one
            when you want per-step replay, not to make the agent remember.
        timeout: Per-turn wall clock, seconds.

    Example::

        session = AgentSession(agent, system="You are terse.")
        await session.send("what files are here?")
        await session.send("delete the second one")   # sees turn 1
    """

    def __init__(
        self,
        agent: Any,
        *,
        system: str = "",
        checkpointer: Any = None,
        timeout: float = 600.0,
    ) -> None:
        self.agent = agent
        self.checkpointer = checkpointer
        self.timeout = timeout
        self._messages: List[dict] = []
        self._turns = 0
        if system and system.strip():
            self._messages.append({"role": "system", "content": system})

    @property
    def messages(self) -> List[dict]:
        """The conversation so far. A copy — the caller mutating this
        must not silently rewrite what the next turn sends."""
        return list(self._messages)

    @property
    def turns(self) -> int:
        """Model calls across the whole session, not just the last run."""
        return self._turns

    async def send(
        self,
        text: str,
        *,
        on_approval: Optional[Callable[[Any], None]] = None,
    ) -> Dict[str, Any]:
        """Send one user message and run the agent to an answer.

        Args:
            text: The user's message.
            on_approval: Called with an ``InterruptEvent`` when a gated
                tool needs a human. Answer with
                ``session.approve(event, True)``. Without a callback a
                gated call waits out its timeout and is then treated as
                declined — which is correct, but slow and opaque, so pass
                one whenever gated tools are reachable.

        Returns:
            The same shape as :func:`~operonx.agents.graphs.react.agent_result`,
            for this exchange.
        """
        previous = list(self._messages)
        self._messages.append({"role": "user", "content": text})

        engine = Operon(self.agent)
        handle = engine.start(
            inputs={"messages": self.messages},
            **({"checkpointer": self.checkpointer} if self.checkpointer is not None else {}),
        )
        self._handle = handle

        unsubscribe = None
        if on_approval is not None:
            from operonx.checkpoint import bind_interrupt_bus

            unsubscribe = bind_interrupt_bus(handle.state, sink=on_approval)

        try:
            await asyncio.wait_for(handle.result(), timeout=self.timeout)
        finally:
            if unsubscribe is not None:
                unsubscribe()

        # handle.result() is assembled from emitted frames and carries no
        # state, so the merged conversation is read from the run's state.
        result = agent_result(handle.state, self.agent)

        # A turn that ended without an assistant reply did not succeed.
        # operonx records op errors into state and returns a partial
        # result rather than raising, so a failed model call comes back
        # looking like a short conversation — and committing it left the
        # history ending on an unanswered user turn while `final` still
        # held the *previous* turn's reply. The caller was told the turn
        # succeeded and shown a stale answer.
        messages = result.get("messages") or []
        answered = bool(messages) and messages[-1].get("role") == "assistant"
        if not answered:
            # Roll back to before this turn so the caller can retry
            # without the history accumulating consecutive user turns
            # (which the provider rejects anyway).
            self._messages = previous
            return {
                **result,
                "messages": list(previous),
                "final": None,
                "error": (
                    "the agent produced no reply; an op raised and operonx "
                    "recorded it into state rather than propagating. Check "
                    "the logs. The conversation was left unchanged."
                ),
            }

        # Replace rather than append: the agent's cell already holds the
        # full conversation including what we sent, so appending its
        # output would duplicate every earlier turn.
        self._messages = list(messages)
        self._turns += int(result.get("turns") or 0)
        return {**result, "error": ""}

    def approve(self, event: Any, approved: bool, reason: str = "") -> bool:
        """Answer a pending approval. Returns whether it was still waiting.

        ``False`` means the request already expired — worth surfacing,
        since a human who answered a stale prompt will otherwise believe
        they authorised something that never ran.
        """
        handle = getattr(self, "_handle", None)
        if handle is None:
            return False
        payload = {"approved": bool(approved)}
        if reason:
            payload["reason"] = reason
        return bool(handle.state.resume_interrupt(event.interrupt_id, payload))

    def reset(self, keep_system: bool = True) -> None:
        """Clear the conversation. Keeps the system prompt by default —
        dropping it would silently change the agent's behaviour rather
        than just its memory."""
        system = [m for m in self._messages if m.get("role") == "system"]
        self._messages = system if keep_system else []
        self._turns = 0
