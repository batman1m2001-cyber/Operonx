"""``RunContext`` — what a tool receives besides its arguments.

A tool that declares a first parameter annotated ``RunContext`` (or
``RunContext[Deps]``) gets the context there; the model never sees that
parameter. ``deps`` is whatever the caller passed in: a CRM client, the
tenant, a limit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Generic, Optional, TypeVar

Deps = TypeVar("Deps")

__all__ = ["RunContext"]


@dataclass
class RunContext(Generic[Deps]):
    """The context of one run, as a tool sees it.

    Attributes:
        deps: The caller's dependencies, passed through untouched.
        tool_call_id: The id of the call being executed, set by dispatch
            for the duration of that call.
        metadata: Free-form values the caller wants every tool to see.
    """

    deps: Deps = None  # type: ignore[assignment]
    tool_call_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
