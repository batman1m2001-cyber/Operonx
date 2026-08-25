"""Whether this machine can actually satisfy a project's env contract.

Deliberately kept out of the Project IR. The IR is a pure function of the
source and is gated on being byte-identical across runs; folding in machine
state would make two extractions of the same commit disagree on different
boxes. This is computed at render time and injected instead.

**Only names and presence are reported — never a value.** The page is a file
that gets shared; a rendered secret is a leaked secret.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Iterable, Set

__all__ = ["env_status", "dotenv_names"]

_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def dotenv_names(path: Path) -> Set[str]:
    """Variable names assigned in a ``.env`` file. Values are never read."""
    if not path.exists():
        return set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    found = set()
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _ASSIGNMENT.match(line)
        if match:
            found.add(match.group(1))
    return found


def env_status(root: Path, required: Iterable[str], optional: Iterable[str]) -> Dict[str, dict]:
    """Presence of each variable, and where it would come from.

    ``.env`` is reported separately from the live environment because the two
    fail differently: a name in ``.env`` but not exported is fine for a
    project that loads it at startup and useless for one that does not.
    """
    from_file = dotenv_names(root / ".env")
    example = dotenv_names(root / ".env.example")
    out: Dict[str, dict] = {}
    for name in list(required) + list(optional):
        in_env = name in os.environ
        in_file = name in from_file
        out[name] = {
            "set": in_env or in_file,
            "in_environment": in_env,
            "in_dotenv": in_file,
            "in_example": name in example,
        }
    return out
