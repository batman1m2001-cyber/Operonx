"""The ``operonx-run``, ``operonx-serve`` and ``operonx-play`` scripts,
kept for one release as aliases of ``operonx run`` / ``serve`` / ``play``.
(``operonx-pack`` went with ``operonx pack`` and the Rust runtime.)

Each warns once on stderr and then calls the very ``main`` the subcommand
calls, with the same arguments, so the two forms cannot drift apart.
Deployed projects and Dockerfiles call these names; they are removed in
the release after the one that deprecated them.
"""

from __future__ import annotations

import importlib
import sys
from typing import Callable

__all__ = ["run", "serve", "play"]


def _alias(command: str) -> Callable[[], int]:
    def main() -> int:
        from operonx.cli.main import DELEGATED

        print(
            f"DeprecationWarning: `operonx-{command}` is deprecated and will be removed "
            f"in the next release; use `operonx {command}`",
            file=sys.stderr,
        )
        return importlib.import_module(DELEGATED[command][0]).main(sys.argv[1:])

    main.__name__ = command
    main.__doc__ = f"`operonx-{command}`: warns, then runs `operonx {command}`."
    return main


run = _alias("run")
serve = _alias("serve")
play = _alias("play")
