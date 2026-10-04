"""``agent_service``: an agent behind an operonx door — HTTP (JSON or
server-sent events) or a websocket — with approvals answered over the wire.

::

    from operonx.app import Application, http, websocket

    store = SQLiteStateStore("runs.db")
    APP = Application("support", services=[
        agent_service(support, http("POST", "/support"), store=store),
        agent_service(support, websocket("/support/ws"), store=store, max_inflight=64,
                      name="support_ws"),
    ])

The service is an ordinary operonx ``Service``: its graph is ``ingress →
<agent> → egress``, so its runs are traced like any other (the agent op,
then each turn, model call and tool call as child executions), filed under
the service, and served by ``operonx serve``.

**What a client sends** (an HTTP body, or one websocket text frame):

- a run: ``{"input": "refund 900 on A1B2C3D4", "session_id": "chat:42"}``
  (``session_id`` needs ``sessions=``; a bare string is the input);
- a resume: ``{"run_id": "...", "approvals": {"<interruption id>":
  "approve" | "deny" | {"deny": "<reason>"}}}``. An http door also answers
  it on ``POST <path>/resume`` (operonx's ``Service(resume=)``); either
  route reads either body.

**What it gets back.** A JSON HTTP caller gets one reply, the run's
result: ``status`` (``completed``, ``interrupted``, ``limit``, ``blocked``,
``failed``), ``output``, ``run_id``, ``interruptions`` (each with the
``id`` a resume answers), ``usage``, ``turns``, ``limit_hit``, ``error``.
A caller that accepts ``text/event-stream``, and every websocket, gets
each event as it happens (:mod:`operonx_agents.run.events`, ``to_json()``),
the last a ``RunFinished`` whose ``result`` is that reply. A body the
service cannot read is answered ``{"status": "invalid", "error": ...}``
and starts no agent run.

A websocket connection is one conversation: its requests run one after
another, in the order they arrive.

An interrupted run waits in ``store`` — any process serving the same
store answers its resume, a day later or after a restart.
"""

from __future__ import annotations

import asyncio
import weakref
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Dict, Mapping, Optional

from operonx.app import Service
from operonx.app.serve import current_session, egress, ingress
from operonx.core import END, START, graph
from operonx.core.ops import BaseOp
from operonx.core.utils.common import Param

from operonx_agents.run.events import RunFinished
from operonx_agents.run.interruption import Approve, Decision, Deny

if TYPE_CHECKING:
    from operonx.app.declare import Listener
    from operonx.app.manifest import ServeSpec

    from operonx_agents.agent import Agent
    from operonx_agents.context.session import Session
    from operonx_agents.run.result import RunResult
    from operonx_agents.run.store import StateStore

__all__ = ["AgentDoorOp", "agent_service", "decisions_of", "reply_of"]

#: What a reply leaves out of ``RunResult.to_dict()``: the conversation is
#: the session's, and a client that wants it keeps its own.
_LEFT_OUT = ("messages", "new_items")


class BadRequest(ValueError):
    """A body the service cannot read; answered ``status: "invalid"``."""


def reply_of(result: "RunResult") -> Dict[str, Any]:
    """A run's result as a client reads it: ``RunResult.to_dict()``
    without the conversation."""
    data = result.to_dict()
    for key in _LEFT_OUT:
        data.pop(key, None)
    return data


def decisions_of(raw: Any) -> Dict[str, Decision]:
    """``{"<id>": "approve" | "deny" | {"deny": "<reason>"}}`` as the
    ``approvals`` ``Runner.resume`` takes.

    Raises:
        BadRequest: anything else — a typo must not read as a decision.
    """
    if not isinstance(raw, Mapping) or not raw:
        raise BadRequest(
            'a resume needs "approvals": {"<interruption id>": "approve" | "deny" | '
            '{"deny": "<reason>"}}'
        )
    out: Dict[str, Decision] = {}
    for key, value in raw.items():
        if value == "approve":
            out[str(key)] = Approve()
        elif value == "deny":
            out[str(key)] = Deny()
        elif isinstance(value, Mapping) and set(value) == {"deny"}:
            out[str(key)] = Deny(str(value["deny"] or ""))
        else:
            raise BadRequest(
                f'approval {key!r} is {value!r}; expected "approve", "deny" or '
                '{"deny": "<reason>"}'
            )
    return out


def _body(item: Any) -> Dict[str, Any]:
    if isinstance(item, str):
        return {"input": item}
    if not isinstance(item, Mapping):
        raise BadRequest(
            'the body is a run, {"input": ..., "session_id"?: ...}, or a resume, '
            f'{{"run_id": ..., "approvals": {{...}}}}; got {type(item).__name__}'
        )
    if "run_id" in item:
        return dict(item)
    if "input" not in item:
        raise BadRequest(
            f'the body has no "input" (a run) or "run_id" (a resume); it has {sorted(item)}'
        )
    return dict(item)


