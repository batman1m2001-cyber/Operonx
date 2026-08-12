"""Terminal front end.

The interesting part is approval. `InterruptOp` fires on the state's bus
from inside a running graph, and the sink is called on the event loop — so
blocking it to ask a human would stall every other branch of the same
turn, including sibling tool calls waiting on their own approval.

The prompt therefore runs in a thread and answers the interrupt when it
returns. Sibling calls keep flowing, and the loop stays live.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Optional

from operonx.agents import AgentSession
from operonx_code.agent import build_coding_agent

__all__ = ["main", "run_repl"]

BANNER = """\
operonx-code — a coding agent on operonx
  workspace: {root}
  model:     {model}
  approval:  {approval}

/help for commands, Ctrl-D to exit.
"""

HELP = """\
  /tools     list the tools the model can call
  /messages  show the conversation so far
  /cwd       where the persistent shell currently is
  /reset     start a fresh conversation (keeps the shell)
  /quit      exit
"""


def _c(text: str, code: str) -> str:
    """Colour, unless the output is redirected or NO_COLOR is set."""
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"


def _render_call(payload: dict) -> str:
    name = payload.get("tool", "?")
    args = payload.get("args") or {}
    if name == "bash":
        body = str(args.get("command", ""))
    elif name in ("write", "edit"):
        body = str(args.get("path", ""))
    else:
        body = ", ".join(f"{k}={v!r}" for k, v in args.items())
    if len(body) > 400:
        body = body[:400] + " …"
    return f"{_c(name, '1;33')} {body}"


class Approver:
    """Asks the terminal, without blocking the event loop."""

    def __init__(self, session: AgentSession) -> None:
        self.session = session
        self.always: set[str] = set()

    def __call__(self, event: Any) -> None:
        asyncio.create_task(self._ask(event))

    async def _ask(self, event: Any) -> None:
        payload = getattr(event, "payload", {}) or {}
        name = payload.get("tool", "?")
        if name in self.always:
            self.session.approve(event, True, "pre-approved for this session")
            return

        print()
        print(_c("  needs approval:", "1;35"), _render_call(payload))
        try:
            # to_thread so the event loop keeps serving sibling branches
            # that are waiting on their own approvals.
            answer = (await asyncio.to_thread(input, "  [y]es / [n]o / [a]lways: ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer.startswith("a"):
            self.always.add(name)
            self.session.approve(event, True, "always-allow for this session")
        elif answer.startswith("y"):
            self.session.approve(event, True)
        else:
            self.session.approve(event, False, "declined by the user")


async def run_repl(
    *,
    root: Path,
    resource: str,
    model_label: str,
    yes_mode: bool,
    max_turns: int,
) -> int:
    agent = build_coding_agent(
        root=root,
        resource=resource,
        yes_mode=yes_mode,
        max_turns=max_turns,
    )
    session = AgentSession(agent.graph, system="")
    approver = Approver(session)

    print(
        BANNER.format(
            root=agent.workspace.root,
            model=model_label,
            approval=_c("OFF — every tool runs unattended", "1;31")
            if yes_mode
            else "ask before bash / write / edit",
        )
    )

    try:
        while True:
            try:
                line = (await asyncio.to_thread(input, _c("› ", "1;36"))).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not line:
                continue

            if line.startswith("/"):
                if _command(line, session, agent) is False:
                    return 0
                continue

            try:
                result = await session.send(line, on_approval=approver)
            except asyncio.TimeoutError:
                print(_c("  turn timed out", "1;31"))
                continue
            except Exception as e:  # noqa: BLE001 — a REPL must survive its own turn
                print(_c(f"  turn failed: {type(e).__name__}: {e}", "1;31"))
                continue

            final = result.get("final") or {}
            content = final.get("content") or _c("(no answer — see the error above)", "1;31")
            print(f"\n{content}\n")
            if result.get("stopped_early"):
                print(_c("  stopped at the turn budget — the answer may be partial", "1;33"))
    finally:
        await agent.aclose()


def _command(line: str, session: AgentSession, agent: Any) -> Optional[bool]:
    """Handle a /command. Returns False to exit the REPL."""
    from operonx.agents import TOOL_REGISTRY

    cmd = line.split()[0]
    if cmd in ("/quit", "/exit"):
        return False
    if cmd == "/help":
        print(HELP)
    elif cmd == "/tools":
        for name in sorted(TOOL_REGISTRY):
            meta = TOOL_REGISTRY[name]._tool_meta
            flag = (
                "destructive" if meta["destructive"] else ("readonly" if meta["readonly"] else "")
            )
            print(f"  {name:10} {_c(flag, '2;37'):<24} {meta['description'].splitlines()[0]}")
    elif cmd == "/messages":
        for m in session.messages:
            body = str(m.get("content", ""))[:200]
            print(f"  {_c(m.get('role', '?'), '1;36'):<12} {body}")
    elif cmd == "/cwd":
        print(f"  {agent.shell._cwd}")
    elif cmd == "/reset":
        session.reset()
        print("  conversation cleared")
    else:
        print(f"  unknown command {cmd} — /help for the list")
    return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="operonx-code",
        description="A coding agent built on operonx.agents.",
    )
    parser.add_argument("--root", default=".", help="Directory the agent may touch (default: .)")
    parser.add_argument(
        "--resource",
        default=os.environ.get("OPERONX_CODE_RESOURCE", "code"),
        help="ResourceHub key without the 'llm:' prefix (default: code)",
    )
    parser.add_argument(
        "--resources",
        default=os.environ.get("OPERONX_RESOURCES", "resources.yaml"),
        help="Path to resources.yaml",
    )
    parser.add_argument("--max-turns", type=int, default=40, help="Turn budget per exchange")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Pre-approve every destructive tool. Removes the only check before rm -rf.",
    )
    args = parser.parse_args(argv)

    from operonx.core.registry.resource_hub import ResourceHub

    if not Path(args.resources).exists():
        print(
            f"resources.yaml not found at {args.resources!r}. "
            f"Point --resources at one, or set OPERONX_RESOURCES.",
            file=sys.stderr,
        )
        return 2
    ResourceHub.set_instance(ResourceHub.from_yaml(args.resources))

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"--root {root} is not a directory", file=sys.stderr)
        return 2

    if args.yes:
        print(
            "--yes: every tool runs without asking, including bash and file writes.",
            file=sys.stderr,
        )

    return asyncio.run(
        run_repl(
            root=root,
            resource=args.resource,
            model_label=args.resource,
            yes_mode=args.yes,
            max_turns=args.max_turns,
        )
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
