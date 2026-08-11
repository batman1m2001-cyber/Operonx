"""09b · Human-in-the-loop — approving destructive tool calls.

Runs with **no API key**: the model is scripted, so what you are looking
at is the approval machinery rather than a language model. Swap
``scripted_model`` for ``LLMOp.of(...)`` and nothing else changes.

    uv run python hitl.py            # prompts you per destructive call
    uv run python hitl.py --deny-all # non-interactive, refuses everything

The harness is the part worth copying — roughly fifteen lines:

    handle = Operon(agent).start(inputs=...)
    bind_interrupt_bus(handle.state, sink=on_approval)
    await handle.result()

``sink`` is called with an ``InterruptEvent`` carrying the tool name and
its arguments; answering is ``handle.state.resume_interrupt(id, value)``.
Any surface can drive it — CLI, TUI, an HTTP endpoint holding the request
open — because the agent is suspended, not polling.
"""

from __future__ import annotations

import asyncio
import sys

from operonx.agents import ToolPolicy, agent_result, build_react_agent, tool
from operonx.checkpoint import bind_interrupt_bus
from operonx.core import Operon, op

# ── Tools ───────────────────────────────────────────────────────────────

FILES = {"notes.txt": "buy milk", "secrets.txt": "hunter2"}


@tool(
    name="list_files",
    description="List the files in the workspace.",
    schema={"type": "object", "properties": {}},
    readonly=True,
)
async def list_files() -> dict:
    return {"files": sorted(FILES)}


@tool(
    name="delete_file",
    description="Permanently delete a file. This cannot be undone.",
    schema={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "File to delete."}},
        "required": ["path"],
    },
    destructive=True,
)
async def delete_file(path: str) -> dict:
    if path not in FILES:
        # Returning rather than raising keeps the model informed — but
        # dispatch would have caught a raise and told it anyway.
        return {"deleted": False, "reason": f"no such file: {path}"}
    del FILES[path]
    return {"deleted": True, "path": path}


# ── A scripted model, so this runs without a provider ───────────────────


def scripted_model(script):
    """Replay ``script`` — a list of (tool_calls, done) per turn."""
    state = {"i": 0}

    @op
    def call_model(messages: list = None) -> dict:
        i = state["i"]
        state["i"] += 1
        calls, done = script[i] if i < len(script) else ([], True)
        text = "Done — see above." if done else f"Step {i + 1}."
        return {
            "assistant_message": [{"id": f"a{i}", "role": "assistant", "content": text}],
            "tool_calls": calls,
            "done": done,
        }

    return call_model


SCRIPT = [
    ([{"id": "c1", "name": "list_files", "args": {}}], False),
    ([{"id": "c2", "name": "delete_file", "args": {"path": "secrets.txt"}}], False),
]


# ── The harness ─────────────────────────────────────────────────────────


async def main() -> int:
    auto_deny = "--deny-all" in sys.argv

    agent = build_react_agent(
        call_model=scripted_model(SCRIPT),
        max_turns=8,
        approval_timeout=120.0,
        # Readonly tools run freely; destructive ones need a human.
        policy=ToolPolicy(default="allow", destructive="ask", readonly="allow"),
    )(messages=None)

    handle = Operon(agent).start(
        inputs={"messages": [{"role": "user", "content": "Clean up the workspace."}]}
    )

    def on_approval(event) -> None:
        """Called while the agent is suspended on this exact call."""
        payload = event.payload
        print(f"\n  APPROVAL NEEDED: {payload['tool']}")
        print(f"    {payload['description']}")
        print(f"    arguments: {payload['args']}")

        if auto_deny:
            approved = False
            print("    → denied (--deny-all)")
        else:
            approved = input("    allow? [y/N] ").strip().lower() in ("y", "yes")

        # Anything not `{"approved": True}` is a refusal. Answering is
        # what un-suspends the agent; without it the call expires after
        # `approval_timeout` and is treated as declined.
        handle.state.resume_interrupt(event.interrupt_id, {"approved": approved})

    bind_interrupt_bus(handle.state, sink=on_approval)

    await handle.result()
    # handle.result() is built from emitted frames and carries no state,
    # so read the conversation from the handle's MemoryState.
    answer = agent_result(handle.state, agent)

    print(f"\n  turns: {answer['turns']}  stopped_early: {answer['stopped_early']}")
    print("  conversation:")
    for message in answer["messages"]:
        role = message.get("role", "?")
        body = str(message.get("content", ""))
        marker = " !" if message.get("status") == "error" else "  "
        print(f"   {marker} {role:<9} {body[:88]}")

    print(f"\n  files remaining: {sorted(FILES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
