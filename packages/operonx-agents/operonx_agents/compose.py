"""Composition: an agent as another agent's tool, and as a graph's op.

**Agent as a tool** (``billing.as_tool(name="ask_billing")``). The parent's
model hands the child a self-contained ``task``; the child runs as its own
run — its own conversation, its own tools (an agent can only run the tools
it owns, so delegation cannot reach a tool the parent did not give the
child) — and answers with its final output, never its transcript. Its
turns, model and tool calls are child executions under the parent's tool
call, and its spending counts toward the parent's limits.

The child's run id is :func:`~operonx_agents.run.interruption.child_run_id`
of the parent's call (operonx's ``invocation_key``, like an interruption's
id), and it is saved in the parent's store. So:

- **an approval inside the child surfaces on the parent**: the child ends
  ``interrupted``, the parent's call parks with the child's
  interruptions (``path`` names both agents), and the parent's
  ``Runner.resume(..., approvals=...)`` resumes the child with its share
  of the answers, then carries on with the child's output;
- a parent resumed after a crash finds the child's run and resumes it
  (the child's own journal decides what re-runs), which is why the tool is
  ``idempotent``.

**Agent as an op** (``AgentOp.of(agent=support, input=...)``, or
``support.as_op()`` for a factory with the options bound). A step of a graph:

- ``AgentOp.of(agent=support, input=...)``: one frame; outputs
  ``output``, ``status``, ``usage``, ``interruptions``, ``state_id``,
  ``error`` that bind downstream like any op's. An interrupted run is
  answered outside the graph with
  ``Runner.resume(agent, state_id, store=..., approvals=...)``.
- ``AgentOp.of(agent=support, stream=True, input=...)``: a transient
  generator whose one output, ``event``, yields every :mod:`~operonx_agents.run.events` event, the
  last a ``RunFinished`` holding the result. Wire it to an ``EmitOp`` to
  reach ``engine.stream(mode="custom")``::

      run = AgentOp.of(agent=support, stream=True, input=question)
      EmitOp(payload=run["event"], channel="agent", transient=True)

  The two are separate ops because operonx runs a consumer once per frame
  a producer yields: one op streaming events *and* binding ``output``
  downstream would run that consumer once per event, without its input.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Dict, Optional

from operonx.core.ops import BaseOp
from operonx.core.ops.base import shorthand, split_shorthand_kwargs
from operonx.core.utils.auto_name import register_skip
from operonx.core.utils.common import Param
from pydantic import BaseModel

from operonx_agents.errors import AgentsError, Interrupted
from operonx_agents.run.context import RunContext
from operonx_agents.run.interruption import child_run_id
from operonx_agents.tools.tool import Tool, tool

if TYPE_CHECKING:
    from operonx_agents.agent import Agent
    from operonx_agents.context.session import Session
    from operonx_agents.run.result import RunResult
    from operonx_agents.run.store import StateStore

__all__ = ["AgentOp", "agent_op", "agent_tool"]

#: What the parent's model reads when the child stops short of an answer.
CHILD_STOPPED = "the {agent} agent stopped ({status}): {why}"
CHILD_LIMIT = "\n\n[the {agent} agent hit its {limit} limit; this answer may be incomplete]"


def agent_tool(
    agent: "Agent",
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    max_turns: Optional[int] = None,
    **spec: Any,
) -> Tool:
    """``agent`` as a :class:`~operonx_agents.Tool` taking one ``task``.

    Args:
        name: What the parent's model calls it (default: the agent's name).
        description: What the parent's model reads to decide when to call
            it (default: a generic "ask the <name> agent").
        max_turns: The child's turn budget, over its ``limits.turns``.
        **spec: :func:`~operonx_agents.tool` options (``timeout``,
            ``approval``, ``sequential``, ...).
    """
    child_agent = agent
    if max_turns is not None:
        child_agent = agent.clone(limits=dataclasses.replace(agent.limits, turns=max_turns))
    tool_name = name or agent.name
    text = description or (
        f"Ask the {agent.name} agent to do a task. It sees the task and nothing else of this "
        "conversation, and answers with its result."
    )

    async def delegate(ctx: RunContext, task: str) -> str:
        """Hand a task to another agent.

        Args:
            task: A self-contained instruction: the agent sees this and nothing
                else of the conversation, so include every detail it needs.
        """
        return await _delegate(child_agent, ctx, task, tool_name)

    spec.setdefault("idempotent", True)
    made = tool(delegate, name=tool_name, description=text, **spec)
    return dataclasses.replace(made, kind="agent", target=agent)


async def _delegate(agent: "Agent", ctx: RunContext, task: str, tool_name: str) -> str:
    from operonx_agents.run.runner import Runner

    run_id = None
    if ctx.run_id is not None and ctx.tool_call_id is not None:
        run_id = child_run_id(ctx.run_id, ctx.agent or "", tool_name, ctx.turn, ctx.tool_call_id)
    store = ctx.store
    saved = await store.load(run_id) if store is not None and run_id is not None else None
    if saved is None:
        res = await Runner.run(agent, task, deps=ctx.deps, store=store, run_id=run_id, parent=ctx)
    else:
        waiting = {i["id"] for i in saved.pending.interruptions} if saved.pending else set()
        answers = {k: v for k, v in ctx.approvals.items() if k in waiting}
        res = await Runner.resume(
            agent, run_id, store=store, approvals=answers, deps=ctx.deps, parent=ctx
        )
    return _answer(agent, res)


def _answer(agent: "Agent", res: "RunResult") -> str:
    """The child's output as the parent's tool result."""
    if res.status == "interrupted":
        raise Interrupted(res.interruptions)
    if res.status in ("completed", "limit") and res.output is not None:
        output = res.output
        text = output.model_dump_json() if isinstance(output, BaseModel) else str(output)
        if res.status == "limit":
            text += CHILD_LIMIT.format(agent=agent.name, limit=res.limit_hit)
        return text
    why = res.error or (f"its {res.limit_hit} limit ran out" if res.limit_hit else "no answer")
    raise AgentsError(CHILD_STOPPED.format(agent=agent.name, status=res.status, why=why))


# ── as_op ──────────────────────────────────────────────────────────────

_RESULT_OUTPUTS = ("output", "status", "usage", "interruptions", "state_id", "error")


def agent_op(
    agent: "Agent",
    *,
    stream: bool = False,
    store: Optional["StateStore"] = None,
    sessions: Optional[Callable[[str], "Session"]] = None,
    durability: str = "turn",
):
    """An op factory running ``agent``: call it inside a ``@graph`` with
    ``input`` (and ``session_id``, ``deps``).

    Args:
        stream: ``False``: the result as outputs. ``True``: every event on
            a transient ``event`` output (see the module docs).
        store: Where runs are saved: an interrupted run waits there.
        sessions: ``session_id -> Session``, for an op given a
            ``session_id``.
        durability: The runs' ``durability``.
    """
    config = dict(agent=agent, stream=stream, store=store, sessions=sessions, durability=durability)

    def factory(**kwargs: Any) -> AgentOp:
        inputs, init_kwargs = split_shorthand_kwargs(kwargs)
        return AgentOp(**config, inputs=inputs or None, **init_kwargs)

    register_skip(factory)  # the op is named after the variable it is assigned to
    factory.config = config  # type: ignore[attr-defined]
    return factory


class AgentOp(BaseOp):
    """An agent as one step of a graph: ``AgentOp.of(agent=..., input=...)``,
    like any op's ``.of``. See the module docs for the outputs."""

    show_keys_default = ("status", "output")

    __slots__ = ["agent", "stream", "store", "sessions", "durability"]

    type = "agent"

    def __init__(
        self,
        *,
        agent: "Agent",
        stream: bool = False,
        store: Optional["StateStore"] = None,
        sessions: Optional[Callable[[str], "Session"]] = None,
        durability: str = "turn",
        inputs: Optional[Dict[str, Any]] = None,
        outputs: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("bound", "io")
        if stream:
            kwargs.setdefault("transient", True)
        super().__init__(**kwargs)
        self.agent = agent
        self.stream = stream
        self.store = store
        self.sessions = sessions
        self.durability = durability
        input_schema = {
            "input": Param(required=True),
            "session_id": Param(type=str, required=False, default=None),
            "deps": Param(required=False, default=None),
        }
        if stream:
            output_schema = {"event": Param(default=None)}
        else:
            output_schema = {name: Param(default=None) for name in _RESULT_OUTPUTS}
        self.inputs = self._merge_params(input_schema, self._normalize_params(inputs))
        self.outputs = self._merge_params(output_schema, self._normalize_params(outputs))
        self._set_core(self._events if stream else self._run)

    @shorthand
    def of(
        cls,
        agent: "Agent",
        stream: bool = False,
        store: Optional["StateStore"] = None,
        sessions: Optional[Callable[[str], "Session"]] = None,
        durability: str = "turn",
        **kwargs: Any,
    ) -> "AgentOp":
        """Run ``agent`` as a step: ``input`` (and ``session_id``, ``deps``) bind
        like any op's inputs.

        Example::

            website = AgentOp.of(agent=researcher, input=tasks["website"])
            website["output"] >> PARENT["website"]

        Args:
            agent: The :class:`~operonx_agents.Agent` it runs.
            stream: ``True``: every event on a transient ``event`` output.
            store: Where runs are saved: an interrupted run waits there.
            sessions: ``session_id -> Session``, for a step given a ``session_id``.
            durability: The runs' ``durability``.
        """
        inputs, init_kwargs = split_shorthand_kwargs(kwargs)
        return cls(agent=agent, stream=stream, store=store, sessions=sessions,
                   durability=durability, inputs=inputs or None, **init_kwargs)  # fmt: skip

    def warmup(self) -> None:
        """Resolve the model's resources at engine start, so a missing one
        fails the build rather than the first call."""
        for resource in self.agent.model.resources:
            self.agent.model.llm(resource)

    def _session(self, session_id: Optional[str]) -> Optional["Session"]:
        if session_id is None:
            return None
        if self.sessions is None:
            raise ValueError(
                f"op {self.name!r} was given session_id={session_id!r} and has no sessions=: "
                "pass as_op(sessions=lambda sid: RedisSession(sid, url=...))."
            )
        return self.sessions(session_id)

    def _options(self, session_id: Optional[str], deps: Any) -> Dict[str, Any]:
        return dict(
            deps=deps,
            session=self._session(session_id),
            store=self.store,
            durability=self.durability,
        )

    async def _run(self, input: Any, session_id: Optional[str] = None, deps: Any = None) -> dict:
        from operonx_agents.run.runner import Runner

        res = await Runner.run(self.agent, input, **self._options(session_id, deps))
        return _outputs(res)

    async def _events(
        self, input: Any, session_id: Optional[str] = None, deps: Any = None
    ) -> AsyncIterator[dict]:
        from operonx_agents.run.runner import Runner

        async for event in Runner.stream(self.agent, input, **self._options(session_id, deps)):
            yield {"event": event}

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        return {"agent": self.agent.name, "model": self.agent.model.resource}


def _outputs(res: "RunResult") -> Dict[str, Any]:
    data = res.to_dict()
    output = res.output
    return {
        "output": output,
        "status": res.status,
        "usage": data["usage"],
        "interruptions": data["interruptions"],
        "state_id": res.state_id,
        "error": res.error,
    }
