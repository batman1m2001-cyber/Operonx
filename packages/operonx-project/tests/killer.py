"""A check command that kills the daemon once (``LIFE_PID``): ``killer.py MARKER [merge|card]``.

It runs as part of the checks. ``merge`` (the default): in the integration
worktree, when HEAD is a card's merge — a crash while merging (the merge
committed, its checks running). ``card``: in a card's worktree — a crash
while a round's checks run. Once: it writes the marker first.
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

marker = Path(sys.argv[1])
mode = sys.argv[2] if len(sys.argv) > 2 else "merge"
here = Path.cwd()
head = subprocess.run(["git", "log", "-1", "--format=%s"], capture_output=True, text=True).stdout
if mode == "merge":
    hit = here.name == "_integration" and ": merge card " in head
else:
    hit = here.name.startswith("C")
if hit and not marker.exists():
    marker.write_text(head)
    os.kill(int(os.environ["LIFE_PID"]), signal.SIGKILL)
