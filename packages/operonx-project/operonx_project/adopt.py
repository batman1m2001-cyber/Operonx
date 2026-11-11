"""``operonx-project adopt``: an existing project gets its BRD (plan D12).

A project built by hand has requirements in its README, its docs and its
tests, and work already done. ``adopt`` writes them down the engine's way:

1. ``docs/brd/`` from the template, where it is missing (nothing is
   overwritten);
2. one read-only session (Claude Code, the planner's tools: read, grep,
   glob) answers what the product must do, what is built (with its code and
   the tests that show it) and what is still open (a backlog, a plan). It
   writes nothing; the engine writes the files from its answer;
3. **the engine runs every cited test.** A row is ``implemented_verified``
   only when each test it cites exists and passed in this run; otherwise
   built work is ``implemented_unverified``. The model never sets a status;
4. open work becomes rows ``planned`` (or ``not_implemented``): the board's
   cards (``docs/brd/board.md``) are written when someone works them.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from operonx_project.brd.folder import init_brd, refresh_summary
from operonx_project.brd.records import Requirement, add_matrix_rows, set_matrix_row, write_requirements
from operonx_project.coder.base import Coder, CoderRequest
from operonx_project.project import BRD_DIR, Project

__all__ = ["ADOPT_SCHEMA", "adopt", "adopt_prompt", "main_report", "run_tests"]

_TYPES = ("business", "functional", "non_functional", "rule", "kpi")
_STATES = ("built", "partial", "open")
_NODE = re.compile(r"^tests/[\w./-]+\.py::\w+(::\w+)?$")

ADOPT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["summary", "requirements"],
    "properties": {
        "summary": {"type": "string", "description": "the product in two sentences"},
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "type", "quote", "location", "state"],
                "properties": {
                    "id": {"type": "string", "description": "<PREFIX>-REQ-<AREA>-<nn>, e.g. QC-REQ-SCORE-01"},
                    "type": {"enum": list(_TYPES)},
                    "quote": {"type": "string", "description": "the project's own words, from its docs"},
                    "location": {"type": "string", "description": "where the quote is: a file path and its section"},
                    "reading": {"type": "string", "description": "what it means, in one sentence"},
                    "state": {"enum": list(_STATES)},
                    "code": {"type": "array", "items": {"type": "string"}, "description": "files that meet it"},
                    "tests": {"type": "array", "items": {"type": "string"},
                              "description": "tests that show it, as tests/<path>.py::<test> (or ::<Class>::<test>)"},
                    "gap": {"type": "string", "description": "for partial or open: what is missing"},
                    "planned": {"type": "boolean", "description": "open, and a backlog or plan names it"},
                },
            },
        },
    },
}  # fmt: skip


def adopt_prompt(name: str, prefix: str) -> str:
    return f"""You are documenting an existing software project, {name}, so that its requirements and its \
progress can be tracked. Read it; change nothing.

Read, in this order: the README and any AGENTS.md / CLAUDE.md; the docs (docs/, plans, a BACKLOG or TODO file); \
the application's entry points; the tests (tests/). Then answer with:

- requirements: what the product must do, each in the project's own words (`quote`, copied from a doc, a README, \
a docstring or a test's name), with `location` (the file and its section). Ids are {prefix}-REQ-<AREA>-<nn>, \
AREA a short upper-case word, nn from 01. One requirement is one behaviour a test could check. Aim for the \
requirements a new maintainer must know, usually 10–40.
  - state `built` when code meets it: list the files (`code`) and the tests that show it (`tests`, as \
`tests/<path>.py::<test_name>` or `tests/<path>.py::<Class>::<test_name>`, only tests that exist);
  - `partial` when part is built (`gap` says what is missing);
  - `open` when nothing is built (`gap`; `planned` true when a backlog or plan names it).