class AgentDoorOp(BaseOp):
    """The op an :func:`agent_service` graph runs: one request in, the
    run's events (or its result) out, one ``frame`` per yield. Named after
    the agent, of type ``agent``."""

    show_keys_default = ("frame",)

    __slots__ = ["agent", "store", "sessions", "deps", "durability", "_lines"]

    type = "agent"

    def __init__(
        self,
        *,
        agent: "Agent",
        store: "StateStore",
        sessions: Optional[Callable[[str], "Session"]],
        deps: Any,
        durability: str,
        inputs: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("bound", "io")
        kwargs.setdefault("transient", True)
        super().__init__(**kwargs)
        self.agent = agent
        self.store = store
        self.sessions = sessions
        self.deps = deps
        self.durability = durability
        # one lock per connection: a websocket's requests run in turn
        self._lines: "weakref.WeakKeyDictionary[Any, asyncio.Lock]" = weakref.WeakKeyDictionary()
        self.inputs = self._merge_params(
            {"item": Param(required=False, default=None)}, self._normalize_params(inputs)
        )
        self.outputs = {"frame": Param(default=None)}
        self._set_core(self._serve)

    def warmup(self) -> None:
        """Resolve the model's resources when the service starts."""
        for resource in self.agent.model.resources:
            self.agent.model.llm(resource)

    def _line(self, session: Any) -> Optional[asyncio.Lock]:
        if session is None:
            return None
        lock = self._lines.get(session)
        if lock is None:
            lock = self._lines[session] = asyncio.Lock()
        return lock

    async def _serve(self, item: Any) -> AsyncIterator[dict]:
        session = current_session()
        # A JSON http caller reads one reply; a caller reading a stream
        # (`text/event-stream`, a websocket) reads every event.
        streaming = getattr(session, "stream", True)
        line = self._line(session)
        if line is None:
            async for frame in self._frames(item, streaming):
                yield {"frame": frame}
            return
        async with line:
            async for frame in self._frames(item, streaming):
                yield {"frame": frame}

    async def _frames(self, item: Any, streaming: bool) -> AsyncIterator[Dict[str, Any]]:
        from operonx_agents.run.runner import Runner

        try:
            body = _body(item)
            session = self._session(body.get("session_id"))
            if "run_id" in body:
                approvals = decisions_of(body.get("approvals"))
                events = Runner.resume_stream(
                    self.agent,
                    str(body["run_id"]),
                    store=self.store,
                    approvals=approvals,
                    deps=self.deps,
                    session=session,
                    durability=self.durability,
                )
            else:
                events = Runner.stream(
                    self.agent,
                    body["input"],
                    deps=self.deps,
                    session=session,
                    store=self.store,
                    durability=self.durability,
                )
            async for event in events:
                if isinstance(event, RunFinished):
                    reply = reply_of(event.result)
                    yield {"type": "RunFinished", "result": reply} if streaming else reply
                elif streaming:
                    yield event.to_json()
        except (BadRequest, KeyError, ValueError) as exc:
            # KeyError: no such run in the store; ValueError: the run is
            # another agent's, or the approvals answer what it does not
            # wait on. The caller's mistake, said back to it.
            message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
            yield {"status": "invalid", "error": str(message)}

    def _session(self, session_id: Any) -> Optional["Session"]:
        if session_id is None:
            return None
        if self.sessions is None:
            raise BadRequest(
                f"session_id {session_id!r} was sent to a service with no sessions: "
                "agent_service(..., sessions=lambda sid: RedisSession(sid, url=...))"
            )
        return self.sessions(str(session_id))

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        return {"agent": self.agent.name, "model": self.agent.model.resource}


def agent_service(
    agent: "Agent",
    listener: "Listener",
    *,
    store: "StateStore",
    sessions: Optional[Callable[[str], "Session"]] = None,
    deps: Any = None,
    name: Optional[str] = None,
    durability: str = "turn",
    **service: Any,
) -> "ServeSpec":
    """``agent`` as an operonx ``Service`` on ``listener`` (see the module
    docs for what clients send and read).

    Args:
        listener: ``operonx.app.http("POST", path)`` or
            ``websocket(path)`` (which needs ``max_inflight=``).
        store: Where runs are saved: an interrupted run waits here for its
            resume, which any process serving this store can answer.
        sessions: ``session_id -> Session``, for requests that carry a
            ``session_id`` (a conversation kept across requests).
        deps: Handed to every run's tools as ``ctx.deps``.
        name: The service's name (default: the agent's).
        durability: The runs' ``durability``.
        **service: Anything else ``operonx.app.Service`` takes —
            ``max_inflight``, ``trace``, ``on_session``, ``on_close``,
            ``description``, ``key_ops`` ...

    Returns:
        The ``ServeSpec`` to list in ``Application(services=[...])``. An
        http door also answers ``POST <path>/resume``.
    """
    if store is None:
        raise ValueError(
            f"agent_service({agent.name!r}) needs store=: an interrupted run waits there "
            "for its resume (SQLiteStateStore, RedisStateStore, InMemoryStateStore)"
        )
    config = dict(agent=agent, store=store, sessions=sessions, deps=deps, durability=durability)

    def flow():
        src = ingress()
        run = AgentDoorOp(**config, name=agent.name, inputs={"item": src["item"]})
        out = egress(item=run["frame"])
        START >> src >> run >> out >> END

    # The graph is named after the service: one project serving two agents
    # draws two graphs, not two called `flow`.
    flow.__name__ = flow.__qualname__ = f"{name or agent.name}_service"
    door = graph(flow)
    service.setdefault("description", f"The {agent.name} agent.")
    if listener.kind == "http":
        service.setdefault("resume", door)
    return Service(name or agent.name, listener, graph=door, **service)
