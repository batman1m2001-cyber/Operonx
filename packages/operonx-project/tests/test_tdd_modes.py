"""TDD modes (PROJECT_PRACTICES_PLAN D3), with a scripted coder: strict proves the acceptance tests fail
before the code and has a vacuous set written again; off writes no tests."""

from __future__ import annotations

from operonx_project.model import MilestoneState as M
from operonx_project.practices import FILE
from tests.flow_env import RIGHT, author_step, git, make_repo
from tests.life_env import Life, base_script

VACUOUS = {"write": {"tests/acceptance/C1/test_c1_add.py": "def test_add():\n    assert True\n"},
           "output": {"files": ["tests/acceptance/C1/test_c1_add.py"]}}  # fmt: skip


def _life(tmp_path, mode, **script):
    repo = make_repo(tmp_path)
    (repo / FILE).parent.mkdir(parents=True)
    (repo / FILE).write_text(f'[tdd]\nmode = "{mode}"\n')
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "practices")
    life = Life(tmp_path, base_script(**script), mode="w1", repo=repo)
    life.command("start")
    assert life.serve() == M.AWAITING_APPROVAL
    life.command("approve")
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason
    return life


def test_strict_writes_a_vacuous_set_again_and_records_the_red(tmp_path):
    life = _life(tmp_path, "strict", test_author=[VACUOUS, author_step("C1")])
    authors = life.coder.of("test_author")
    assert len(authors) == 2 and "already pass before any code is written" in authors[1]["prompt"]
    assert "found wrong by the engine" in authors[1]["prompt"]
    red = life.store.get_card("m1", "C1").detail["red"]
    assert red["red"] and red["rewritten"] and red["passed"] == 0 and red["failed"]
    assert [e["data"] for e in life.store.events("m1", limit=10_000) if e["kind"] == "acceptance.vacuous"] == [
        {"passed": 1}
    ]
    assert (life.integ() / "tests/acceptance/C1/test_c1_add.py").read_text() != VACUOUS["write"][
        "tests/acceptance/C1/test_c1_add.py"
    ]


def test_strict_red_at_once_is_one_author_and_its_evidence(tmp_path):
    life = _life(tmp_path, "strict")
    assert len(life.coder.of("test_author")) == 1
    red = life.store.get_card("m1", "C1").detail["red"]
    assert red["red"] and not red["rewritten"] and red["exit_code"] == 1


def test_acceptance_mode_runs_no_red_check(tmp_path):
    life = _life(tmp_path, "acceptance", test_author=[VACUOUS])
    assert len(life.coder.of("test_author")) == 1 and "red" not in life.store.get_card("m1", "C1").detail


def test_off_writes_neither_set_and_the_checks_judge_the_card(tmp_path):
    life = _life(tmp_path, "off")
    assert not life.coder.of("test_author") and not life.coder.of("examiner")
    detail = life.store.get_card("m1", "C1").detail
    assert detail["acceptance"]["tdd"] == "off" and not detail["acceptance"]["files"]
    assert (life.integ() / "calc.py").read_text() == RIGHT


def test_a_rewrite_that_keeps_a_file_keeps_it_protected(tmp_path):
    """A rewrite lists every file in the directory, not only the ones it changed."""
    keep = "tests/acceptance/C1/test_c1_keep.py"
    first = {"write": {keep: "from calc import add\n\n\ndef test_keep():\n    assert add(2, 2) == 4\n",
                       **VACUOUS["write"]}, "output": {"files": [keep]}}  # fmt: skip
    life = _life(tmp_path, "strict", test_author=[first, author_step("C1")])
    files = life.store.get_card("m1", "C1").detail["acceptance"]["files"]
    assert keep in files and "tests/acceptance/C1/test_c1_add.py" in files
    assert not [f for f in files if "__pycache__" in f or f.endswith(".pyc")], files  # the red run's caches
