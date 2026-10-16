"""A project driven end to end by the lifecycle, with a scripted coder.

The repo is flow_env's calc project (``add`` subtracts). The script plays
every session: the survey, the plan and its review, the cards' test
author, examiner, implementer and reviewer, the milestone review, and the
BRD extraction when the milestone has a source.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from operonx_project.daemon import Daemon
from operonx_project.lifecycle import Lifecycle
from operonx_project.model import MilestoneState, Policy
from operonx_project.project import Project, create_milestone
from tests.flow_env import PY, RIGHT, author_step, examiner_step, impl, make_repo
from tests.scripted_coder import ScriptedCoder

TESTS = f"{PY} -m pytest -q -p no:cacheprovider tests"
SURVEY = {"output": {"brief": "calc: one module, calc.py; tests in tests/", "stack": "python",
                     "commands": [{"name": "tests", "cmd": TESTS, "purpose": "test"}]}}  # fmt: skip


def card_json(cid: str, row: str = "", **kw: Any) -> Dict[str, Any]:
    base = {
        "id": cid,
        "title": "add numbers",
        "intent": "calc.add adds two numbers",
        "rows": [row] if row else [],
        "criteria": [{"row": row, "text": "WHEN add(a, b) is called THE SYSTEM SHALL return a + b"}],
        "deps": [],
        "paths": ["calc.py"],
        "kind": "code",
        "risk": "low",
    }
    base.update(kw)
    return base


def plan(cards: List[Dict[str, Any]], epics: Optional[List[Dict[str, Any]]] = None, init=None) -> Dict[str, Any]:
    return {"output": {"architecture": "one module", "epics": epics or [], "cards": cards, "init": init}}


def base_script(**over: Any) -> Dict[str, List[Any]]:
    script: Dict[str, List[Any]] = {
        "planner:survey": [SURVEY],
        "planner:plan": [plan([card_json("C1")])],
        "test_author": [author_step("C1")],
        "examiner": [examiner_step(None)],
        "implementer": [impl(RIGHT)],
    }
    script.update(over)
    return script


class Life:
    def __init__(self, root: Path, script: Dict[str, List[Any]], mode: str = "w1", policy: Optional[Policy] = None,
                 sources=(), repo: Optional[Path] = None, log: Optional[Path] = None, goal: str = "calc.add adds",
                 prefix: str = "CALC"):  # fmt: skip
        self.root = root
        self.repo = repo or make_repo(root)
        self.coder = ScriptedCoder(script, log=log)
        self.project = Project.open(self.repo, worktrees=root / "worktrees")
        self.store = self.project.store
        if not self.store.milestones():
            create_milestone(self.project, "calc", goal=goal, mode=mode, policy=policy, sources=sources,
                             prefix=prefix, mid="m1")  # fmt: skip
        self.life = Lifecycle(
            self.project,
            self.coder,
            sandbox=False,
            heldout_root=root / "heldout",
            runtime={
                "acceptance_cmd": f"{PY} -m pytest -q -p no:cacheprovider {{dir}}",
                "heldout_cmd": f"{PY} -m pytest -q --tb=no -rfE --assert=plain -p no:cacheprovider {{dir}}",
                "check_timeout_s": 120.0,
            },
        )

    def command(self, kind: str, **kw: Any) -> int:
        card = kw.pop("card", None)
        return self.store.enqueue("m1", kind, kw or None, card=card)

    def serve(self, lease_ttl: float = 30.0) -> MilestoneState:
        asyncio.run(Daemon(self.life, poll_s=0.02, lease_ttl=lease_ttl).serve(once=True))
        return self.state()

    def state(self) -> MilestoneState:
        return self.store.get_milestone("m1").state

    def cards(self) -> Dict[str, str]:
        return {c.id: c.state.value for c in self.store.cards("m1")}

    def integ(self) -> Path:
        return self.project.git.integration_path("m1")


CALC_BRD = (
    "# Calculator\n\n## 1. Adding\n\nThe calculator must add two numbers.\n\n"
    "## 2. Doubling\n\nThe calculator must double a number.\n"
)
ADD_ROW, DOUBLE_ROW = "CALC-REQ-MATH-01", "CALC-REQ-MATH-02"
DOUBLE = RIGHT + "\n\ndef double(x):\n    return 2 * x\n"
DOUBLE_TEST = "from calc import double\n\n\ndef test_double():\n    assert double(4) == 8\n"


def extraction() -> Dict[str, Any]:
    def rec(rid, section, quote):
        return {"id": rid, "type": "functional", "section": section, "quote": quote, "reading": quote}

    return {"output": {"requirements": [rec(ADD_ROW, "§1", "The calculator must add two numbers."),
                                        rec(DOUBLE_ROW, "§2", "The calculator must double a number.")],
                       "descriptive": [{"section": "S01", "reason": "the title"}]}}  # fmt: skip


def double_author() -> Dict[str, Any]:
    path = "tests/acceptance/C2/test_c2_double.py"
    return {"write": {path: DOUBLE_TEST}, "output": {"files": [path]}}


def double_card() -> Dict[str, Any]:
    return card_json("C2", DOUBLE_ROW, title="double a number", intent="calc.double doubles", deps=["C1"],
                     criteria=[{"row": DOUBLE_ROW, "text": "WHEN double(x) is called THE SYSTEM SHALL return 2x"}],
                     epic="E1")  # fmt: skip


def w2_script(**over: Any) -> Dict[str, List[Any]]:
    epics = [{"id": "E1", "title": "arithmetic", "rows": [ADD_ROW, DOUBLE_ROW], "deps": []}]
    return base_script(
        **{
            "planner:extractor": [extraction()],
            "reviewer:hunter": [{"output": {"requirements": []}}],
            "planner:plan": [plan([card_json("C1", ADD_ROW, epic="E1")], epics=epics)],
            "planner:replenish": [{"output": {"cards": [double_card()]}}],
            "test_author": [author_step("C1"), double_author()],
            "implementer": [impl(RIGHT), impl(DOUBLE)],
            **over,
        }
    )


def write_source(root: Path) -> Path:
    f = root / "calc_brd.md"
    f.write_text(CALC_BRD)
    return f
