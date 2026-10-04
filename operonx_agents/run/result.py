"""``RunResult`` — how a run ended, never a silent ``{}``.

::

    res = await Runner.run(agent, "refund order A1B2C3D4", deps=deps)
    match res.status:
        case "completed": res.output        # str, or the output_type's value
        case "limit":     res.limit_hit     # which cap; res.output is the best answer so far
        case "failed":    res.error         # "ModelTimeout: ..."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from operonx_agents.model.usage import Usage
from operonx_agents.run.state import Status

__all__ = ["RunResult", "TRUNCATED_REASONS"]

#: Stop reasons that mean the answer was cut, not finished.
TRUNCATED_REASONS = frozenset({"length", "max_tokens", "content_filter"})


@dataclass(frozen=True)
class RunResult:
    """Attributes:
    status: ``completed``, ``limit`` (a cap ran out) or ``failed``.
    output: The answer: text, or the agent's ``output_type`` value.
        For ``limit``, the last answer the model gave, else ``None``.
        For ``failed``, ``None``: a stale answer is worse than none.
    usage: What the run spent, its children's runs included.
    run_id: The run's id; ``Runner.resume`` takes it.
    turns: Model turns committed.
    messages: The conversation as the model last saw it (no system prompt).
    new_items: What the run added to its session.
    limit_hit: The cap that ran out, for ``limit``.
    error: ``"TypeName: message"``, for ``failed``.
    finish_reason: The last model answer's stop reason.
    """

    status: Status
    output: Any
    usage: Usage
    run_id: str
    turns: int
    messages: List[Dict[str, Any]] = field(default_factory=list)
    new_items: List[Dict[str, Any]] = field(default_factory=list)
    limit_hit: Optional[str] = None
    error: Optional[str] = None
    finish_reason: Optional[str] = None

    @property
    def truncated(self) -> bool:
        """The answer was cut (``finish_reason`` ``length``/``max_tokens``)."""
        return self.finish_reason in TRUNCATED_REASONS

    @property
    def state_id(self) -> str:
        return self.run_id

    def to_dict(self) -> Dict[str, Any]:
        output = self.output
        if isinstance(output, BaseModel):
            output = output.model_dump(mode="json")
        return {
            "status": self.status,
            "output": output,
            "usage": self.usage.to_dict(),
            "run_id": self.run_id,
            "turns": self.turns,
            "messages": self.messages,
            "new_items": self.new_items,
            "limit_hit": self.limit_hit,
            "error": self.error,
            "finish_reason": self.finish_reason,
        }