Never invent a test, a file or a requirement no doc or code supports. A status is not yours to decide: the \
engine runs every test you cite, and only a test that passes makes a requirement verified."""


def _python(repo: Path) -> List[str]:
    for root in (repo, *repo.parents):
        py = root / ".venv" / "bin" / "python"
        if py.is_file():
            return [str(py)]
        if (root / ".git").exists():
            break
    return [sys.executable]


def run_tests(repo: Path, nodes: Sequence[str], python: Optional[List[str]] = None,
              timeout_s: float = 1800.0) -> Dict[str, bool]:  # fmt: skip
    """Run *nodes* once (pytest node ids) in *repo*; each one's pass or fail.
    A node pytest did not report (missing, not collected) is a fail."""
    nodes = sorted({n for n in nodes if _NODE.match(n)})
    if not nodes:
        return {}
    py = [*(python or _python(Path(repo))), "-m", "pytest", "-p", "no:cacheprovider"]

    def run(args: List[str]) -> str:
        try:
            got = subprocess.run([*py, *args], cwd=repo, capture_output=True, text=True, timeout=timeout_s)
            return got.stdout + got.stderr
        except subprocess.TimeoutExpired as exc:
            return exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")

    # a node that does not exist stops pytest before it runs anything: collect first, run what exists
    files = sorted({n.split("::", 1)[0] for n in nodes if (Path(repo) / n.split("::", 1)[0]).is_file()})
    collected = set(re.findall(r"^(tests/\S+::\S+)$", run(["--collect-only", "-q", *files]), re.M)) if files else set()
    have = {re.sub(r"\[.*\]$", "", c) for c in collected}
    run_now = [n for n in nodes if n in have]
    out = run(["-q", "-rA", *run_now]) if run_now else ""
    passed = {re.sub(r"\[.*\]$", "", n) for n in re.findall(r"^PASSED (\S+)", out, re.M)}
    # a parametrized test passes when every case passed: "tests/x.py::test_y[a]"
    failed = {re.sub(r"\[.*\]$", "", n) for n in re.findall(r"^(?:FAILED|ERROR) (\S+)", out, re.M)}
    return {n: n in passed and n not in failed for n in nodes}


def _status(r: Dict[str, Any], results: Dict[str, bool]) -> str:
    state = r.get("state")
    tests = [t for t in r.get("tests") or [] if _NODE.match(t)]
    if state == "built":
        return "implemented_verified" if tests and all(results.get(t) for t in tests) else "implemented_unverified"
    if state == "partial":
        return "partially_implemented"
    return "planned" if r.get("planned") else "not_implemented"


async def adopt(project: Project, coder: Coder, prefix: str = "", python: Optional[List[str]] = None,
                answer: Optional[Dict[str, Any]] = None, max_usd: float = 2.0, model: Optional[str] = None
                ) -> Dict[str, Any]:  # fmt: skip
    """Adopt *project*: its BRD. *answer* skips the
    session (a recorded answer, a test). Returns a report."""
    repo, brd = project.repo, project.repo / BRD_DIR
    name = project.git.project
    prefix = prefix or (re.sub(r"[^A-Z0-9]", "", name.upper())[:8] or "REQ")
    written = init_brd(repo, BRD_DIR, project=name)
    cost = 0.0
    if answer is None:
        if not model:  # the surveyor's model: the project's settings, else the engine's default (sonnet)
            from operonx_project.project import load_settings
            from operonx_project.runtime import DEFAULT_MODELS

            got = load_settings(project.state)
            model = (got.get("models") or {}).get("surveyor") or got.get("model") or DEFAULT_MODELS["surveyor"]
        d = project.state / "transcripts" / "adopt"
        d.mkdir(parents=True, exist_ok=True)
        req = CoderRequest(prompt=adopt_prompt(name, prefix), cwd=repo, role="planner", schema=ADOPT_SCHEMA,
                           model=model, max_usd=max_usd, timeout_s=1800.0, transcript=d / "adopt.jsonl",
                           sandbox=False, tags={"step": "adopt"})  # fmt: skip
        res = await coder.run(req)
        cost = res.cost_usd
        if not res.ok or not isinstance(res.output, dict):
            raise RuntimeError(f"the adoption session ended {res.status}: {res.error or res.text[-500:]}")
        answer = res.output
        (project.state / "adopt.json").write_text(json.dumps(answer, indent=1, ensure_ascii=False), encoding="utf-8")
    reqs = [r for r in answer.get("requirements") or [] if r.get("id") and r.get("quote")]
    results = run_tests(repo, [t for r in reqs for t in r.get("tests") or []], python=python)

    today = _dt.date.today().isoformat()
    records = [Requirement(id=r["id"], type=r.get("type") if r.get("type") in _TYPES else "functional",
                           quote=r["quote"], location=r.get("location") or "", reading=r.get("reading") or "")
               for r in reqs]  # fmt: skip
    write_requirements(brd, "adopted", f"{name}: requirements found in the project", "the project's own docs, "
                       f"code and tests (`operonx-project adopt`, {today})", records)  # fmt: skip
    add_matrix_rows(brd, records, "Adopted")
    counts: Dict[str, int] = {}
    for r in reqs:
        status = _status(r, results)
        counts[status] = counts.get(status, 0) + 1
        ran = [t for t in r.get("tests") or [] if t in results]
        if status == "implemented_verified":
            evidence = f"adopt {today}: {len(ran)} cited test(s) passed"
        elif ran:
            # by name only: a missing test's path would be cited as a test (brd check)
            evidence = f"adopt {today}: failed or not found: " + ", ".join(t.rsplit("::", 1)[-1] for t in ran
                                                                           if not results[t])  # fmt: skip
        else:
            evidence = r.get("gap") or (f"adopt {today}: no test shows it" if r.get("state") == "built" else "")
        set_matrix_row(brd, r["id"], status, tests=[t for t in ran if results[t]], code=", ".join(r.get("code") or []),
                       evidence=evidence)  # fmt: skip
    refresh_summary(brd)

    log = brd / "CHANGELOG.md"
    line = (f"- {today}: adopted (`operonx-project adopt`): {len(reqs)} requirements "
            f"({', '.join(f'{v} {k}' for k, v in sorted(counts.items()))}).")  # fmt: skip
    log.write_text(log.read_text(encoding="utf-8").rstrip("\n") + "\n" + line + "\n", encoding="utf-8")
    return {"requirements": len(reqs), "status": counts, "tests": results,
            "brd_files": [str(p) for p in written], "cost_usd": cost, "summary": answer.get("summary", "")}  # fmt: skip


def main_report(report: Dict[str, Any]) -> str:
    ran = report["tests"]
    return (f"adopted: {report['requirements']} requirements {json.dumps(report['status'])}, "
            f"tests run {len(ran)}, passed {sum(ran.values())}; "
            f"${report['cost_usd']:.2f}")  # fmt: skip
