"""The board in the BRD (plan D1–D6): ``docs/brd/board/``.

Store → files → store is the contract: whatever a milestone and its cards
hold that a rebuild needs comes back equal. A lost store is rebuilt from
the files; a milestone that was moving comes back paused, a card that was
mid-flight comes back ready. ``start --from-board`` seeds a new milestone
with an old one's cards as its plan.
"""

from __future__ import annotations

import subprocess

from operonx_project.brd.board import read_board, write_board
from operonx_project.model import Card, CardState, Milestone, MilestoneState, Policy, Stage
from operonx_project.project import Project, board_seed, create_milestone
from operonx_project.store import Store
from tests.life_env import Life, base_script, card_json
from tests.test_lifecycle import M

ODD = "a | pipe, a comma\nand a second line \\ with a backslash"


def _git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def _sample(tmp_path) -> Store:
    store = Store(tmp_path / "s" / "project.db")
    policy = Policy.w2(max_parallel=3)
    policy.budget.max_usd = 12.5
    store.add_milestone(Milestone("m1", str(tmp_path), f"first {ODD}", goal=f"Goal line one\n\n{ODD}",
                                  rows=["R-01", "R-02"], state=MilestoneState.RUNNING, policy=policy,
                                  reason=ODD, cost_usd=1.25, replans=1))  # fmt: skip
    store.add_cards([
        Card("C1", "m1", "add", intent=ODD, rows=["R-01"], criteria=["[R-01] WHEN add THE SYSTEM SHALL sum", ODD],
             paths=["calc.py", "tests/x, y.py"], state=CardState.DONE, reason="merged at abc", run_id="m1.C1.1",
             cost_usd=0.62, epic="E1", priority=-2),
        Card("C2", "m1", "double", rows=["R-02"], deps=["C1"], state=CardState.RUNNING, stage=Stage.REVIEW,
             kind="test", risk="high", reopens=1),
        Card("C3", "m1", "nothing", state=CardState.PENDING, deps=["C1", "C2"]),
    ])  # fmt: skip
    return store


def _board(store: Store):
    return [(m, store.cards(m.id)) for m in store.milestones()]


def test_store_to_files_to_store_is_equal(tmp_path):
    store = _sample(tmp_path)
    brd = tmp_path / "docs" / "brd"
    changed = write_board(brd, _board(store))
    assert sorted(p.name for p in changed) == ["README.md", "m1.md"]
    assert write_board(brd, _board(store)) == []  # unchanged: nothing rewritten, nothing to commit

    (bf,) = read_board(brd, str(tmp_path))
    ms, want = bf.milestone, store.get_milestone("m1")
    for f in ("id", "title", "goal", "rows", "state", "reason", "cost_usd", "replans", "created"):
        assert getattr(ms, f) == getattr(want, f), f
    assert ms.policy.to_dict() == want.policy.to_dict()
    fields = ("id", "title", "intent", "rows", "criteria", "deps", "paths", "kind", "risk", "priority", "epic",
              "state", "stage", "reason", "reopens", "run_id", "cost_usd", "created")  # fmt: skip
    for got, exp in zip(bf.cards, store.cards("m1")):
        for f in fields:
            assert getattr(got, f) == getattr(exp, f), (got.id, f)

    text = (brd / "board" / "m1.md").read_text()
    assert "### C2 · double · running" in text  # a person reads each card's status in its heading
    assert "- Kind: test" in text and "- Kind: code" not in text  # a default is left out
    assert "Priority" not in text and "Cost" not in text  # what only a rebuild needs is in the comment
    assert "| m1 | first a \\| pipe" in (brd / "board" / "README.md").read_text()


OLD = """# m0 · baseline

| Field | Value |
|---|---|
| State | done |
| Mode | w2 |
| Rows | every row not verified |
| Budget | $30.00, 8 h · spent $0.35 |
| Replans | 1 |
| Reason | adopted |

## Goal

What was built.

## Cards

| Card | Title | Rows | Deps | Kind | Status |
|---|---|---|---|---|---|
| C1 | Door · in | R-01 |  | code | done |
| C2 | Store | R-02 | C1 | test | running |

### C1 · Door · in

- Intent: Accept calls.
- Rows: R-01
- Deps: 
- Files: app/main.py, app/x.py
- Kind: code
- Risk: low
- Priority: 0
- Epic: 
- Criteria:
  - a missing one → 403
- Status: done
- Stage: 
- Reopens: 0
- Outcome: adopted
- Run: 
- Cost: 0.0000

### C2 · Store

- Intent: 
- Rows: R-02
- Deps: C1
- Files: 
- Kind: test
- Risk: high
- Priority: 3
- Epic: E1
- Criteria:
- Status: running
- Stage: review
- Reopens: 1
- Outcome: 
- Run: m0.C2.1
- Cost: 0.2500

<!-- operonx-project: {"created": 1.0, "policy": {}, "times": {"C1": [2.0, 3.0], "C2": [4.0, 5.0]}, "updated": 6.0} -->
"""


