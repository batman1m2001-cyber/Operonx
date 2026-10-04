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
        if usage is None:
            raw: dict = {}
        elif isinstance(usage, dict):
            raw = usage
        else:
            raw = usage.model_dump() if hasattr(usage, "model_dump") else dict(vars(usage))
        prompt = raw.get("prompt_details") or raw.get("prompt_tokens_details") or {}
        completion = raw.get("completion_tokens_details") or {}
        input_tokens = int(raw.get("prompt_tokens") or 0)
        output_tokens = int(raw.get("completion_tokens") or 0)
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
            cached_input_tokens=int((prompt or {}).get("cached_tokens") or 0),
            reasoning_tokens=int((completion or {}).get("reasoning_tokens") or 0),
            requests=1,
            cost_usd=cost,
        )
