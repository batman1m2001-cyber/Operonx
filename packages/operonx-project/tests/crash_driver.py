"""Run or recover card C1 in this process, for the kill -9 tests.

``python -m tests.crash_driver <root> <scenario> run|recover``: the scripted
coder logs every call to ``<root>/calls.jsonl``; a step with ``die`` kills
this process with SIGKILL. The outcome goes to ``<root>/outcome.json``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from tests.flow_env import RIGHT, WRONG, Env, impl

SCENARIOS = {
    # killed while the implementer of round 3 is working
    "implement": {"implementer": [impl(WRONG + "# 1\n"), impl(WRONG + "# 2\n"),
                                  impl(WRONG + "# 3\n", die=True), impl(RIGHT)]},
    # killed while the reviewer of round 2 works: that round's implement had ended
    "review": {"implementer": [impl(WRONG + "# 1\n"), impl(RIGHT)],
               "reviewer": [{"die": True}, {"output": {"findings": []}}]},
}  # fmt: skip


async def main(root: Path, scenario: str, mode: str) -> None:
    repo = root / "proj" if (root / "proj").exists() else None
    env = Env(root, SCENARIOS[scenario], log=root / "calls.jsonl", repo=repo)
    card = env.card()
    if mode == "run":
        out = await env.runner.run(card, lambda s: None)
    else:
        out = await env.runner.recover(card, lambda s: None)
    (root / "outcome.json").write_text(json.dumps({"status": out.status, "reason": out.reason}))


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2], sys.argv[3]))
