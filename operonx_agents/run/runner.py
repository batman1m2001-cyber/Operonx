"""``Runner`` — the agent loop, as plain ``async`` code.

::

    res = await Runner.run(agent, "refund order A1B2C3D4", deps=deps,
                           session=RedisSession("chat:42", url=...),
                           store=RedisStateStore(url=...))
    async for event in Runner.stream(agent, "hi"): ...      # ends with RunFinished
    res = await Runner.resume(agent, res.run_id, store=store)
    res = await Runner.resume(agent, res.run_id, store=store,       # an interrupted run
                              approvals={i.id: Approve() for i in res.interruptions})

Each turn::

    caps (before)    a spent budget ends the run; the last budgeted turn is
                     told so and cannot call tools
    compaction       when the last prompt crossed ContextPolicy's threshold
    model call       hooks.before_model; streamed: TextDelta / ReasoningDelta
                     events, real usage; hooks.after_model
    caps (after)     an overrun ends the run, calls answered "not run"
    output           an answer ends the run; one that does not validate is
                     re-asked next turn, at most output_retries times
    tools            dispatch: one tool message per call, child executions;
                     a call that needs a human parks the turn
    commit           the turn's items to the session and the state to the
                     store, together, after the whole turn

**Approvals are interruptions.** A call that needs a human (its tool's
``approval``, the policy's ``ask``, a hook's ``Ask``, a sub-agent's own
approval) does not wait in the process: the turn is parked in the
:class:`RunState` with the calls that finished, the run ends
``interrupted`` with an :class:`~operonx_agents.Interruption` per waiting
call, and ``Runner.resume(..., approvals={id: Approve() | Deny()})`` — in
this process or another — finishes the turn and carries on. That needs a
``store``; without one, such a call is refused (fail closed). A hook's
:class:`~operonx_agents.Tripwire` ends the run ``blocked``.

**Commit only at turn boundaries.** A turn cancelled, failed or cut by the
wall clock writes nothing, so the session never holds half a turn and a
superseded run leaves no trace. The one write inside a turn is the crash
journal: before a turn runs a tool that is not ``idempotent``, the turn is
saved with its calls in flight, and each finished call's result follows.
A run resumed after a crash re-runs an idempotent call still in flight and
answers any other one "outcome unknown"; a cancel while such a tool runs
leaves that journal in the store, since the call may have taken effect.

``durability="exit"`` skips the per-turn writes: the session and the state
are written once, when the run ends. Cheaper, and nothing to resume after
a crash.

Called inside an op, every turn, model call and tool call is a child
execution of that op (``turn[n]`` → ``model``, ``<tool>``), with GenAI
attributes. ``Runner.run`` is ``Runner.stream`` drained: one loop, the
model streamed either way, no events built.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
import uuid
from contextlib import nullcontext
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple

import pydantic
from operonx import child
from operonx.core import LOGGER

from operonx_agents.agent import Agent
from operonx_agents.context import compaction
from operonx_agents.context.prompt import assemble
from operonx_agents.context.session import Session
from operonx_agents.errors import Interrupted, OutputInvalid, Tripwire
from operonx_agents.model._deadline import deadline as _deadline
from operonx_agents.model.model import ModelResponse, Reasoning
from operonx_agents.model.output import (
    FINAL_TOOL,
    Shape,
    final_tool,
    native_format,
    read_answer,
    shape_for,
    validation_errors,
)
from operonx_agents.model.usage import Usage
from operonx_agents.run.context import RunContext
from operonx_agents.run.events import (
    ApprovalRequired,
    Compacted,
    Event,
    ReasoningDelta,
    RunFinished,
    RunStarted,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
    TurnStarted,
)
from operonx_agents.run.interruption import (
    Approve,
    Decision,
    Deny,
    Interruption,
    interruption_id,
)
from operonx_agents.run.limits import Meter
from operonx_agents.run.result import RunResult
from operonx_agents.run.state import PendingTurn, RunState
from operonx_agents.run.store import StateStore
from operonx_agents.safety.hooks import ModelRequest, ToolCall
from operonx_agents.safety.redact import RunRedaction
from operonx_agents.tools.dispatch import DENIED, NO_APPROVER, Paused, run_calls, tool_message
from operonx_agents.tools.tool import ToolSpec

__all__ = ["Runner", "BUDGET_NOTICE", "EXPIRED", "NOT_RUN", "OUTCOME_UNKNOWN"]

DURABILITY = ("turn", "exit")

#: The user message the last budgeted turn carries. Kept from
#: operonx.agents' react loop, where it was tuned against a live model.
BUDGET_NOTICE = {
    "turns": (
        "You have used your entire turn budget ({cap} turns) and cannot call any more tools. "
        "Answer now with what you already have, and say plainly what you could not finish."
    ),
    "tool_calls": (
        "You have used your entire tool-call budget ({cap} calls) and cannot call any more "
        "tools. Answer now with what you already have, and say plainly what you could not "
        "finish."
    ),
}

#: The tool message of a call the loop answers without running it, so the
#: stored history never holds an unanswered call.
NOT_RUN = {
    "final": (
        "Not run: this was the last turn the budget allowed, and no more tools could be "
        "called. Do not assume it happened."
    ),
    "tool_calls": (
        "Not run: the tool-call budget ({cap} calls) ran out before this call could be "
        "dispatched. Do not assume it happened."
    ),
    "limit": (
        "Not run: the run's {cap} limit was reached before this call could be dispatched. "
        "Do not assume it happened."
    ),
    "answered": (
        "Not run: the final answer was given in the same turn. Do not assume it happened."
    ),
}

#: A call that was running when the process died, whose tool is not
#: idempotent: the run cannot know whether it took effect.
OUTCOME_UNKNOWN = (
    "Interrupted: {name!r} was running when the run stopped, and its outcome is unknown. "
    "Check whether it took effect before calling it again."
)

#: A call whose approval request was not answered in time: an answer that
#: arrives later must not run it (``Agent.approval_ttl``).
EXPIRED = (
    "Blocked: the approval for this {name!r} call expired before anyone answered. "
    "Do not retry it; ask how to proceed."
)

#: The statuses a run ends in for good: resuming one returns its result.
TERMINAL = ("completed", "limit", "blocked")

ACCEPTED = "Final answer recorded."
FINAL_DESCRIPTION = "Give the final answer. Call it once, when you are done."
REASK = "Your answer did not match the required shape: {error}. Answer again, corrected."
PROMPTED = (
    "\n\nWhen you give your final answer, answer with one JSON object that matches this JSON "
    "Schema, and nothing else:\n{schema}"
)

Emit = Callable[[Event], None]


class Runner:
    """Runs an :class:`~operonx_agents.Agent`. Stateless: everything a run
    holds is in its :class:`RunState`."""

    @staticmethod
    async def run(
        agent: Agent,
        input: Any,
        *,
        deps: Any = None,
        session: Optional[Session] = None,
        store: Optional[StateStore] = None,
        durability: str = "turn",
        run_id: Optional[str] = None,
        parent: Optional[RunContext] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RunResult:
        """Run ``agent`` on ``input`` to the end.

        Args:
            input: The user's message (a string), or a list of messages.
            deps: Handed to tools as ``ctx.deps``.
            session: The conversation to continue and to write.
            store: Where the :class:`RunState` is saved, for
                :meth:`resume`.
            durability: ``"turn"`` (default) writes at every turn
                boundary; ``"exit"`` once, at the end.
            run_id: The run's id (default: a new one).
            parent: A tool's ``ctx``, when a tool runs this agent: this
                run's spending counts toward the parent's limits.
            metadata: ``ctx.metadata`` for the tools.

        A cancel (``task.cancel()``, a timeout around this call) raises
        ``CancelledError`` here and writes nothing for the turn it cut.
        """
        run = await _Run.start(
            agent, input, deps, session, store, durability, run_id, parent, metadata
        )
        return await run.execute(None)

    @staticmethod
    def stream(
        agent: Agent,
        input: Any,
        *,
        deps: Any = None,
        session: Optional[Session] = None,
        store: Optional[StateStore] = None,
        durability: str = "turn",
        run_id: Optional[str] = None,
        parent: Optional[RunContext] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[Event]:
        """:meth:`run`, yielding its events (:mod:`operonx_agents.run.events`);
        the last is ``RunFinished`` with the result. Leaving the loop early
        (``break``, ``aclose()``) cancels the run."""
        return _stream(
            _Run.start(agent, input, deps, session, store, durability, run_id, parent, metadata)
        )

    @staticmethod
    async def resume(
        agent: Agent,
        run_id: str,
        *,
        store: StateStore,
        approvals: Optional[Dict[str, Decision]] = None,
        deps: Any = None,
        session: Optional[Session] = None,
        durability: str = "turn",
        parent: Optional[RunContext] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RunResult:
        """Continue the run saved under ``run_id``: an interrupted run, with
        the humans' ``approvals``; after a crash, from its last turn
        (finishing the turn whose tools were running); or a failed run, by
        retrying the turn that failed. A run that ended returns its result
        again. Pass the same agent and session.

        Args:
            approvals: ``{interruption id: Approve() | Deny(reason)}``. An
                interruption left unanswered keeps waiting: the run ends
                ``interrupted`` again, with the same id.

        Raises:
            KeyError: ``store`` has no run ``run_id``.
            ValueError: the run belongs to another agent, or ``approvals``
                answers an interruption the run is not waiting on.
        """
        run = await _Run.load(
            agent, run_id, store, approvals, deps, session, durability, parent, metadata
        )
        return await run.execute(None)

    @staticmethod
    def resume_stream(
        agent: Agent,
        run_id: str,
        *,
        store: StateStore,
        approvals: Optional[Dict[str, Decision]] = None,
        deps: Any = None,
        session: Optional[Session] = None,
        durability: str = "turn",
        parent: Optional[RunContext] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[Event]:
        """:meth:`resume`, yielding its events."""
        return _stream(
            _Run.load(agent, run_id, store, approvals, deps, session, durability, parent, metadata)
        )


class _End:
    pass


class _Raised:
    __slots__ = ("exc",)

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc


async def _stream(prepare: Awaitable["_Run"]) -> AsyncIterator[Event]:
    """Run the loop in its own task and hand its events over a queue.

    Its own task, because the loop holds child scopes (``turn[n]``) open
    while events go out: run in this generator, they would be open in the
    consumer's code too. The task starts from the caller's context, so its
    steps are still children of the op that called ``stream``.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def produce() -> None:
        try:
            run = await prepare
            await run.execute(queue.put_nowait)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - re-raised in the consumer
            queue.put_nowait(_Raised(exc))
        queue.put_nowait(_End)

    task = asyncio.ensure_future(produce())
    try:
        while True:
            item = await queue.get()
            if item is _End:
                break
            if isinstance(item, _Raised):
                raise item.exc
            yield item
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class _Stop(Exception):
    """Ends the loop: ``status`` (``completed``/``limit``/``interrupted``)."""

    def __init__(self, status: str, limit: Optional[str] = None, error: Optional[str] = None):
        super().__init__(f"{status}: {limit}" if limit else status)
        self.status = status
        self.limit = limit
        self.error = error


