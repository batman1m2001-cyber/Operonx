"""A check command that kills the daemon once, right after a merge (``LIFE_PID``).

It runs as part of the checks; in the integration worktree, when HEAD is a
card's merge and the marker is absent, it writes the marker and SIGKILLs the
daemon: a crash while merging (the merge committed, its checks running).
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

marker = Path(sys.argv[1])
here = Path.cwd()
head = subprocess.run(["git", "log", "-1", "--format=%s"], capture_output=True, text=True).stdout
if here.name == "_integration" and ": merge card " in head and not marker.exists():
    marker.write_text(head)
    os.kill(int(os.environ["LIFE_PID"]), signal.SIGKILL)
