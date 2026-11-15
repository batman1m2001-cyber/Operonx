"""Run the token A/B (ENGINEERING_ENV_PLAN E7): the same seven tasks, arm A on ``operonx init`` (guide,
AGENTS.md, knowledge.md, ``operonx-project``) and arm B on an empty folder, each task one fresh Claude
session, a hidden acceptance check after it and at most one fix turn.

    python run.py OUT --tracks A1,A2,B1,B2 --cap 40

A track is one arm run end to end (its tasks in order, in one workspace). Tracks run in parallel. The
run stops before a session when spent + the dearest session so far would pass ``--cap`` dollars.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import accept  # noqa: E402
import hard  # noqa: E402
from tasks import E7B, FIX, GROWTH, TASKS  # noqa: E402

OPERON = HERE.parent.parent
OPERONX = OPERON / ".venv" / "bin" / "operonx"
PROJECT_BIN = Path(os.environ.get("OPERONX_PROJECT_BIN", "/home/thanglq/operonx-studio/.venv/bin/operonx-project"))
DOCS = (".operonx/", "AGENTS.md", "CLAUDE.md", "docs/brd/", "README.md")
TIMEOUT = 25 * 60
# arm L (COST_PLAN round 3): A' with AGENTS.md step 3 made lean, verifying the way arm B does
LEAN_STEP3 = ("3. Verify by hand, the way you would without a framework: start `./run.sh`, call what you\n"
              "   changed (curl), run the job. Write a test only when the task asks for one.\n"
              "   `uv run operonx check` is there to explain a failure you cannot see; it is not a\n"
              "   step of every task.\n")


def claude_bin() -> str:
    found = shutil.which("claude") or sorted(glob.glob(
        "/root/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude"))[-1:]
    return found if isinstance(found, str) else found[0]


class Budget:
    def __init__(self, cap: float):
        self.cap, self.spent, self.dearest, self.lock = cap, 0.0, 0.0, threading.Lock()

    def may_start(self) -> bool:
        with self.lock:
            return self.spent + max(self.dearest, 0.5) <= self.cap

    def add(self, cost: float) -> None:
        with self.lock:
            self.spent += cost
            self.dearest = max(self.dearest, cost)


def setup(track: str, root: Path, bindir: Path) -> Path:
    parent = root / track
    shutil.rmtree(parent, ignore_errors=True)
    parent.mkdir(parents=True)
    ws = parent / "support"
    if track[0] in "AL":
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        subprocess.run([str(OPERONX), "init", "support", "--web", "--http", "--editable", str(OPERON)],
                       cwd=parent, env=env, check=True, capture_output=True, text=True)
    else:
        ws.mkdir()
    if track.startswith("L"):
        agents = (ws / "AGENTS.md").read_text()
        lean = re.sub(r"^3\. Verify with .*?(?=^\S|^$)", LEAN_STEP3, agents, count=1, flags=re.M | re.S)
        assert lean != agents, "AGENTS.md has no step 3 to make lean"
        (ws / "AGENTS.md").write_text(lean)
    shutil.copy(HERE / "SPEC.md", ws / "SPEC.md")
    git = ["git", "-c", "user.name=bench", "-c", "user.email=bench@example.invalid"]
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(git + ["commit", "-qm", "start"], cwd=ws, check=True)
    return ws


def session_env(track: str, ws: Path, bindir: Path, llm_port: int, port: int) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAUDE") and k not in ("AI_AGENT", "VIRTUAL_ENV", "PYTHONPATH")}
    path = [p for p in env.get("PATH", "").split(":") if "/.venv/" not in p]
    if track[0] in "AL":
        path.insert(0, str(bindir))  # operonx-project, which AGENTS.md tells the agent to use
    env.update(PATH=":".join(path), PORT=str(port), DATA_DIR=str(ws / ".data"),
               LLM_BASE_URL=f"http://127.0.0.1:{llm_port}/v1", LLM_MODEL="fake-model", LLM_API_KEY="test-key")
    return env


def run_session(prompt: str, ws: Path, env: dict, log: Path, resume: str = "") -> dict:
    cmd = [claude_bin(), "-p", prompt, "--model", "sonnet", "--output-format", "stream-json", "--verbose",
           "--permission-mode", "acceptEdits", "--allowedTools", "Bash", "Read", "Edit", "Write", "Glob", "Grep",
           "TodoWrite", "--disallowedTools", "WebFetch", "WebSearch", "--setting-sources", "project,local"]
    if resume:
        cmd += ["--resume", resume]
    # its own pid namespace: an agent's `pkill -f "operonx serve"` sees only its own processes, never
    # another track's server or a check's (round 3 found A' services killed by L sessions)
    cmd = ["unshare", "--pid", "--fork", "--mount-proc", "--kill-child"] + cmd
    t0 = time.time()
    with open(log, "w") as out:
        proc = subprocess.Popen(cmd, cwd=ws, env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            proc.wait(TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, 9)
            proc.wait()
    return read_log(log, time.time() - t0)


def read_log(log: Path, seconds: float) -> dict:
    """Cost, tokens and the doc tokens read (Read / Bash results on the arm's docs, chars / 4)."""
    res = {"cost": 0.0, "turns": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
           "doc_tokens": 0, "seconds": round(seconds), "session": "", "error": ""}
    docs_ids = {}
    for line in log.read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        res["session"] = ev.get("session_id") or res["session"]
        msg = ev.get("message") or {}
        for part in msg.get("content") or [] if isinstance(msg.get("content"), list) else []:
            if part.get("type") == "tool_use":
                arg = json.dumps(part.get("input") or {})
                if part.get("name") in ("Read", "Bash", "Grep") and any(d in arg for d in DOCS + ("operonx-project",)):
                    docs_ids[part["id"]] = True
            elif part.get("type") == "tool_result" and part.get("tool_use_id") in docs_ids:
                c = part.get("content")
                text = c if isinstance(c, str) else json.dumps(c)
                res["doc_tokens"] += len(text) // 4
        if ev.get("type") == "result":
            u = ev.get("usage") or {}
            # a session that waited on a background task ends more than once: the cost is cumulative,
            # the turns are per result
            res["turns"] += int(ev.get("num_turns") or 0)
            res.update(cost=float(ev.get("total_cost_usd") or 0),
                       input=u.get("input_tokens", 0), output=u.get("output_tokens", 0),
                       cache_read=u.get("cache_read_input_tokens", 0),
                       cache_write=u.get("cache_creation_input_tokens", 0),
                       error="" if ev.get("subtype") == "success" else str(ev.get("subtype")))
    if not res["turns"] and not res["error"]:
        res["error"] = "no result event (timeout or crash)"
    return res


def track(name: str, i: int, args, budget: Budget, results: list, lock: threading.Lock) -> None:
    out = Path(args.out)
    ws = Path(args.ws[name]) if name in args.ws else setup(name, out / "ws", out / "bin")
    b = args.port_base + 10 * i  # the fake model, the agent's service, the checks' service (+3: T14's hook)
    llm_port, port, accept_port = b, b + 1, b + 2
    fake = subprocess.Popen([sys.executable, str(HERE / "fake_llm.py"), str(llm_port)])
    env = session_env(name, ws, out / "bin", llm_port, port)
    try:
        order = [t for t, _ in TASKS + E7B + GROWTH]
        for task, prompt in TASKS + E7B + GROWTH:
            if task not in args.tasks if args.tasks else task in dict(E7B + GROWTH):
                continue
            if name in args.start and order.index(task) < order.index(args.start[name]):
                continue
            if not budget.may_start():
                with lock:
                    results.append({"track": name, "task": task, "skipped": "budget"})
                continue
            row = {"track": name, "arm": name[0], "task": task, **run_session(prompt, ws, env, out / f"{name}-{task}.jsonl")}
            budget.add(row["cost"])
            row["pass"], row["why"] = accept.check(task, ws, accept_port, llm_port)
            row["fixed"] = False
            if not row["pass"] and row["session"] and budget.may_start():
                fix = run_session(FIX.format(why=row["why"][:1500]), ws, env, out / f"{name}-{task}-fix.jsonl",
                                  resume=row["session"])
                budget.add(fix["cost"])
                for k in ("cost", "turns", "input", "output", "cache_read", "cache_write", "doc_tokens", "seconds"):
                    row[k] += fix[k]
                row["pass"], row["why"] = accept.check(task, ws, accept_port, llm_port)
                row["fixed"] = True
            subprocess.run(["git", "add", "-A"], cwd=ws)
            subprocess.run(["git", "-c", "user.name=bench", "-c", "user.email=bench@example.invalid",
                            "commit", "-qm", task, "--allow-empty"], cwd=ws)
            with lock:
                results.append(row)
                with open(out / "results.jsonl", "a") as f:
                    f.write(json.dumps(row) + "\n")
                print(f"{name} {task} pass={row['pass']} fixed={row['fixed']} ${row['cost']:.2f} "
                      f"turns={row['turns']} docs={row['doc_tokens']} spent=${budget.spent:.2f} {row['why'][:120]}",
                      flush=True)
        if args.hard:  # the hidden robustness probes and every earlier task, once the track is done
            upto = [t for t in order if (t in args.tasks if args.tasks else t not in dict(E7B + GROWTH))][-1]
            for row in hard.probe_all(ws, accept_port, llm_port, upto):
                with lock, open(out / "hard.jsonl", "a") as f:
                    f.write(json.dumps({"track": name, **row}) + "\n")
    finally:
        fake.terminate()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--tracks", default="A1,A2,B1,B2")
    ap.add_argument("--tasks", default="")
    ap.add_argument("--cap", type=float, default=40.0)
    ap.add_argument("--ws", default="", help="TRACK=DIR,...: go on in a workspace an earlier run left (gate 2)")
    ap.add_argument("--start", default="", help="TRACK=TASK,...: with --ws, go on from TASK (a run that was cut off)")
    ap.add_argument("--port-base", type=int, default=8870, help="track i uses BASE+10i .. BASE+10i+3")
    ap.add_argument("--hard", action="store_true", help="run hard.py's probes on each track when it is done")
    args = ap.parse_args()
    args.ws = dict(p.split("=", 1) for p in args.ws.split(",") if p)
    args.start = dict(p.split("=", 1) for p in args.start.split(",") if p)
    args.tasks = [t for t in args.tasks.split(",") if t]
    out = Path(args.out)
    (out / "bin").mkdir(parents=True, exist_ok=True)
    link = out / "bin" / "operonx-project"
    if not link.exists():
        link.symlink_to(PROJECT_BIN)
    budget, results, lock = Budget(args.cap), [], threading.Lock()
    threads = [threading.Thread(target=track, args=(n, i, args, budget, results, lock))
               for i, n in enumerate(args.tracks.split(","))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    (out / "summary.json").write_text(json.dumps({"spent": budget.spent, "rows": results}, indent=1))
    print(f"spent ${budget.spent:.2f}")


if __name__ == "__main__":
    main()
