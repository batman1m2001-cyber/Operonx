"""``Agent`` — what an agent is, as data. ``Runner`` runs it.

::

    support = Agent(
        name="support",
        instructions="You resolve order problems for Edupia.",
        model=Model("qwen3.7-plus", deadline=30),
        tools=[lookup_order, refund],
        output_type=Resolution,
        limits=UsageLimits(turns=8, tool_calls=20, total_tokens=60_000, wall_s=90),
        context=ContextPolicy(window=128_000, summarizer=Model("inhouse")),
    )
    res = await Runner.run(support, "refund order A1B2C3D4", deps=deps)

There is no base class and no ``run()`` on the agent: a spec is a frozen
dataclass, so it can be shared between concurrent runs, and changed with
:meth:`Agent.clone`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Sequence, Union

from operonx_agents.context.compaction import ContextPolicy
from operonx_agents.model.model import Model, ModelSettings
from operonx_agents.run.context import RunContext
from operonx_agents.run.limits import UsageLimits
from operonx_agents.safety.hooks import Hooks, HookSet
from operonx_agents.safety.redact import Redactor
from operonx_agents.tools.policy import ToolPolicy
from operonx_agents.tools.toolset import Toolset

__all__ = ["Agent"]

#: The agent's standing instructions: text, or built from the run's context
#: once when the run starts (so the prompt stays byte-stable for the run).
Instructions = Union[str, Callable[[RunContext], str]]


@dataclass(frozen=True)
class Agent:
    """An agent spec.

    Attributes:
        name: Who it is, in traces (``gen_ai.agent.name``) and run states.
        model: The :class:`~operonx_agents.Model` it thinks with.
        instructions: The system prompt, or ``(ctx) -> str``.
        tools: The tools it owns (a :class:`Toolset`, or tools and plain
            functions to make one from). Only these can run.
        output_type: ``str`` (default), a pydantic model, or a type
            pydantic validates. How a typed answer is asked for is the
            model resource's ``structured_output``: ``tool`` offers a
            ``final_result`` tool beside the agent's own, ``native`` sends
            the schema as ``response_format``, ``prompted`` puts it in the
            system prompt. Every answer is validated.
        output_retries: Re-asks of an answer that does not validate.
        limits: :class:`UsageLimits` per run.
        policy: allow / ask / deny per tool (default: destructive tools
            ask, and with no one to ask they are refused).
        context: When to compact the conversation; ``None`` never does.
        settings: Request knobs over the model's.
        hooks: :class:`~operonx_agents.Hooks` around the model and tool
            calls (guardrails, rewrites, :class:`RedactToolOutput`).
        redact: Scrubs credentials from what leaves the run: trace
            records and the arguments an approval request shows. ``None``
            turns it off. What the model reads is untouched unless a
            ``RedactToolOutput`` hook is added.
        approval_ttl: Seconds an approval request stays answerable; past
            it the call is refused. ``None``: no expiry.
    """

    name: str
    model: Model
    instructions: Instructions = ""
    tools: Union[Toolset, Sequence[Any]] = field(default_factory=Toolset)
    output_type: Any = str
    output_retries: int = 1
    limits: UsageLimits = field(default_factory=UsageLimits)
    policy: Optional[ToolPolicy] = None
    context: Optional[ContextPolicy] = None
    settings: Optional[ModelSettings] = None
    hooks: Union[HookSet, Sequence[Hooks]] = ()
    redact: Optional[Redactor] = field(default_factory=Redactor, repr=False)
    approval_ttl: Optional[float] = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Agent name must be a non-empty string: traces and run states use it.")
        if not isinstance(self.model, Model):
            raise TypeError(
                f"Agent {self.name!r}: model must be a Model, got {type(self.model).__name__}. "
                "Model('qwen3.7-plus') reads the llm:qwen3.7-plus resource."
            )
        if not isinstance(self.tools, Toolset):
            object.__setattr__(self, "tools", Toolset(self.tools))
        if self.output_retries < 0:
            raise ValueError(f"Agent {self.name!r}: output_retries must be 0 or more.")
        if not isinstance(self.hooks, HookSet):
            object.__setattr__(self, "hooks", HookSet(self.hooks))
        if self.approval_ttl is not None and self.approval_ttl <= 0:
            raise ValueError(
                f"Agent {self.name!r}: approval_ttl must be positive seconds, or None for no "
                "expiry."
            )

    def clone(self, **changes: Any) -> "Agent":
        """A copy with ``changes`` applied."""
        return dataclasses.replace(self, **changes)

    def as_tool(
        self,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        max_turns: Optional[int] = None,
        **spec: Any,
    ) -> Any:
        """This agent as a tool another agent calls with a ``task``; it
        answers with its final output, never its transcript. See
        :func:`operonx_agents.compose.agent_tool`."""
        from operonx_agents.compose import agent_tool

        return agent_tool(self, name=name, description=description, max_turns=max_turns, **spec)

    def as_op(self, **options: Any) -> Any:
        """This agent as an operonx op factory, for a graph. See
        :func:`operonx_agents.compose.agent_op`."""
        from operonx_agents.compose import agent_op

        return agent_op(self, **options)

    def describe(self) -> Dict[str, Any]:
        """What the agent is, as JSON values, for a viewer (the studio's
        agent card): its model, instructions, output, limits, context and
        tools, each tool with its kind and what the policy decides for it.
        Nothing here runs the agent or builds its prompt."""
        from operonx_agents.tools.policy import DEFAULT_POLICY

        policy = self.policy or DEFAULT_POLICY
        dynamic = callable(self.instructions)
        tools = []
        for t in self.tools:
            spec = t.spec
            meta = {"readonly": spec.readonly, "destructive": spec.destructive}
            entry: Dict[str, Any] = {
                "name": t.name,
                "description": spec.description,
                "kind": t.kind,
                "readonly": spec.readonly,
                "destructive": spec.destructive,
                "approval": spec.approval if isinstance(spec.approval, str) else "when",
                "policy": policy.decide(t.name, meta),
                "sequential": spec.sequential,
                "timeout": spec.timeout,
            }
            if t.kind in ("op", "graph") and t.target is not None:
                fn = getattr(t.target, "__wrapped__", t.target)
                entry["target"] = f"{fn.__module__}:{fn.__qualname__}"
            elif t.kind == "agent" and t.target is not None:
                entry["agent"] = getattr(t.target, "name", None)
            tools.append(entry)
        limits = {k: v for k, v in dataclasses.asdict(self.limits).items() if v is not None}
        context = None
        if self.context is not None:
            c = self.context
            context = {
                "window": c.window,
                "compact_at": c.compact_at,
                "keep_recent": c.keep_recent,
                "summarizer": c.summarizer.resource if c.summarizer is not None else None,
                "clear_tool_results_after": c.clear_tool_results_after,
            }
        out = self.output_type
        return {
            "name": self.name,
            "model": list(self.model.resources),
            "instructions": {
                "text": None if dynamic else self.instructions,
                "dynamic": dynamic,
                "from": (
                    f"{self.instructions.__module__}:{self.instructions.__qualname__}"
                    if dynamic
                    else None
                ),
            },
            "output": "str" if out in (str, None) else getattr(out, "__name__", str(out)),
            "output_retries": self.output_retries,
            "limits": limits,
            "context": context,
            "hooks": [type(h).__name__ for h in self.hooks.hooks],
            "redact": self.redact is not None,
            "approval_ttl": self.approval_ttl,
            "tools": tools,
        }

    def system_prompt(self, ctx: RunContext) -> str:
        text = self.instructions(ctx) if callable(self.instructions) else self.instructions
        if not isinstance(text, str):
            raise TypeError(
                f"Agent {self.name!r}: instructions(ctx) returned {type(text).__name__}, not str."
            )
        return text
