"""``Usage`` — token counts and cost, from the provider's own numbers.

Every budget decision reads these, never an estimate: the 3.5
characters-per-token guess of ``operonx.agents`` is off by a large factor
for Vietnamese. Normalised from the OpenAI-shaped usage every operonx
backend returns (the Anthropic backend converts its counts to it).
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Optional

__all__ = ["Usage"]


@dataclass(frozen=True)
class Usage:
    """Attributes:
    input_tokens: Prompt tokens, cached ones included.
    output_tokens: Completion tokens, reasoning included.
    cached_input_tokens: Prompt tokens served from the provider's cache.
    reasoning_tokens: Completion tokens spent thinking.
    requests: Model requests made (a re-ask or a fallback is one more).
    cost_usd: The resources' declared prices times the counts; ``None``
        when a resource used declares no price.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    requests: int = 0
    cost_usd: Optional[float] = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        cost = (
            None
            if self.cost_usd is None or other.cost_usd is None
            else self.cost_usd + other.cost_usd
        )
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            requests=self.requests + other.requests,
            cost_usd=cost,
        )

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_completion(cls, usage: Any, config: Any = None) -> "Usage":
        """One request's usage from a completion's ``usage`` (an SDK
        object, a dict, or ``None``) and the resource config's prices."""
        get = _field
        prompt = get(usage, "prompt_tokens_details") or get(usage, "prompt_details")
        completion = get(usage, "completion_tokens_details")
        input_tokens = int(get(usage, "prompt_tokens") or 0)
        output_tokens = int(get(usage, "completion_tokens") or 0)
        price_in = getattr(config, "cost_per_input_token", None)
        price_out = getattr(config, "cost_per_output_token", None)
        cost = (
            None
            if price_in is None or price_out is None
            else input_tokens * price_in + output_tokens * price_out
        )
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=int(get(prompt, "cached_tokens") or 0),
            reasoning_tokens=int(get(completion, "reasoning_tokens") or 0),
            requests=1,
            cost_usd=cost,
        )


def _field(value: Any, name: str) -> Any:
    """``value[name]`` of a dict, the field of an SDK object (a vendor
    field from its extras), ``None`` for ``None`` or a field it lacks.
    Read without dumping the object, and without the ``AttributeError`` a
    missing field costs a pydantic model: one usage per request."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(name)
    fields = getattr(value, "__dict__", None)
    if fields is None:
        return getattr(value, name, None)
    if name in fields:
        return fields[name]
    extra = getattr(value, "__pydantic_extra__", None)
    return extra.get(name) if extra else None
