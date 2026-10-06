"""What the runner tests share: scripted replies, tools that record what
ran, and a one-call way to build an agent on a scripted model."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from operonx_agents import Agent, Model, RunContext, tool
from operonx_agents.context.compaction import unmatched_tool_calls
from operonx_agents.testing import ScriptedLLM, asks, calls, says  # noqa: F401 — re-exported

RAN: List[str] = []


@tool(readonly=True)
async def echo(a: int) -> dict:
    """Echo a number."""
    RAN.append(f"echo:{a}")
    return {"a": a}


@tool
async def note(text: str) -> str:
    """Write a note (not readonly, idempotent)."""
    RAN.append(f"note:{text}")
    return "saved"


@tool(idempotent=False)
async def charge(amount: int) -> str:
    """Charge a card (not idempotent)."""
    RAN.append(f"charge:{amount}")
    return f"charged {amount}"


@tool(readonly=True)
async def slow(seconds: float) -> str:
    """Wait."""
    await asyncio.sleep(seconds)
    RAN.append("slow")
    return "done"


TOOLS = [echo, note, charge, slow]


def strict(script: List[Any]):
    """A model that rejects a history the way a provider does: 400 when an
    assistant tool call has no tool message answering it."""
    state = {"i": 0}

    def reply(messages, params):
        broken = unmatched_tool_calls(messages)["calls_without_results"]
        if broken:
            raise RuntimeError(f"400 Bad Request: tool_calls without results: {broken}")
        i = state["i"]
        state["i"] += 1
        return script[i] if i < len(script) else says(f"reply {i}")

    return reply


def make(hub, *script: Any, name: str = "m", llm_kw: Dict[str, Any] = None, **agent_kw: Any):
    """An agent on a scripted model, and the model's backend."""
    llm = ScriptedLLM(*script, **(llm_kw or {}))
    hub(**{name: llm})
    agent_kw.setdefault("tools", TOOLS)
    return Agent(name="agent", model=Model(name), **agent_kw), llm


def roles(messages: List[dict]) -> List[str]:
    return [m.get("role") for m in messages]


def tool_ids(messages: List[dict]) -> List[str]:
    return [m["tool_call_id"] for m in messages if m.get("role") == "tool"]


__all__ = ["RAN", "RunContext"]
