"""A daemon in its own process, for the crash tests: ``python -m tests.life_daemon CONFIG``.

CONFIG (JSON): ``root``, ``script`` (JSON steps only), ``log``, ``mode``,
``policy`` (a Policy dict), ``commands`` (``[kind, payload, card]`` queued
before serving), ``fake_claude`` (a folder: the implementer runs through the
real ClaudeCodeCoder against ``tests/fake_claude.py`` scripted there; every
other role stays scripted). The process sets ``LIFE_PID`` so a check
command can kill it at a chosen point (``tests/killer.py``).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from operonx_project.model import Policy
from tests.life_env import Life


class Hybrid:
    """The implementer through ClaudeCodeCoder and a fake ``claude`` binary; the rest scripted."""

    def __init__(self, scripted, folder: Path):
        from operonx_project.coder import ClaudeCodeCoder
        from operonx_project.coder.claude import base_env

        self.scripted = scripted
        self.real = ClaudeCodeCoder(binary=str(folder / "claude"), env={
            **base_env(), "FAKE_CLAUDE_LOG": str(folder / "calls.jsonl"),
            "FAKE_CLAUDE_SCENARIOS": str(folder / "scenarios.json")})  # fmt: skip

    async def run(self, req, on_start=None):
        return await (self.real if req.role == "implementer" else self.scripted).run(req, on_start)

    def __getattr__(self, name):
        return getattr(self.scripted, name)


def main(config: str) -> None:
    os.environ["LIFE_PID"] = str(os.getpid())
    c = json.loads(Path(config).read_text())
    root = Path(c["root"])
    repo = root / "proj"
    life = Life(root, c["script"], mode=c.get("mode", "w1"), policy=Policy.from_dict(c.get("policy")),
                repo=repo if repo.exists() else None, log=Path(c["log"]))  # fmt: skip
    if c.get("post_merge"):  # a command git runs right after each merge commit (a crash while merging)
        hook = Path(life.repo) / ".git" / "hooks" / "post-merge"
        hook.write_text(f"#!/bin/sh\n{c['post_merge']}\n")
        hook.chmod(0o755)
    if c.get("fake_claude"):
        life.life.coder = life.coder = Hybrid(life.coder, Path(c["fake_claude"]))
    for kind, payload, card in c.get("commands") or []:
        life.store.enqueue("m1", kind, payload or None, card=card)
    life.serve(lease_ttl=float(c.get("lease_ttl", 30.0)))


if __name__ == "__main__":
    main(sys.argv[1])
