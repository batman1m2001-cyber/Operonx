"""The bash tool.

Marked destructive, so the permission gate asks before every call. That is
the right default and an uncomfortable one: `ls` is harmless and `rm -rf`
is not, and no static classifier separates them reliably. The policy layer
is where a caller relaxes this — per-session, knowingly — rather than here.
"""

from __future__ import annotations

from operonx.agents import tool
from operonx_code.shell import ShellTimeout, active_shell

__all__ = ["run_bash"]

_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": (
                "Shell command to run. State persists between calls — cd, "
                "export and activated environments carry over."
            ),
        },
        "timeout": {
            "type": "number",
            "description": (
                "Seconds to wait, default 120. On timeout the shell is "
                "killed and replaced, losing any cd or export."
            ),
        },
    },
    "required": ["command"],
}


@tool(
    name="bash",
    description=(
        "Run a command in a persistent bash session. cd and export carry "
        "over between calls. Prefer read/glob/grep for inspecting files — "
        "they are cheaper and are not gated on approval."
    ),
    schema=_SCHEMA,
    destructive=True,
    max_result_chars=40_000,
)
async def run_bash(command: str, timeout: float = 120.0) -> dict:
    shell = active_shell()
    try:
        result = await shell.run(command, timeout=float(timeout or 120.0))
    except ShellTimeout as e:
        # Returned rather than raised: a timeout is information the model
        # can act on — split the work, redirect to a file, raise the limit
        # — and an exception would reach it as an opaque tool error.
        return {"output": str(e), "exit_code": 124, "timed_out": True}
    return {
        "output": result.output,
        "exit_code": result.exit_code,
        "timed_out": False,
        "cwd": result.cwd,
        "truncated": result.truncated,
    }