class _Run:
    """One run: the agent, its state, and where it writes."""

    def __init__(
        self,
        agent: Agent,
        state: RunState,
        deps: Any,
        session: Optional[Session],
        store: Optional[StateStore],
        durability: str,
        parent: Optional[RunContext],
        metadata: Optional[Dict[str, Any]],
        resumed: bool,
        approvals: Optional[Dict[str, Decision]] = None,
    ) -> None:
        if durability not in DURABILITY:
            raise ValueError(
                f"durability={durability!r} is not one of {list(DURABILITY)}: 'turn' writes at "
                "every turn boundary, 'exit' once at the end."
            )
        self.agent = agent
        self.state = state
        self.session = session
        self.store = store
        self.durability = durability
        self.resumed = resumed
        self.meter = Meter(parent.meter if parent is not None else None, Usage(**state.usage))
        self.decisions: Dict[str, Decision] = dict(approvals or {})
        self.ctx = RunContext(
            deps=deps,
            metadata=dict(metadata or {}),
            run_id=state.run_id,
            session_id=state.session_id,
            agent=agent.name,
            turn=state.turn,
            meter=self.meter,
            store=store,
            approvals=self.decisions,
        )
        self.hooks = agent.hooks if agent.hooks else None
        self.redact = RunRedaction(agent.redact) if agent.redact is not None else None
        # The interruptions the parked turn waits on, by id.
        self.waiting: Dict[str, Interruption] = {}
        if state.pending is not None:
            for data in state.pending.interruptions:
                waiting = Interruption.from_json(data)
                self.waiting[waiting.id] = waiting
        self._check_decisions()
        self.emit: Optional[Emit] = None
        self.shape: Optional[Shape] = None
        self.strategy: Optional[str] = None
        self.system = ""
        self.skip = 0  # session items a crashed commit already wrote
        self.lock = asyncio.Lock()
        self.journal = False
        self.started: Dict[str, float] = {}
        self.wall: Optional[float] = None

    # ── construction ───────────────────────────────────────────────────

    @classmethod
    async def start(
        cls, agent, input, deps, session, store, durability, run_id, parent, metadata
    ) -> "_Run":
        items = await session.get_items() if session is not None else []
        state = RunState(
            run_id=run_id or uuid.uuid4().hex,
            agent=agent.name,
            input=_input_messages(input),
            messages=compaction.view(items),
            session_id=getattr(session, "session_id", None),
            session_base=len(items),
        )
        return cls(agent, state, deps, session, store, durability, parent, metadata, False)

    @classmethod
    async def load(
        cls, agent, run_id, store, approvals, deps, session, durability, parent, metadata
    ) -> "_Run":
        state = await store.load(run_id)
        if state is None:
            raise KeyError(
                f"no run {run_id!r} in {store!r}: it never committed a turn, or it expired."
            )
        if state.agent != agent.name:
            raise ValueError(
                f"run {run_id!r} belongs to agent {state.agent!r}, not {agent.name!r}. Resume "
                "it with the agent that started it."
            )
        run = cls(agent, state, deps, session, store, durability, parent, metadata, True, approvals)
        if session is not None and state.status in ("running", "interrupted"):
            written = len(await session.get_items()) - (state.session_base + state.saved)
            run.skip = max(0, written)
        return run

    def _check_decisions(self) -> None:
        for key, decision in self.decisions.items():
            if not isinstance(decision, (Approve, Deny)):
                raise TypeError(
                    f"approvals[{key!r}] is {type(decision).__name__}; answer each "
                    "interruption with Approve() or Deny(reason)."
                )
        unknown = sorted(set(self.decisions) - set(self.waiting))
        if unknown:
            raise ValueError(
                f"run {self.state.run_id!r} is not waiting on {unknown}; it waits on "
                f"{sorted(self.waiting) or 'nothing'}. Answer the ids in "
                "RunResult.interruptions."
            )

    # ── the loop ───────────────────────────────────────────────────────

    async def execute(self, emit: Optional[Emit]) -> RunResult:
        s, agent = self.state, self.agent
        self.emit = emit
        self._emit(RunStarted(s.run_id, agent.name, self.resumed))
        if s.status in TERMINAL:
            output_type = agent.output_type
            if isinstance(s.output, dict) and output_type not in (str, None):
                # Read back from JSON: the answer is the model again.
                shape = shape_for(output_type)
                if shape.kind == "model":
                    s.output = shape.model.model_validate(s.output)
            result = self._result()
            self._emit(RunFinished(result))
            return result
        s.status, s.error = "running", None
        if agent.limits.wall_s is not None:
            self.wall = time.monotonic() + agent.limits.wall_s
        try:
            self.system, self.shape, self.strategy = self._prompt()
            if s.pending is not None:
                await self._resume_pending()
            while True:
                await self._turn()
        except _Stop as stop:
            s.status, s.limit_hit, s.error = stop.status, stop.limit, stop.error
            if stop.status != "completed":
                s.output = self._best() if stop.status == "limit" else None
        except Tripwire as trip:
            LOGGER.warning("agent %s run %s blocked: %s", agent.name, s.run_id, trip.reason)
            s.status, s.output = "blocked", None
            s.error = f"Tripwire: {trip.reason}"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a run ends in a result, not a raise
            LOGGER.warning("agent %s run %s failed: %s", agent.name, s.run_id, exc)
            s.status, s.output = "failed", None
            s.error = f"{type(exc).__name__}: {exc}"
        if self.session is not None or self.store is not None:
            await _shielded(self._close())
        result = self._result()
        self._emit(RunFinished(result))
        return result

    def _prompt(self) -> Tuple[str, Optional[Shape], Optional[str]]:
        agent = self.agent
        system = agent.system_prompt(self.ctx)
        if agent.output_type is str or agent.output_type is None:
            return system, None, None
        shape = shape_for(agent.output_type)
        strategy = agent.model.structured_output()
        if strategy == "prompted":
            schema = json.dumps(shape.schema(), ensure_ascii=False)
            system = system + PROMPTED.format(schema=schema)
        return system, shape, strategy

    async def _turn(self) -> None:
        s, agent, limits = self.state, self.agent, self.agent.limits
        n = s.turn + 1
        if limits.turns is not None and n > limits.turns:
            raise _Stop("limit", "turns")
        self._check_wall()
        hit = limits.before_call(self.meter.total, s.last_input)
        if hit:
            raise _Stop("limit", hit)
        offers_tools = len(agent.tools) > 0 or self.strategy == "tool"
        final = s.final_turn
        if final is None and offers_tools and limits.turns is not None and n == limits.turns:
            final = "turns"
        self.ctx.turn = n
        # A turn boundary yields to the event loop once. A model that
        # answers without I/O (a cache, a local model) and tools that never
        # wait would otherwise hold the loop for the whole run, and every
        # other coroutine in the process (a voice call's audio) with it.
        await asyncio.sleep(0)
        async with child("turn", inputs={"turn": n}, op_type="turn") as rec:
            rec.attrs.update({"gen_ai.agent.name": agent.name, "operonx.agent.run_id": s.run_id})
            if self.emit is not None:
                self.emit(TurnStarted(n))
            items: List[Dict[str, Any]] = list(s.input)
            convo = s.messages + items
            async with self._wall_bound():
                if s.compact_next and agent.context is not None:
                    compacted = await self._compact(convo)
                    if compacted is not None:
                        summary, convo = compacted
                        items.append(summary)
                if final is not None:
                    notice = {
                        "role": "user",
                        "content": BUDGET_NOTICE[final].format(cap=getattr(limits, final)),
                    }
                    items.append(notice)
                    convo = convo + [notice]
                reply = await self._call_model(convo, offers_tools, final is not None)
                assistant: Dict[str, Any] = {"role": "assistant", "content": reply.content}
                if reply.tool_calls:
                    assistant["tool_calls"] = reply.tool_calls
                items.append(assistant)
                convo = convo + [assistant]
                end = await self._after_reply(reply, items, convo, final)
            rec.outputs = {
                "tool_calls": len(reply.tool_calls),
                "finish_reason": reply.finish_reason,
            }
        if end is not None:
            raise end

    async def _after_reply(
        self, reply: ModelResponse, items: List[dict], convo: List[dict], final: Optional[str]
    ) -> Optional[_Stop]:
        """Everything after the model answered: caps, the answer, tools,
        the commit. Returns how the run ends, if it does."""
        s, agent, limits = self.state, self.agent, self.agent.limits
        s.finish_reason = reply.finish_reason
        if agent.context is not None:
            s.compact_next = agent.context.due(reply.usage.input_tokens)
        calls = list(reply.tool_calls)
        hit = limits.after_call(self.meter.total)
        if hit:
            answers = self._unrun(calls, NOT_RUN["limit"].format(cap=hit))
            await self._commit(items + answers, convo + answers, reply)
            return _Stop("limit", hit)

        if self.strategy == "tool":
            answer = next((c for c in calls if c["name"] == FINAL_TOOL), None)
            if answer is not None:
                return await self._final_result(answer, calls, items, convo, reply)
        if not calls:
            return await self._answer(reply, items, convo, final)
        if final is not None:  # told it could not, and asked for tools anyway
            answers = self._unrun(calls, NOT_RUN["final"])
            await self._commit(items + answers, convo + answers, reply)
            return _Stop("limit", final)

        run, cut = calls, []
        if limits.tool_calls is not None:
            room = limits.tool_calls - s.tool_calls
            run, cut = calls[:room], calls[room:]
            if len(run) == room:
                s.final_turn = "tool_calls"
        s.tool_calls += len(run)
        outcomes = await self._dispatch(run, items, convo)
        answers = self._unrun(cut, NOT_RUN["tool_calls"].format(cap=limits.tool_calls))
        by_id = {m["tool_call_id"]: m for m in answers}
        by_id.update((m["tool_call_id"], m) for m in outcomes if isinstance(m, dict))
        paused = [o for o in outcomes if isinstance(o, Paused)]
        if paused:
            return self._park(items, convo, calls, by_id, paused)
        ordered = [by_id[c["id"]] for c in calls]
        await self._commit(items + ordered, convo + ordered, reply)
        return None

    async def _answer(
        self, reply: ModelResponse, items: List[dict], convo: List[dict], final: Optional[str]
    ) -> Optional[_Stop]:
        """A reply with no tool calls: the answer, or a re-ask."""
        s = self.state
        if self.shape is None:
            s.output = await self._output(reply.content)
            await self._commit(items, convo, reply)
            return _Stop("limit", final) if final is not None else _Stop("completed")
        raw, error = read_answer(reply, "tool" if self.strategy == "tool" else "native")
        if error is None:
            try:
                s.output = self.shape.value(self.shape.model.model_validate(raw))
            except pydantic.ValidationError as exc:
                error = validation_errors(exc)
        if error is None:
            s.output = await self._output(s.output)
            await self._commit(items, convo, reply)
            return _Stop("limit", final) if final is not None else _Stop("completed")
        fix = {"role": "user", "content": REASK.format(error=error)}
        return await self._reask(error, items + [fix], convo + [fix], reply)

    async def _final_result(self, answer, calls, items, convo, reply) -> Optional[_Stop]:
        """The model called ``final_result`` (the ``tool`` strategy)."""
        s = self.state
        error: Optional[str] = None
        if not isinstance(answer["args"], dict):
            error = f"the {FINAL_TOOL} arguments are not a JSON object"
        else:
            try:
                s.output = self.shape.value(self.shape.model.model_validate(answer["args"]))
            except pydantic.ValidationError as exc:
                error = validation_errors(exc)
        others = [c for c in calls if c is not answer]
        if error is None:
            s.output = await self._output(s.output)
            done = tool_message(answer["id"], FINAL_TOOL, ACCEPTED)
            answers = [done, *self._unrun(others, NOT_RUN["answered"])]
            self._finished(done)
            await self._commit(items + answers, convo + answers, reply)
            return _Stop("completed")
        bad = tool_message(answer["id"], FINAL_TOOL, REASK.format(error=error), is_error=True)
        self._finished(bad)
        answers = [bad, *self._unrun(others, NOT_RUN["answered"])]
        return await self._reask(error, items + answers, convo + answers, reply)

    async def _reask(self, error: str, items, convo, reply) -> Optional[_Stop]:
        s = self.state
        s.output = None
        if s.reasks >= self.agent.output_retries:
            await self._commit(items, convo, reply)
            raise OutputInvalid(
                f"agent {self.agent.name!r}: the answer did not validate after "
                f"{self.agent.output_retries} re-ask(s): {error}. Raise output_retries or "
                "tighten the instructions.",
                error=error,
            )
        s.reasks += 1
        await self._commit(items, convo, reply)
        return None

    # ── the model ──────────────────────────────────────────────────────

    async def _call_model(self, convo: List[dict], offers_tools: bool, last: bool) -> ModelResponse:
        agent = self.agent
        tools = agent.tools.definitions() if offers_tools else None
        tool_choice: Any = None
        response_format = None
        if self.strategy == "tool":
            tools = (tools or []) + [final_tool(self.shape, FINAL_DESCRIPTION)]
            if last:
                tool_choice = {"type": "function", "function": {"name": FINAL_TOOL}}
        elif last and tools:
            tool_choice = "none"
        if self.strategy == "native":
            response_format = native_format(self.shape)
        hooks = self.hooks
        if hooks is not None and hooks.before_model:
            request = await hooks.model_request(self.ctx, ModelRequest(convo, tools or None))
            convo, tools = request.messages, request.tools
        messages = assemble(self.system, convo)
        reply: Optional[ModelResponse] = None
        async for piece in agent.model.stream(
            messages,
            tools=tools or None,
            tool_choice=tool_choice,
            response_format=response_format,
            settings=agent.settings,
            reasoning=self.emit is not None,
            redact=self.redact,
        ):
            if isinstance(piece, ModelResponse):
                reply = piece
            elif self.emit is None:
                continue  # run(): nobody reads the deltas
            elif isinstance(piece, Reasoning):
                self.emit(ReasoningDelta(piece.text))
            else:
                self.emit(TextDelta(piece))
        assert reply is not None  # a stream always ends with one
        self.meter.add(reply.usage)
        self.state.last_input = reply.usage.input_tokens
        if hooks is not None and hooks.after_model:
            reply = await hooks.model_response(self.ctx, reply)
        return reply

    async def _compact(self, convo: List[dict]) -> Optional[Tuple[dict, List[dict]]]:
        policy = self.agent.context
        older, kept = compaction.plan(convo, policy.keep_recent)
        if not older:
            return None
        kept = compaction.clear_tool_results(kept, policy.clear_tool_results_after)
        tokens = 0
        async with child("compact", inputs={"messages": len(older)}, op_type="compaction") as rec:
            rec.redact = self.redact
            if policy.summarizer is None:
                summary = compaction.dropped_note(len(older))
            else:
                prompt = [{"role": "user", "content": compaction.summary_prompt(older)}]
                try:
                    reply = await policy.summarizer.request(prompt, redact=self.redact)
                except Exception as exc:  # noqa: BLE001 - enrichment fails open
                    LOGGER.warning("compaction skipped: the summarizer failed (%s)", exc)
                    return None
                self.meter.add(reply.usage)
                summary, tokens = reply.content, reply.usage.output_tokens
            rec.outputs = {"summary": summary, "kept": len(kept)}
        self._emit(Compacted(dropped=len(older), summary_tokens=tokens))
        return compaction.summary_item(summary, kept), [compaction.summary_message(summary), *kept]

    # ── tools ──────────────────────────────────────────────────────────

    async def _dispatch(self, calls: List[dict], items: List[dict], convo: List[dict]) -> list:
        """Run ``calls``: per call, its message or :class:`Paused`."""
        if not calls:
            return []
        risky = any(
            (t := self.agent.tools.get(c["name"])) is not None and not t.spec.idempotent
            for c in calls
        )
        self.journal = bool(risky and self.store is not None and self.durability == "turn")
        if self.journal:
            self.state.pending = PendingTurn(
                items=items, messages=convo, calls=calls, inflight=[c["id"] for c in calls]
            )
            await self._save()
        try:
            return await self._run_calls(calls, self.emit is not None)
        finally:
            self.journal = False

    def _run_calls(self, calls: List[dict], listening: bool) -> Awaitable[list]:
        return run_calls(
            calls,
            self.agent.tools,
            ctx=self.ctx,
            policy=self.agent.policy,
            gate=self._gate,
            hooks=self.hooks,
            redact=self.redact,
            on_start=self._started if listening else None,
            on_message=self._result_ready if listening or self.journal else None,
        )

    async def _gate(self, call: ToolCall, spec: ToolSpec, reason: str) -> Optional[str]:
        """A call that needs a human: its answer, or park it."""
        s, agent = self.state, self.agent
        key = interruption_id(s.run_id, agent.name, call.name, s.turn + 1, call.id)
        asked = self.waiting.get(key)
        if asked is not None and asked.expired:
            return EXPIRED.format(name=call.name)  # a late answer does not count
        decision = self.decisions.get(key)
        if isinstance(decision, Approve):
            return None
        if isinstance(decision, Deny):
            text = DENIED.format(name=call.name)
            return f"{text} Their reason: {decision.reason}" if decision.reason else text
        if self.store is None:
            return NO_APPROVER.format(name=call.name)
        if asked is not None:
            asked = dataclasses.replace(asked, path=())  # _park adds this agent again
        else:
            ttl = agent.approval_ttl
            shown = (
                self.redact.redactor.scrub_data(call.args) if self.redact is not None else call.args
            )
            asked = Interruption(
                id=key,
                tool=call.name,
                args=_jsonable(shown),
                reason=reason,
                call_id=call.id,
                path=(),
                expires_at=time.time() + ttl if ttl is not None else None,
            )
        raise Interrupted([asked])

    def _park(
        self, items: List[dict], convo: List[dict], calls: List[dict], results: dict, paused: list
    ) -> _Stop:
        """Save the turn as waiting for a human; the run ends ``interrupted``."""
        s = self.state
        waiting = [i.under(self.agent.name) for p in paused for i in p.interruptions]
        s.pending = PendingTurn(
            items=items,
            messages=convo,
            calls=calls,
            inflight=[],
            results=results,
            interruptions=[i.to_json() for i in waiting],
        )
        self.waiting = {i.id: i for i in waiting}
        if self.emit is not None:
            for i in waiting:
                self.emit(ApprovalRequired(i.id, i.call_id, i.tool, i.args, i.reason, i.path))
        return _Stop("interrupted")

    def _started(self, call: Dict[str, Any]) -> None:
        self.started[call["id"]] = time.perf_counter()
        self._emit(ToolCallStarted(call["id"], call["name"], call["args"]))

    async def _result_ready(self, message: dict) -> None:
        self._finished(message)
        if self.journal:
            async with self.lock:
                pending = self.state.pending
                pending.results[message["tool_call_id"]] = message
                pending.inflight.remove(message["tool_call_id"])
                await self._save()

    def _finished(self, message: dict) -> None:
        if self.emit is None:
            return
        started = self.started.pop(message["tool_call_id"], None)
        ms = (time.perf_counter() - started) * 1000.0 if started is not None else 0.0
        self._emit(
            ToolCallFinished(
                message["tool_call_id"],
                message["name"],
                message["status"] == "success",
                str(message["content"])[:200],
                ms,
            )
        )

    def _unrun(self, calls: List[dict], text: str) -> List[dict]:
        out = []
        for call in calls:
            message = tool_message(call["id"], call["name"], text, is_error=True)
            self._finished(message)
            out.append(message)
        return out

    async def _resume_pending(self) -> None:
        """Finish the parked turn: the calls a crash cut (an idempotent one
        re-runs, any other is "outcome unknown") and the calls that waited
        for a human (run with their answers, or parked again)."""
        s = self.state
        pending = s.pending
        n = s.turn + 1
        self.ctx.turn = n
        async with child("turn", inputs={"turn": n, "resumed": True}, op_type="turn") as rec:
            rec.attrs.update(
                {"gen_ai.agent.name": self.agent.name, "operonx.agent.run_id": s.run_id}
            )
            if self.emit is not None:
                self.emit(TurnStarted(n))
            results = dict(pending.results)
            unknown = [
                c
                for c in pending.calls
                if c["id"] in pending.inflight
                and ((t := self.agent.tools.get(c["name"])) is None or not t.spec.idempotent)
            ]
            run = [c for c in pending.calls if c["id"] not in results and c not in unknown]
            self.journal = self.store is not None and self.durability == "turn"
            try:
                if self.journal and run:
                    # The calls about to run join the cut ones still unknown:
                    # a crash during this resume treats both alike.
                    pending.inflight = [c["id"] for c in run + unknown]
                    await self._save()
                async with self._wall_bound():
                    outcomes = await self._run_calls(run, True)
            finally:
                self.journal = False
            for call in unknown:
                text = OUTCOME_UNKNOWN.format(name=call["name"])
                message = tool_message(call["id"], call["name"], text, is_error=True)
                self._finished(message)
                results[call["id"]] = message
            results.update((m["tool_call_id"], m) for m in outcomes if isinstance(m, dict))
            paused = [o for o in outcomes if isinstance(o, Paused)]
            rec.outputs = {"run": len(run), "unknown": len(unknown), "waiting": len(paused)}
            if paused:
                end = self._park(pending.items, pending.messages, pending.calls, results, paused)
        if paused:
            raise end
        ordered = [results[c["id"]] for c in pending.calls]
        self.waiting = {}
        await self._commit(pending.items + ordered, pending.messages + ordered, None)

    # ── writes ─────────────────────────────────────────────────────────

    async def _commit(
        self, items: List[dict], convo: List[dict], reply: Optional[ModelResponse]
    ) -> None:
        """The turn, whole: into the state, then the session and the store."""
        s = self.state
        s.messages = convo
        s.new_items = s.new_items + items
        s.input = []
        s.turn += 1
        s.pending = None
        if self.durability == "turn" and (self.session is not None or self.store is not None):
            await _shielded(self._write())
        if self.emit is not None:
            self.emit(TurnFinished(s.turn, reply.usage if reply is not None else Usage()))

    async def _write(self) -> None:
        s = self.state
        if self.session is not None and s.saved < len(s.new_items):
            tail = s.new_items[s.saved + self.skip :]
            self.skip = 0
            if tail:
                await self.session.add_items(tail)
            s.saved = len(s.new_items)
        if self.store is not None:
            await self._save()

    async def _save(self) -> None:
        self.state.usage = self.meter.total.to_dict()
        await self.store.save(self.state)

    async def _close(self) -> None:
        if self.durability == "exit" or self.store is not None:
            await self._write()

    # ── helpers ────────────────────────────────────────────────────────

    async def _output(self, output: Any) -> Any:
        hooks = self.hooks
        if hooks is None or not hooks.on_output:
            return output
        return await hooks.output(self.ctx, output)

    def _emit(self, event: Event) -> None:
        if self.emit is not None:
            self.emit(event)

    def _check_wall(self) -> None:
        if self.wall is not None and time.monotonic() >= self.wall:
            raise _Stop("limit", "wall_s")

    def _wall_bound(self):
        """The rest of the wall budget over the uncommitted part of a turn;
        when it passes, the turn is cancelled and the run ends ``limit``."""
        if self.wall is None:
            return nullcontext()
        return _WallBound(self.wall - time.monotonic())

    def _best(self) -> Any:
        """The last answer the model gave, for a run cut short."""
        if self.state.output is not None:
            return self.state.output
        if self.shape is not None:
            return None
        for message in reversed(self.state.messages):
            if message.get("role") == "assistant" and message.get("content"):
                if not message.get("tool_calls"):
                    return message["content"]
        return None

    def _result(self) -> RunResult:
        s = self.state
        return RunResult(
            status=s.status,
            output=s.output,
            usage=self.meter.total,
            run_id=s.run_id,
            turns=s.turn,
            messages=list(s.messages),
            new_items=list(s.new_items),
            limit_hit=s.limit_hit,
            error=s.error,
            finish_reason=s.finish_reason,
            interruptions=list(self.waiting.values()) if s.status == "interrupted" else [],
        )


