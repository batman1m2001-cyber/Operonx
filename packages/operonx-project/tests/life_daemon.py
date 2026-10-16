"""A daemon in its own process, for the crash tests: ``python -m tests.life_daemon CONFIG``.

CONFIG (JSON): ``root``, ``script`` (JSON steps only), ``log``, ``mode``,
``policy`` (a Policy dict), ``commands`` (``[kind, payload, card]`` queued
before serving). The process sets ``LIFE_PID`` so a check command can kill
it at a chosen point (``tests/killer.py``).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from operonx_project.model import Policy
from tests.life_env import Life


def main(config: str) -> None:
    os.environ["LIFE_PID"] = str(os.getpid())
    c = json.loads(Path(config).read_text())
    root = Path(c["root"])
    repo = root / "proj"
    life = Life(root, c["script"], mode=c.get("mode", "w1"), policy=Policy.from_dict(c.get("policy")),
                repo=repo if repo.exists() else None, log=Path(c["log"]))  # fmt: skip
    for kind, payload, card in c.get("commands") or []:
        life.store.enqueue("m1", kind, payload or None, card=card)
    life.serve(lease_ttl=float(c.get("lease_ttl", 30.0)))


if __name__ == "__main__":
    main(sys.argv[1])
