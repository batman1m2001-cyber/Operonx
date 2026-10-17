"""``operonx-project`` (and ``operonx project``): the commands arrive in P5."""

from __future__ import annotations

import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    from operonx_project import __version__

    args = sys.argv[1:] if argv is None else argv
    if args in ([], ["--version"]):
        sys.stdout.write(f"operonx-project {__version__}\n")
        return 0
    sys.stderr.write("operonx-project: no commands yet (P5 adds them)\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
