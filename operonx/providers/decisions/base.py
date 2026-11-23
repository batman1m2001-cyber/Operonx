"""What every decision backend shares: the result, the checks, the answers.

A backend answers one call: ``await decider.decide(state, questions)``.
The questions are the System One wire format, name -> question::

    {"refund": {"type": "noul", "instructions": "The customer asks for a refund."},
     "tone":   {"type": "choice", "instructions": "The customer's tone.",
                "criteria": {"calm": None, "angry": None}}}

and the answers come back keyed the same way, each one the server's answer
with a ``confidence`` added where the server sent none (a noul).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from operonx.core.exceptions import DecisionError

__all__ = [
    "BaseDecider",
    "Decision",
    "check_questions",
    "normalize_answers",
    "server_message",
    "verdict",
]


@dataclass
class Decision:
    """One decision call's result.

    ``usage`` uses the LLM names (``prompt_tokens``, ``completion_tokens``,
    ``total_tokens``) so a run row counts its tokens as it counts a model's.
    ``cost_usd`` is None when the route reports no cost and the resource
    states no price: unknown, not free.
    """

    answers: Dict[str, Dict[str, Any]]
    usage: Dict[str, int] = field(default_factory=dict)
    cost_usd: Optional[float] = None
    model: Optional[str] = None

    def merged(self, other: "Decision") -> "Decision":
        """Both halves of a split request as one result."""
        usage = {
            k: self.usage.get(k, 0) + other.usage.get(k, 0) for k in {*self.usage, *other.usage}
        }
        cost = (
            None
            if self.cost_usd is None or other.cost_usd is None
            else self.cost_usd + other.cost_usd
        )
        return Decision({**self.answers, **other.answers}, usage, cost, self.model or other.model)


class BaseDecider(ABC):
    """A ``decision:`` resource's instance."""

    def __init__(self, config: Any, name: str = "") -> None:
        self.config = config
        self.name = name

    @abstractmethod
    async def decide(
        self,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
        assets: Optional[Mapping[str, Any]] = None,
    ) -> Decision:
        """Ask every question about ``state`` in one request."""


def check_questions(questions: Any, resource: str = "") -> None:
    """Refuse what no server would take, before it is sent.

    Only the outline: a non-empty mapping of name -> mapping with a string
    ``type``. The rest is the server's to judge — its 422 names the field —
    and a type this code does not know passes, as new ones do in TypeSafe's
    own SDK.
    """
    if not isinstance(questions, Mapping) or not questions:
        raise DecisionError(
            "questions must be a non-empty mapping of name -> question "
            '({"refund": {"type": "noul", "instructions": "..."}})',
            resource=resource,
            original_error=TypeError(f"got {type(questions).__name__}"),
        )
    for name, q in questions.items():
        if not isinstance(name, str) or not name:
            raise DecisionError(f"question name {name!r} is not a string", resource=resource)
        if not isinstance(q, Mapping) or not isinstance(q.get("type"), str):
            raise DecisionError(
                f"question {name!r} must be a mapping with a type: noul, choice, score or box",
                resource=resource,
                questions=list(questions),
            )


def _noul_confidence(p: float) -> float:
    return abs(2.0 * p - 1.0)


def _choice_confidence(probabilities: Mapping[str, Any]) -> Optional[float]:
    """TypeSafe's definition: the top probability rescaled so a uniform
    answer is 0 and a certain one 1."""
    try:
        ps = [float(v) for v in probabilities.values()]
    except (TypeError, ValueError, AttributeError):
        return None
    n = len(ps)
    if n < 2:
        return 1.0 if n == 1 else None
    return (max(ps) - 1.0 / n) / (1.0 - 1.0 / n)


def normalize_answers(
    answers: Any, questions: Mapping[str, Any], resource: str = ""
) -> Dict[str, Dict[str, Any]]:
    """The server's answers, checked against the questions, with a
    ``confidence`` added where the server sent none. Nothing is removed.

    A wrong ``base_url`` can answer 200 with a page that is not an answer:
    the shape is what tells, not the status.
    """
    if not isinstance(answers, Mapping) or set(answers) != set(questions):
        got = sorted(answers) if isinstance(answers, Mapping) else type(answers).__name__
        raise DecisionError(
            f"the server answered without the expected answers (got {got}); "
            "check the resource's base_url and model",
            resource=resource,
            questions=list(questions),
        )
    out: Dict[str, Dict[str, Any]] = {}
    for name, raw in answers.items():
        if not isinstance(raw, Mapping):
            raise DecisionError(f"answer {name!r} is not an object: {raw!r}", resource=resource)
        a = dict(raw)
        kind = a.get("type") or questions[name].get("type")
        a.setdefault("type", kind)
        if a.get("confidence") is None:
            if kind == "noul" and isinstance(a.get("noul"), (int, float)):
                a["confidence"] = _noul_confidence(float(a["noul"]))
            elif kind == "choice" and isinstance(a.get("probabilities"), Mapping):
                a["confidence"] = _choice_confidence(a["probabilities"])
        out[name] = a
    return out


def verdict(answer: Mapping[str, Any], at: float = 0.7) -> Any:
    """An answer as a decision, or None when it is not sure enough.

    TypeSafe's advice, applied: a noul is True at ``p >= at``, False at
    ``p <= 1 - at``, and None between — the unsure band is its own outcome,
    not a coin flip. A choice is its label when that label's probability
    is at least ``at``, else None.

        verdict({"type": "noul", "noul": 0.91})    # True
        verdict({"type": "noul", "noul": 0.55})    # None

    A score or a box has no single "sure": what counts depends on the
    scale, so those raise; read ``confidence`` and ``probabilities``.
    """
    if not 0.5 <= at <= 1.0:
        raise ValueError(f"verdict(at={at}): a threshold between 0.5 and 1")
    kind = answer.get("type")
    if kind == "noul":
        p = float(answer["noul"])
        if p >= at:
            return True
        if p <= 1.0 - at:
            return False
        return None
    if kind == "choice":
        label = answer.get("choice")
        p = (answer.get("probabilities") or {}).get(label)
        return label if p is not None and float(p) >= at else None
    raise ValueError(f"verdict() takes a noul or a choice answer, not {kind!r}")


def server_message(body: Any, limit: int = 600) -> str:
    """The error a System One server sent, from either shape.

    TypeSafe sends ``{"detail": ...}`` (a string, an object, or a list of
    pydantic errors); OpenRouter wraps the provider's as
    ``{"error": {"message": "HTTP 422: {...}", "code": 422}}``.
    """
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", "replace")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return body.strip()[:limit]
    if not isinstance(body, Mapping):
        return str(body)[:limit]
    err = body.get("error")
    if isinstance(err, Mapping) and isinstance(err.get("message"), str):
        return err["message"][:limit]
    for key in ("message", "error"):
        if isinstance(body.get(key), str):
            return body[key][:limit]
    detail = body.get("detail")
    if isinstance(detail, str):
        return detail[:limit]
    if isinstance(detail, list):
        parts: List[str] = []
        for d in detail:
            if isinstance(d, Mapping):
                loc = ".".join(str(x) for x in d.get("loc") or () if x != "body")
                parts.append(f"{loc}: {d.get('msg')}" if loc else str(d.get("msg")))
            else:
                parts.append(str(d))
        return "; ".join(parts)[:limit]
    if detail is not None:
        return json.dumps(detail, ensure_ascii=False)[:limit]
    return json.dumps(body, ensure_ascii=False)[:limit]
