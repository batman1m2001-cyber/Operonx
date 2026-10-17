"""A scripted stand-in for the ``claude`` binary (a test double).

It speaks the stream-json shapes captured from Claude Code 2.1.301 in the P2
paid smoke (init, assistant tool_use, user tool_result, rate_limit_event,
result with structured_output / permission_denials / error_max_budget_usd).

Driven by environment variables:

- ``FAKE_CLAUDE_LOG``: each call appends ``{"argv", "cwd", "stdin", "env"}``;
- ``FAKE_CLAUDE_SCENARIOS``: a JSON file holding a list of scenarios, one
  consumed per call (the last one repeats). A scenario:
  ``{"events": [...], "stderr": "", "exit": 0, "write": {path: text},
  "hang": seconds, "child": true}``. In events, ``"$SESSION"`` is the
  session id the call runs under.
"""

import json
import os
import subprocess
import sys
import time
import uuid


def main() -> int:
    argv = sys.argv[1:]
    stdin = sys.stdin.read()
    log = os.environ.get("FAKE_CLAUDE_LOG")
    if log:
        with open(log, "a") as fh:
            keys = sorted(k for k in os.environ if k.startswith(("CLAUDE", "ANTHROPIC", "VIRTUAL_ENV")))
            fh.write(json.dumps({"argv": argv, "cwd": os.getcwd(), "stdin": stdin, "env": keys}) + "\n")
    with open(os.environ["FAKE_CLAUDE_SCENARIOS"]) as fh:
        scenarios = json.load(fh)
    counter = os.environ["FAKE_CLAUDE_SCENARIOS"] + ".n"
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(n + 1))
    sc = scenarios[min(n, len(scenarios) - 1)]

    session = None
    if "--session-id" in argv:
        session = argv[argv.index("--session-id") + 1]
    if "--resume" in argv:
        session = argv[argv.index("--resume") + 1]
        if "--fork-session" in argv:
            session = str(uuid.uuid4())
    session = session or str(uuid.uuid4())

    for path, text in (sc.get("write") or {}).items():
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
    if sc.get("child"):
        # a grandchild in the same process group: a stop must reach it too
        p = subprocess.Popen(["sleep", "300"])
        with open(os.environ["FAKE_CLAUDE_SCENARIOS"] + ".child", "w") as fh:
            fh.write(str(p.pid))
    if sc.get("stderr"):
        sys.stderr.write(sc["stderr"] + "\n")
        sys.stderr.flush()
    for ev in sc.get("events") or []:
        # "raw:…" is written as is (a line that is not JSON)
        line = ev[4:] if isinstance(ev, str) and ev.startswith("raw:") else json.dumps(ev).replace("$SESSION", session)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    if sc.get("hang"):
        time.sleep(float(sc["hang"]))
    return int(sc.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
