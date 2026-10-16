"""operonx-project: take a project's requirements to a verified branch.

A **project** is the product repo. It keeps a living BRD and a checklist in
``docs/brd/`` and a board of **cards** per **milestone** (a goal and the
requirement rows it covers). Coding agents implement cards in their own git
worktrees, a different session reviews each diff, and the repo's own tests
(plus held-out ones the implementer never sees) decide. A card that needs
the owner parks its journalled run and resumes when the answer arrives.

The plan of record is ``docs/PROJECT_ENGINE_PLAN.md`` in the operonx repo.
"""

from operonx_project.model import (
    BudgetPolicy,
    Card,
    CardState,
    Milestone,
    MilestoneState,
    Policy,
    Stage,
)
from operonx_project.store import IllegalTransition, Store

__version__ = "0.1.0"

__all__ = [
    "BudgetPolicy",
    "Card",
    "CardState",
    "IllegalTransition",
    "Milestone",
    "MilestoneState",
    "Policy",
    "Stage",
    "Store",
    "__version__",
]
