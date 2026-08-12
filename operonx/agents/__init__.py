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
from operonx.agents.graphs.subagent import describe_delegation, make_delegate_tool
from operonx.agents.heartbeat import Heartbeat
from operonx.agents.memory import LocalMarkdownMemory, MemoryEntry, MemoryProvider
from operonx.agents.ops.compact_ops import (
    apply_compaction,
    count_tokens,
    plan_compaction,
    unmatched_tool_calls,
)
from operonx.agents.ops.prompt_ops import (
    apply_cache_control,
    assemble_api_messages,
    build_system_prompt,
)
from operonx.agents.policy import ToolPolicy
from operonx.agents.redact import Redactor
from operonx.agents.session import AgentSession
from operonx.agents.skills import Skill, inject_skills, load_skills, match_skills
from operonx.agents.tool import (
    TOOL_REGISTRY,
    ToolMeta,
    clear_registry,
    get_tool_definitions,
    tool,
)


def __getattr__(name: str):
    """Expose the MCP layer without importing it.

    ``operonx.agents.mcp`` needs the ``mcp`` SDK, which is an optional
    extra. Importing it at package import time would make
    ``from operonx.agents import tool`` fail on an install that never
    asked for MCP.
    """
    if name in ("MCPServer", "MCPClient", "MCPError", "connect_mcp", "register_mcp_tools"):
        from operonx.agents import mcp as _mcp

        return getattr(_mcp, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Heartbeat",
    "MCPServer",
    "MCPClient",
    "MCPError",
    "connect_mcp",
    "register_mcp_tools",
    "tool",
    "ToolMeta",
    "TOOL_REGISTRY",
    "get_tool_definitions",
    "clear_registry",
    "ToolPolicy",
    "Redactor",
    "MemoryProvider",
    "MemoryEntry",
    "LocalMarkdownMemory",
    "count_tokens",
    "plan_compaction",
    "apply_compaction",
    "unmatched_tool_calls",
    "build_system_prompt",
    "assemble_api_messages",
    "apply_cache_control",
    "build_dispatch",
    "build_react_agent",
    "agent_result",
    "AgentSession",
    "make_delegate_tool",
    "describe_delegation",
    "Skill",
    "load_skills",
    "match_skills",
    "inject_skills",
]
