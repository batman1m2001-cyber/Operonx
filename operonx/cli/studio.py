"""`operonx studio` — open the project you are in, in operonx-studio.

    operonx studio                  the project at or above here
    operonx studio DIR              the project at or above DIR
    operonx studio --port 9000      a studio on another port
    operonx studio --no-open        do not open a browser

It starts the studio when none is running on the port, adds the project
to its list and opens it in the browser. A studio already running there
is handed the project instead, so the command returns at once.

operonx-studio is a separate tool, installed from its repository
(``git clone … && operonx-studio/install.sh``); this
command only finds it and hands it the project. operonx never imports it.
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

__all__ = ["INSTALL", "main", "studio_command"]

#: How to get the studio, said wherever it is missing.
INSTALL = (
    "git clone https://github.com/batman1m2001-cyber/operonx-studio && operonx-studio/install.sh"
)


def studio_command() -> Optional[List[str]]:
    """How to run operonx-studio here: its command on PATH, else the module
    in this interpreter; ``None`` when it is not installed."""
    exe = shutil.which("operonx-studio")
    if exe:
        return [exe]
    if importlib.util.find_spec("operonx_studio") is not None:
        return [sys.executable, "-m", "operonx_studio.cli"]
    return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    from operonx.guide import find_project

    parser = argparse.ArgumentParser(
        prog="operonx studio",
        description="Open the project in operonx-studio: start the studio if none is "
        "running, add the project to its list, and show it.",
    )
    parser.add_argument(
        "dir", nargs="?", default=None, help="the project (default: the one at or above here)"
    )
    parser.add_argument("--port", type=int, default=None, help="the studio's port (default 8765)")
    parser.add_argument("--host", default=None, help="the studio's host (default 127.0.0.1)")
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = parser.parse_args(list(argv) if argv is not None else None)

    start = Path(args.dir) if args.dir else None
    root = find_project(start)
    if root is None:
        where = (start or Path.cwd()).resolve()
        print(
            f"operonx studio: no operonx.toml in {where} or above it "
            "(`operonx init` makes a project)",
            file=sys.stderr,
        )
        return 2
    cmd = studio_command()
    if cmd is None:
        print(
            f"operonx studio: operonx-studio is not installed. Install it once:\n  {INSTALL}",
            file=sys.stderr,
        )
        return 2
    cmd = [*cmd, str(root)]
    if args.port is not None:
        cmd += ["--port", str(args.port)]
    if args.host is not None:
        cmd += ["--host", args.host]
    if args.no_open:
        cmd.append("--no-open")
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:  # Ctrl+C stops the studio it started
        return 130
