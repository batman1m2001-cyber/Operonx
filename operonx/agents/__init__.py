"""Agent primitives — tools, dispatch, ReAct loops, sub-agents.

This package is deliberately thin. Operonx 1.0.0 shipped the substrate an
agent framework needs — back-edge loops, ``PARENT.declare(reducers=…)``,
``Checkpointer``, ``InterruptOp``, ``EmitOp``, ``engine.stream(mode=…)``,
``LLMOp.of(fields=…, max_retries=…)`` — so what lives here is only the
composition on top: metadata carriers, subgraph factories, and pure-Python
helpers. Nothing here re-implements a core primitive, and nothing here is
a class you subclass to get an agent.

See ``AGENT_EXTENSION_PLAN.md`` for the full design and
:doc:`CONTRIBUTING <CONTRIBUTING>` for the Footprint Ladder that governs
what is allowed to land here.

**Status: P1.** Tools, permission policy, dispatch and the ReAct loop
have landed. Memory, compaction and sub-agents are P2/P3. Nothing is
exported before it works.
"""

from __future__ import annotations

from operonx.agents.graphs.dispatch import build_dispatch
from operonx.agents.graphs.react import agent_result, build_react_agent
from operonx.agents.memory import LocalMarkdownMemory, MemoryEntry, MemoryProvider
from operonx.agents.policy import ToolPolicy
from operonx.agents.tool import TOOL_REGISTRY, ToolMeta, get_tool_definitions, tool

__all__ = [
    "tool",
    "ToolMeta",
    "TOOL_REGISTRY",
    "get_tool_definitions",
    "ToolPolicy",
    "MemoryProvider",
    "MemoryEntry",
    "LocalMarkdownMemory",
    "build_dispatch",
    "build_react_agent",
    "agent_result",
]
