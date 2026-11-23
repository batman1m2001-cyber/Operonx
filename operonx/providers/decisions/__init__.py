"""Typed decisions: a ``decision:`` resource asks a System One classifier
(TypeSafe Jev) typed questions about a state and gets probabilities back.

The HTTP backend is lazy-loaded; the rest is light. See docs/JEV_PLAN.md.
"""

from operonx.providers.decisions.base import (
    BaseDecider,
    Decision,
    check_questions,
    normalize_answers,
    verdict,
)
from operonx.providers.decisions.config import DecisionConfig, DecisionType
from operonx.providers.decisions.factory import create_decision

_LAZY_BACKENDS = {
    "SystemOneDecider": "operonx.providers.decisions.system_one",
    "FakeDecider": "operonx.providers.decisions.fake",
}


def __getattr__(name: str):
    if name in _LAZY_BACKENDS:
        import importlib

        cls = getattr(importlib.import_module(_LAZY_BACKENDS[name]), name)
        globals()[name] = cls
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BaseDecider",
    "Decision",
    "DecisionConfig",
    "DecisionType",
    "FakeDecider",
    "SystemOneDecider",
    "check_questions",
    "create_decision",
    "normalize_answers",
    "verdict",
]
