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

**Status: scaffold.** P0 (namespace + governance) is complete; the public
surface below arrives in P1. ``__all__`` stays empty until there is
something real to export — an empty package is honest, a package full of
``NotImplementedError`` stubs is not.

Planned surface (P1–P3)::

    from operonx.agents import tool, TOOL_REGISTRY   # P1
    from operonx.agents import build_react_agent     # P1
    from operonx.agents import subagent              # P3
"""

from __future__ import annotations

__all__: list[str] = []
