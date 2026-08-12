"""operonx-code — a reference coding agent built on ``operonx.agents``.

Deliberately a **sibling package**, not part of the framework. A coding
agent needs opinions — which tools exist, what the system prompt says,
when to ask a human — and opinions in a framework become defaults nobody
can change. Everything reusable lives in ``operonx.agents``; what is here
is the composition.

The whole harness is about 900 lines, of which roughly 200 are the agent
itself. If it needed a thousand lines of its own control flow, the agent
layer would have the wrong shape — so this package doubles as a test of
that claim.

Usage::

    from operonx_code import build_coding_agent
    from operonx.agents import AgentSession

    async with build_coding_agent(root=".", resource="code") as agent:
        session = AgentSession(agent.graph)
        result = await session.send("what does workspace.py do?")
        print(result["final"]["content"])

Or from a terminal::

    operonx-code --root . --resource code
"""

from __future__ import annotations

from operonx_code.agent import SYSTEM_PROMPT, CodingAgent, build_coding_agent
from operonx_code.shell import PersistentShell, ShellResult, ShellTimeout
from operonx_code.workspace import (
    OutsideWorkspace,
    StaleRead,
    Workspace,
    WorkspaceError,
)

__version__ = "0.1.0"

__all__ = [
    "build_coding_agent",
    "CodingAgent",
    "SYSTEM_PROMPT",
    "Workspace",
    "WorkspaceError",
    "OutsideWorkspace",
    "StaleRead",
    "PersistentShell",
    "ShellResult",
    "ShellTimeout",
    "__version__",
]
