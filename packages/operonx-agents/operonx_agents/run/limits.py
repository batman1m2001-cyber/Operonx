"""``UsageLimits`` — the seven caps on one run, and the meter they read.

Checked before each model call and after it::

    UsageLimits(turns=8, tool_calls=20, total_tokens=60_000, cost_usd=0.05, wall_s=90)

- ``turns`` and ``tool_calls`` run out gracefully: the model gets one
  last turn, told so, with ``tool_choice="none"``, and the run ends
  ``status="limit"`` with that answer.
- ``input_tokens``, ``output_tokens``, ``total_tokens`` and ``cost_usd``
  end the run ``status="limit"`` with the best answer so far: before a
  call that would go over (the next prompt is at least as long as the
  last one), or after a call that went over.
- ``wall_s`` bounds the run's wall time. A turn still running when it
  passes is cancelled and writes nothing.

Every count is the provider's own (:class:`~operonx_agents.Usage`), and a
run started from a tool with ``parent=ctx`` adds its usage to its parent's
meter as it spends it, so a parent's limits include its children.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Optional

from operonx_agents.model.usage import Usage

__all__ = ["LIMITS", "Meter", "UsageLimits"]

#: The limit names, in the order a check reports them.
LIMITS = (
    "turns",
    "tool_calls",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cost_usd",
    "wall_s",
)


@dataclass(frozen=True)
class UsageLimits:
    """Caps on one run; ``None`` leaves one uncapped.

    Attributes:
        turns: Model calls. The last one is told to answer and cannot
            call tools. Default 25.
        tool_calls: Tool calls run. Calls past it are answered "not run".
        input_tokens / output_tokens / total_tokens: Provider-counted
            tokens over every request of the run and its children.
        cost_usd: Their price, from the resources' declared prices.
        wall_s: Seconds of wall time.

    Raises:
        ValueError: on a cap that is not positive — a zero budget would
            end every run before its first call.
    """

    turns: Optional[int] = 25
    tool_calls: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    wall_s: Optional[float] = None

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(
                    f"UsageLimits {f.name}={value!r} must be a positive number, or None for "
                    "no cap: a zero budget would end the run before its first call."
                )
            if f.name in ("turns", "tool_calls") and value != math.floor(value):
                raise ValueError(f"UsageLimits {f.name}={value!r} must be a whole number.")

    def before_call(self, usage: Usage, next_input: int) -> Optional[str]:
        """The spending cap a call would break: already spent, or the next
        prompt, at least ``next_input`` tokens, would go over."""
        if self.input_tokens is not None and usage.input_tokens + next_input > self.input_tokens:
            return "input_tokens"
        if self.output_tokens is not None and usage.output_tokens >= self.output_tokens:
            return "output_tokens"
        if self.total_tokens is not None and usage.total_tokens + next_input > self.total_tokens:
            return "total_tokens"
        if self.cost_usd is not None and _cost(usage) >= self.cost_usd:
            return "cost_usd"
        return None

    def after_call(self, usage: Usage) -> Optional[str]:
        """The spending cap ``usage`` is over."""
        for name, spent in (
            ("input_tokens", usage.input_tokens),
            ("output_tokens", usage.output_tokens),
            ("total_tokens", usage.total_tokens),
        ):
            cap = getattr(self, name)
            if cap is not None and spent > cap:
                return name
        if self.cost_usd is not None and _cost(usage) > self.cost_usd:
            return "cost_usd"
        return None


def _cost(usage: Usage) -> float:
    if usage.cost_usd is None:
        raise ValueError(
            "UsageLimits(cost_usd=...) is set, but a resource this run used declares no "
            "price, so its cost is unknown. Give every llm: resource cost_per_input_token "
            "and cost_per_output_token, or cap tokens instead."
        )
    return usage.cost_usd


class Meter:
    """What a run has spent, live. A child run's meter adds to its
    parent's as it spends, so the parent's next check sees it."""

    __slots__ = ("total", "parent")

    def __init__(self, parent: Optional["Meter"] = None, start: Optional[Usage] = None) -> None:
        self.total = start or Usage()
        self.parent = parent

    def add(self, usage: Usage) -> None:
        meter: Optional[Meter] = self
        while meter is not None:
            meter.total = meter.total + usage
            meter = meter.parent