class _WallBound:
    """``deadline(seconds)`` whose expiry ends the run as a ``wall_s`` limit."""

    __slots__ = ("_seconds", "_cm")

    def __init__(self, seconds: float) -> None:
        self._seconds = max(0.0, seconds)
        self._cm = None

    async def __aenter__(self) -> None:
        self._cm = _deadline(self._seconds)
        await self._cm.__aenter__()

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        try:
            return bool(await self._cm.__aexit__(exc_type, exc, tb))
        except TimeoutError:
            raise _Stop("limit", "wall_s") from None


async def _shielded(write: Awaitable[None]) -> None:
    """Let a commit land even when the run is cancelled during it, then
    re-raise the cancel: a turn is written whole or not at all."""
    task = asyncio.ensure_future(write)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def _jsonable(value: Any) -> Any:
    """Validated arguments as JSON data (an interruption is saved)."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=_plain))


def _plain(value: Any) -> Any:
    if isinstance(value, pydantic.BaseModel):
        return value.model_dump(mode="json")
    return str(value)


def _input_messages(input: Any) -> List[Dict[str, Any]]:
    if isinstance(input, str):
        return [{"role": "user", "content": input}]
    if isinstance(input, dict):
        return [dict(input)]
    if isinstance(input, (list, tuple)) and all(isinstance(m, dict) for m in input):
        return [dict(m) for m in input]
    raise TypeError(
        f"Runner input must be the user's message (a str) or a list of message dicts, got "
        f"{type(input).__name__}."
    )
