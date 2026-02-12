"""Control-flow ops for conditional routing.

- BranchOp: Evaluates conditions and routes to a target op.
- Branch: Fluent builder for creating BranchOp instances.
- if_: Shorthand to start a branch declaration.
"""

from .branch_op import Branch, BranchOp, if_

__all__ = [
    "BranchOp",
    "Branch",
    "if_",
]
