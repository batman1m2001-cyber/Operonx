"""The owner's standing rules, and the project's, in every coder session (plan D16).

A daemon's coder works in a worktree where no editor memory is loaded: it
would not know the rules the owner gave in editor sessions (no co-author
trailer, pull before a commit, a checkout never to touch). Two files carry
them:

- ``~/.operonx/owner.md`` (or ``OPERONX_OWNER_RULES``): the owner's rules for
  every project;
- the project's ``AGENTS.md``, from the session's working directory.

:func:`standing_rules` is appended to every session's system prompt
(``--append-system-prompt``), and the reviewer checks a diff against them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

__all__ = ["LIMIT", "owner_rules_path", "standing_rules"]

#: Each file is cut at this many characters: rules, not a manual.
LIMIT = 16_000


def owner_rules_path() -> Path:
    return Path(os.environ.get("OPERONX_OWNER_RULES") or "~/.operonx/owner.md").expanduser()


def _read(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""
    return text if len(text) <= LIMIT else text[:LIMIT] + "\n… (cut)"


def standing_rules(cwd: Optional[Path]) -> str:
    """The text appended to a session's system prompt: the owner's rules, then
    the project's AGENTS.md. Empty when there are neither."""
    parts = []
    owner = _read(owner_rules_path())
    if owner:
        parts.append("# The owner's standing rules (every project; ~/.operonx/owner.md)\n\n" + owner)
    agents = _read(Path(cwd) / "AGENTS.md") if cwd else ""
    if agents:
        parts.append("# This project's rules (its AGENTS.md)\n\n" + agents)
    if not parts:
        return ""
    return (
        "Follow these rules as the owner's own instructions. Where they conflict with the engine's "
        "instructions for this session (its paths, its tools, no push), the engine's win.\n\n" + "\n\n".join(parts)
    )
