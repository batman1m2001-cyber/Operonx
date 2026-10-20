"""The ops of ``discover_flow``: survey the repo, then run its commands once.

``survey`` is one read-only session (the surveyor): the brief every later
session is handed, and the repo's own commands to install, test, lint,
type-check and build. An empty repository is not surveyed: it becomes an
operonx project (card ``C0`` runs ``operonx init``), so its checks are
operonx's own.

``baseline`` runs the commands on the integration tip before any change:
a check already failing there never counts as a regression and is never
blamed on a card. A command that cannot be found or cannot run is dropped
from the checks and recorded as ``missing``, the first thing to tell the
owner. An operonx target always gets operonx's checks (plan §6.1): its
tests, ``operonx lint --build`` (the conventions, and every graph built
with the network blocked) and ``operonx guide --check``.

The project's practices (``docs/brd/practices.toml``, read on the integration
tip) add their checks: ``[done] checks`` (the Definition of Done) and, when
they declare architecture rules, ``architecture`` (``operonx-project arch``,
run with the engine's own Python). A project without the file gets the
checks it got before.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

from operonx import op

import operonx_project
from operonx_project.checks import Check, run_checks
from operonx_project.flows.stage import stage_call, stage_request
from operonx_project.practices import Practices, PracticesError, load
from operonx_project.prompts import OPERONX_RULE, SURVEY_SCHEMA, survey_prompt
from operonx_project.runtime import Runtime, get_runtime

__all__ = ["GREENFIELD_BRIEF", "baseline", "is_greenfield", "operonx_checks", "practice_checks", "survey"]

GREENFIELD_BRIEF = (
    "An empty repository: it becomes an operonx project. Card C0 runs `operonx init`, which writes the "
    "standard layout (app/main.py holds the Application; src/<feature>/graph.py and ops.py, one feature "
    "each; tests/; operonx.toml, resources.yaml, .env.example; AGENTS.md and .operonx/guide/)."
)

#: Files an empty repository may already hold: the BRD folder, a readme, a licence.
_SEED = ("docs/", "README", "LICENSE", ".gitignore", ".gitattributes", ".operonx/")
_MISSING = ("command not found", "No module named", "not recognized as", "No such file or directory")


def is_greenfield(repo: Path) -> bool:
    files = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True).stdout.split()
    return all(any(f.startswith(s) for s in _SEED) for f in files)


def operonx_checks(rt: Runtime) -> List[Check]:
    """pytest, ``operonx lint --build`` and ``operonx guide --check`` (plan §6.1)."""
    py = rt.target_python()
    ox = " ".join(shlex.quote(a) for a in rt.operonx_argv())
    t = rt.check_timeout_s
    return [
        Check("tests", f"{py} -m pytest -q -p no:cacheprovider", timeout_s=t, env={"COLUMNS": "1000"}),
        Check("lint", f"{ox} lint --build .", timeout_s=t),
        Check("guide", f"{ox} guide --check .", timeout_s=t),
    ]


def _merge(own: List[Check], extra: List[Check]) -> List[Check]:
    """The repo's own commands, then operonx's that it does not already run."""
    out = list(own)
    for c in extra:
        if any(x.name == c.name for x in out):
            continue
        if c.name == "tests" and any("pytest" in x.cmd for x in own):
            continue
        if c.name in ("lint", "guide") and any(f"operonx {c.name}" in x.cmd for x in own):
            continue
        out.append(c)
    return out


def practice_checks(p: Practices, rt: Runtime) -> List[Check]:
    """The checks the practices add: ``architecture``, then the ``[done]`` ones."""
    t = rt.check_timeout_s
    out: List[Check] = []
    if p.enforced or p.command:
        cmd = f"{shlex.quote(sys.executable)} -m operonx_project.cli arch --repo ."
        engine = str(Path(operonx_project.__file__).resolve().parents[1])  # the engine that runs is the one that checks
        out.append(Check("architecture", cmd, timeout_s=t, env={"COLUMNS": "1000", "PYTHONPATH": engine}))
    out += [Check(d["name"], d["cmd"], timeout_s=t, env={"COLUMNS": "1000"}) for d in p.done]
    return out


def _is_missing(r) -> bool:
    """The command never ran: not found, not executable, or its tool is not installed."""
    if r.ok or r.timed_out:
        return False
    if r.exit_code in (126, 127) or r.exit_code is None:
        return True
    return not r.failures and any(m in r.tail[-600:] for m in _MISSING)


@op
async def survey(key: str, mid: str) -> dict:
    rt = get_runtime(key)
    saved = rt.store.ms_get(mid, "survey")
    if saved is not None:
        return {"survey": saved}
    repo = Path(rt.git.repo)
    operonx = (repo / "operonx.toml").exists()
    if is_greenfield(repo):
        out: Dict[str, Any] = {"greenfield": True, "operonx": True, "brief": f"{GREENFIELD_BRIEF}\n{OPERONX_RULE}",
                               "stack": "operonx (to be created)", "commands": []}  # fmt: skip
    else:
        ms = rt.store.get_milestone(mid)
        req = stage_request(rt, mid, "planner", "surveyor", survey_prompt(ms.goal), repo, "survey",
                            schema=SURVEY_SCHEMA)  # fmt: skip
        res = await stage_call(rt, mid, "survey", req)
        if not res.ok or not isinstance(res.output, dict):
            raise RuntimeError(f"the survey ended {res.status}: {res.error or res.text[-300:]}")
        brief = str(res.output.get("brief") or "").strip()
        if operonx:
            brief = f"{brief}\n{OPERONX_RULE}"
        out = {"greenfield": False, "operonx": operonx, "brief": brief, "stack": res.output.get("stack", ""),
               "commands": list(res.output.get("commands") or [])}  # fmt: skip
    rt.store.ms_put(mid, "survey", out)
    return {"survey": out}


@op
async def baseline(key: str, mid: str, survey: dict) -> dict:
    rt = get_runtime(key)
    saved = rt.store.ms_get(mid, "baseline")
    if saved is not None:
        return {"baseline": saved}
    own = [
        Check(str(c["name"]), str(c["cmd"]), timeout_s=rt.check_timeout_s, env={"COLUMNS": "1000"})
        for c in survey.get("commands") or []
        if c.get("purpose") != "install" and c.get("cmd")
    ]
    checks = _merge(own, operonx_checks(rt)) if survey.get("operonx") else own
    integ = rt.git.ensure_integration(mid)
    try:
        practices = load(integ)
    except PracticesError as e:
        raise RuntimeError(f"the project's practices do not parse: {e}") from e
    for c in practice_checks(practices, rt):
        if any(x.name == c.name for x in checks):
            rt.store.note(mid, "discover.check_named_twice", {"check": c.name, "cmd": c.cmd})
            continue
        checks.append(c)
    results: List[Dict[str, Any]] = []
    missing: List[Dict[str, str]] = []
    if not survey.get("greenfield"):
        ran = await run_checks(checks, integ, rt.env)
        for c, r in zip(checks, ran):
            if _is_missing(r):
                missing.append({"name": c.name, "cmd": c.cmd, "tail": r.tail[-600:]})
            else:
                results.append(r.to_dict())
        gone = {m["name"] for m in missing}
        checks = [c for c in checks if c.name not in gone]
    out = {
        "greenfield": bool(survey.get("greenfield")),
        "operonx": bool(survey.get("operonx")),
        "sha": rt.git.head(integ),
        "checks": [asdict(c) for c in checks],
        "results": results,
        "missing": missing,
    }
    rt.store.ms_put(mid, "baseline", out)
    for m in missing:
        rt.store.note(mid, "discover.missing", {"check": m["name"], "cmd": m["cmd"]})
    return {"baseline": out}
