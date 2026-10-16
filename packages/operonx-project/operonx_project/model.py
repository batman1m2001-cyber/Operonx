"""The nouns: a project's milestones, their cards, and the policy that runs them.

A **milestone** is one push of work on a project (a goal and the requirement
rows it covers). Its pieces of work are **cards**; a card's work happens in
rounds (``task_flow``). States are plain strings in enums so the store keeps
them readable (``sqlite3`` shell, the studio) without a lookup table.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

__all__ = [
    "BudgetPolicy",
    "Card",
    "CardState",
    "FINAL_CARD_STATES",
    "Milestone",
    "MilestoneState",
    "Policy",
    "Stage",
]


class CardState(str, Enum):
    PENDING = "pending"  # a dependency is not done yet
    READY = "ready"  # could start; waits for a slot or a path lock
    RUNNING = "running"  # its task_flow run is going (see Stage)
    WAITING_HUMAN = "waiting_human"  # its run is parked on a question
    VERIFIED = "verified"  # passed; waits for the merge lock
    DONE = "done"  # merged, regression checks green
    FAILED = "failed"  # gave up, with a reason
    BLOCKED = "blocked"  # a dependency failed or was cancelled
    CANCELLED = "cancelled"  # by the owner, or a replan removed it


#: States a card does not leave by itself. ``waiting_human`` is not final:
#: an answer resumes it; ``blocked`` can be re-opened by a replan.
FINAL_CARD_STATES = frozenset({CardState.DONE, CardState.FAILED, CardState.CANCELLED})


class Stage(str, Enum):
    """Where a running card is inside its round: the board column it shows in."""

    IMPLEMENT = "implement"
    REVIEW = "review"
    QA = "qa"


class MilestoneState(str, Enum):
    CREATED = "created"
    DISCOVERING = "discovering"
    REQUIREMENTS = "requirements"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"  # nothing can run; at least one question open
    PAUSED = "paused"
    VERIFYING = "verifying"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass
class BudgetPolicy:
    max_usd: float = 30.0
    max_hours: float = 8.0  # wall clock while running; waiting for the owner does not count
    card_max_usd: float = 5.0
    max_rounds: int = 6
    max_questions: int = 5
    max_replans: int = 2
    max_cards: int = 25
    max_reopens: int = 2  # integration conflicts / regressions per card
    plan_reviews: int = 2  # revise rounds before the plan goes to the owner with its notes


@dataclass
class Policy:
    """How a milestone's cards are run (plan §5.5).

    W1 (spec → DAG) is ``horizon="all", start="push", done_unit="task"``;
    W2 (BRD → checklist → board) is ``horizon="ready_depth", start="pull",
    done_unit="row"``.
    """

    horizon: str = "ready_depth"  # "all" | "ready_depth"
    start: str = "pull"  # "push" | "pull"
    done_unit: str = "row"  # "task" | "row"
    heldout: bool = True
    max_parallel: int = 2  # coder slots: the In progress column's limit
    wip: Dict[str, int] = field(default_factory=lambda: {"review": 2, "qa": 2})
    ready_depth: Optional[int] = None  # default 2 × max_parallel
    approve_plan: str = "ask"  # "ask": one tap; "auto": when the review passed and no card is risky
    budget: BudgetPolicy = field(default_factory=BudgetPolicy)

    def __post_init__(self) -> None:
        if self.horizon not in ("all", "ready_depth"):
            raise ValueError(f"horizon must be 'all' or 'ready_depth', not {self.horizon!r}")
        if self.start not in ("push", "pull"):
            raise ValueError(f"start must be 'push' or 'pull', not {self.start!r}")
        if self.done_unit not in ("task", "row"):
            raise ValueError(f"done_unit must be 'task' or 'row', not {self.done_unit!r}")
        if self.approve_plan not in ("ask", "auto"):
            raise ValueError(f"approve_plan must be 'ask' or 'auto', not {self.approve_plan!r}")
        if self.max_parallel < 1:
            raise ValueError("max_parallel must be at least 1")
        if isinstance(self.budget, dict):
            self.budget = BudgetPolicy(**self.budget)

    @classmethod
    def w1(cls, **kw: Any) -> "Policy":
        return cls(horizon="all", start="push", done_unit="task", **kw)

    @classmethod
    def w2(cls, **kw: Any) -> "Policy":
        return cls(horizon="ready_depth", start="pull", done_unit="row", **kw)

    @property
    def depth(self) -> int:
        return self.ready_depth if self.ready_depth is not None else 2 * self.max_parallel

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Policy":
        return cls(**(data or {}))


@dataclass
class Card:
    id: str
    milestone: str
    title: str
    intent: str = ""
    rows: List[str] = field(default_factory=list)  # requirement ids it serves
    criteria: List[str] = field(default_factory=list)  # EARS statements, each tied to a row
    deps: List[str] = field(default_factory=list)
    paths: List[str] = field(default_factory=list)  # globs it may touch
    kind: str = "code"  # code | test | docs | config | init (greenfield: `operonx init`, run by the engine)
    risk: str = "low"  # low | high
    priority: int = 0  # lower runs first
    epic: str = ""
    state: CardState = CardState.PENDING
    stage: Optional[Stage] = None
    reason: str = ""
    reopens: int = 0
    run_id: Optional[str] = None
    cost_usd: float = 0.0
    detail: Dict[str, Any] = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.state = CardState(self.state)
        self.stage = Stage(self.stage) if self.stage else None


@dataclass
class Milestone:
    id: str
    project: str  # the project's root path
    title: str
    goal: str = ""
    rows: List[str] = field(default_factory=list)  # target scope; empty = every row not verified
    state: MilestoneState = MilestoneState.CREATED
    policy: Policy = field(default_factory=Policy)
    reason: str = ""
    cost_usd: float = 0.0
    replans: int = 0
    resume_at: Optional[float] = None
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.state = MilestoneState(self.state)
        if isinstance(self.policy, dict):
            self.policy = Policy.from_dict(self.policy)
