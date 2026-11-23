"""``api_type: fake`` — scripted decisions for tests, templates and demos.

    decision:jev:
      api_type: fake
      script:
        - {refund: {noul: 0.91}, tone: {choice: angry}}   # the first call
        - {status: 429}                                   # the server says no
        - {}                                              # every later call: defaults

Each call takes the next entry; the last one repeats. An entry maps a
question name to its answer's fields; a question it leaves out gets a
default: a noul 0.5, a choice its first option, a score its middle level.
A ``choice`` with no ``probabilities`` puts all of it on the chosen label.
``{status: N}`` raises the ``DecisionError`` a server's status would — what
retries and error edges see from a real one. Nothing goes over the network.

``calls`` keeps every request, for a test to look at.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

import httpx

from operonx.core.exceptions import DecisionError
from operonx.providers.decisions.base import (
    BaseDecider,
    Decision,
    check_questions,
    normalize_answers,
)


class FakeDecider(BaseDecider):
    """Answers from ``config.script``, in order."""

    def __init__(self, config: Any, name: str = "") -> None:
        super().__init__(config, name)
        self.script: List[Any] = list(config.script or [{}])
        self.calls: List[Dict[str, Any]] = []
        self._next = 0

    async def decide(
        self,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
        assets: Optional[Mapping[str, Any]] = None,
    ) -> Decision:
        check_questions(questions, self.name)
        self.calls.append({"state": state, "questions": dict(questions), "assets": assets})
        entry = self.script[min(self._next, len(self.script) - 1)]
        self._next += 1
        if not isinstance(entry, Mapping):
            raise ValueError(f"fake decision script entry {entry!r}: a mapping")
        if "status" in entry:
            status = int(entry["status"])
            request = httpx.Request("POST", "https://fake.invalid/v1/systemone")
            response = httpx.Response(status, request=request, json={"detail": "scripted"})
            raise DecisionError(
                f"HTTP {status}: scripted",
                resource=self.name,
                questions=list(questions),
                status=status,
                response=response,
            )
        answers = {name: _answer(q, entry.get(name) or {}) for name, q in questions.items()}
        return Decision(
            answers=normalize_answers(answers, questions, self.name),
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            cost_usd=0.0,
            model="fake",
        )


def _answer(question: Mapping[str, Any], given: Mapping[str, Any]) -> Dict[str, Any]:
    kind = question.get("type")
    a: Dict[str, Any] = {"type": kind, **given}
    if kind == "noul":
        a.setdefault("noul", 0.5)
    elif kind == "choice":
        labels = list(question.get("criteria") or {})
        a.setdefault("choice", labels[0] if labels else None)
        a.setdefault("probabilities", {label: float(label == a["choice"]) for label in labels})
    elif kind == "score":
        levels = list(question.get("criteria") or [])
        a.setdefault("score", (len(levels) - 1) / 2 if levels else 0.0)
        a.setdefault("legend", {str(i): lv for i, lv in enumerate(levels)})
        a.setdefault("confidence", 0.0)
    elif kind == "box":
        a.setdefault("box", {"top": 0.0, "bottom": 1.0, "left": 0.0, "right": 1.0})
        a.setdefault("confidence", None)
    return a