def test_the_older_format_still_reads_and_comes_back_compact(tmp_path):
    folder = tmp_path / "brd" / "board"
    folder.mkdir(parents=True)
    (folder / "m0.md").write_text(OLD)
    (bf,) = read_board(tmp_path / "brd")
    ms, (c1, c2) = bf.milestone, bf.cards
    assert (ms.state, ms.rows, ms.reason, ms.replans, ms.cost_usd, ms.goal) == (
        MilestoneState.DONE, [], "adopted", 1, 0.35, "What was built.")
    assert (c1.title, c1.paths, c1.criteria, c1.state, c1.reason, c1.created) == (
        "Door · in", ["app/main.py", "app/x.py"], ["a missing one → 403"], CardState.DONE, "adopted", 2.0)
    assert (c2.deps, c2.kind, c2.risk, c2.priority, c2.epic, c2.stage, c2.reopens, c2.run_id, c2.cost_usd) == (
        ["C1"], "test", "high", 3, "E1", Stage.REVIEW, 1, "m0.C2.1", 0.25)

    write_board(tmp_path / "brd", [(ms, bf.cards)])
    assert len((folder / "m0.md").read_text()) < len(OLD) * 0.8
    (again,) = read_board(tmp_path / "brd")
    assert again.milestone.cost_usd == 0.35 and again.milestone.replans == 1
    for got, exp in zip(again.cards, bf.cards):
        assert got == exp


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "x.txt").write_text("x\n")
    _git(repo, "add", "x.txt")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return repo


def test_a_lost_store_is_rebuilt_from_the_files(tmp_path):
    repo = _repo(tmp_path)
    write_board(repo / "docs" / "brd", _board(_sample(tmp_path)))
    project = Project.open(repo, worktrees=tmp_path / "wt")  # no project.db: rebuilt
    ms = project.store.get_milestone("m1")
    assert ms.state == MilestoneState.PAUSED and "it was running" in ms.reason
    states = {c.id: c.state for c in project.store.cards("m1")}
    assert states == {"C1": CardState.DONE, "C2": CardState.READY, "C3": CardState.PENDING}
    c2 = project.store.get_card("m1", "C2")
    assert c2.stage is None and "it was running" in c2.reason
    # opened again, the store is the store: nothing is rebuilt twice
    assert len(Project.open(repo, worktrees=tmp_path / "wt").store.milestones()) == 1


def test_from_board_seeds_the_plan_and_skips_the_planner(tmp_path):
    src = tmp_path / "src"
    write_board(src, _board(_sample(tmp_path)))
    seed = board_seed(src, "m1")
    assert [c["id"] for c in seed["cards"]] == ["C1", "C2", "C3"]
    assert seed["cards"][2]["deps"] == ["C1", "C2"] and "state" not in seed["cards"][0]

    # a fresh calc repo, its plan from the board: the planner is never asked
    script = base_script(**{"planner:plan": []})
    life = Life(tmp_path / "life", script, mode="w1")
    life.store.ms_put("m1", "seeded", {"from": "x", "cards": [card_json("C1")]})
    life.command("start")
    assert life.serve() == M.AWAITING_APPROVAL
    assert life.store.ms_get("m1", "plan")["seeded_from"] == "x"
    life.command("approve")
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason
    assert life.cards() == {"C1": "done"}


def test_the_milestone_branch_carries_its_board(tmp_path):
    life = Life(tmp_path, base_script(), mode="w1")
    life.command("start")
    life.serve()
    life.command("approve")
    assert life.serve() == M.DONE
    integ = life.integ()
    (bf,) = read_board(integ / "docs" / "brd")
    assert bf.milestone.state == MilestoneState.DONE and [(c.id, c.state) for c in bf.cards] == [("C1", CardState.DONE)]
    log = _git(integ, "log", "--format=%s")
    assert "m1: board (C1 done)" in log and "m1: board (m1 done)" in log
    assert _git(integ, "status", "--porcelain") == ""


def test_create_milestone_keeps_the_seed(tmp_path):
    repo = _repo(tmp_path)
    project = Project.open(repo, worktrees=tmp_path / "wt")
    create_milestone(project, "again", seed={"from": "f", "cards": [{"id": "C1"}]}, mid="m2")
    assert project.store.ms_get("m2", "seeded")["cards"] == [{"id": "C1"}]


def test_brd_check_reads_the_board(tmp_path):
    from operonx.cli.init import brd_files

    from operonx_project.brd.checker import check

    brd = tmp_path / "docs" / "brd"
    for rel, content in brd_files("calc").items():
        (brd / rel).parent.mkdir(parents=True, exist_ok=True)
        (brd / rel).write_bytes(content)
    write_board(brd, _board(_sample(tmp_path)))
    errors, _ = check(brd)
    board = [e for e in errors if e.startswith("board/")]
    assert board == ["board/m1.md: C1 serves R-01, which is not defined",
                     "board/m1.md: C2 serves R-02, which is not defined"], errors  # fmt: skip
