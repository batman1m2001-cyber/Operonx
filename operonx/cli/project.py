"""`operonx project` — operonx-project's commands: a project's requirements and where it stands.

    operonx project new DIR                    # a repo with docs/brd/ and an empty board
    operonx project status | plans | board [CARD STATE]
    operonx project brd init | check | register | read | extract
    operonx project adopt                      # an existing project: its BRD and a baseline board

operonx-project is its own package (``packages/operonx-project`` in the
operonx repository); this command only finds it and hands it the rest of
the command line, as ``operonx studio`` does the studio. operonx never
imports it otherwise.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence

__all__ = ["INSTALL", "main"]

#: How to get operonx-project, said wherever it is missing.
INSTALL = "pip install -e packages/operonx-project  (from an operonx checkout), or uv pip install the same"


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        from operonx_project.cli import main as run
    except ImportError:
        print(
            f"operonx project needs operonx-project, which is not installed here: {INSTALL}",
            file=sys.stderr,
        )
        return 2
    return run(list(sys.argv[1:] if argv is None else argv))
