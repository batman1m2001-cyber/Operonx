"""The retrospective (PROJECT_PRACTICES_PLAN D4), with a scripted coder: the report in DELIVERY.md, the lessons
in the project's lessons file on the milestone's branch, and the next session told them."""

from __future__ import annotations

from operonx_project.model import MilestoneState as M
from operonx_project.practices import FILE
from operonx_project.retro import append
from operonx_project.rules import standing_rules
from tests.flow_env import RIGHT, WRONG, git, impl, make_repo
from tests.life_env import Life, base_script

LESSON = {"rule": "add() returns a + b: read the operator before the first round",
          "evidence": "C1, round 1 failed the acceptance tests"}  # fmt: skip


def _life(tmp_path, practices=None, **script):
    repo = make_repo(tmp_path)
    if practices is not None:
        (repo / FILE).parent.mkdir(parents=True)
        (repo / FILE).write_text(practices)
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "practices")
    life = Life(tmp_path, base_script(**script), mode="w1", repo=repo)
    life.command("start")
    assert life.serve() == M.AWAITING_APPROVAL
    life.command("approve")
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason
    return life


def test_a_round_lost_becomes_a_lesson_the_next_session_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("OPERONX_OWNER_RULES", str(tmp_path / "none.md"))
    life = _life(tmp_path, '[lessons]\nfile = "docs/brd/LESSONS.md"\n',
                 implementer=[impl(WRONG + "# not yet\n"), impl(RIGHT)],
                 **{"planner:retrospective": [{"output": {"lessons": [LESSON]}}]})  # fmt: skip
    [call] = life.coder.of("planner:retrospective")
    assert '"rounds": 2' in call["prompt"] and "(none yet)" in call["prompt"]
    integ = life.integ()
    lessons = (integ / "docs/brd/LESSONS.md").read_text()
    assert "## m1 (" in lessons and f"- {LESSON['rule']} ({LESSON['evidence']})" in lessons
    assert "m1: lessons (docs/brd/LESSONS.md)" in git(integ, "log", "--format=%s")
    delivery = (integ / "DELIVERY.md").read_text()
    assert "## Retrospective" in delivery and "| C1 | done | 2 |" in delivery and LESSON["rule"] in delivery
    # the next milestone's sessions are told it
    assert LESSON["rule"] in standing_rules(integ)
    rec = life.store.ms_get("m1", "retro")
    assert "acceptance" in rec["report"]["cards"][0]["failed_checks"] and rec["file"] == "docs/brd/LESSONS.md"


def test_no_friction_or_no_lessons_file_is_no_session(tmp_path):
    life = _life(tmp_path)  # no practices: the report only, as DELIVERY.md's last section
    assert not life.coder.of("planner:retrospective")
    assert "## Retrospective" in (life.integ() / "DELIVERY.md").read_text()
    assert not (life.integ() / "docs/brd/LESSONS.md").exists()


def test_a_smooth_milestone_teaches_nothing_and_costs_nothing(tmp_path):
    life = _life(tmp_path, '[lessons]\nfile = "docs/brd/LESSONS.md"\n')
    assert not life.coder.of("planner:retrospective")


def test_a_failed_retrospective_never_holds_the_delivery(tmp_path):
    life = _life(tmp_path, '[lessons]\nfile = "docs/brd/LESSONS.md"\n',
                 implementer=[impl(WRONG + "# not yet\n"), impl(RIGHT)],
                 **{"planner:retrospective": [{"status": "infra", "error": "boom"}]})  # fmt: skip
    assert [e["data"]["error"] for e in life.store.events("m1", limit=10_000) if e["kind"] == "retro.failed"]
    assert (life.integ() / "DELIVERY.md").exists()


def test_append_keeps_what_is_there_and_at_most_five(tmp_path):
    (tmp_path / "L.md").write_text("# Lessons\n\n- an old one (m0)\n")
    assert append(tmp_path, "L.md", "m2", [{"rule": f"r{i}", "evidence": "e"} for i in range(8)])
    text = (tmp_path / "L.md").read_text()
    assert text.startswith("# Lessons\n\n- an old one (m0)\n\n## m2 (") and text.count("\n- r") == 5
    assert not append(tmp_path, "L.md", "m3", [{"rule": " ", "evidence": ""}])


def test_what_the_milestone_fixed_is_in_the_report(tmp_path):
    # tipcalc m3: the architecture check failed at the base (the _RaisingOp access) and passed at the end; the Board
    # and DELIVERY.md said only "1 violation before it"
    from operonx_project.retro import report

    repo = make_repo(tmp_path)
    (repo / "calc.py").write_text(RIGHT + "\nimport json\n\nD = json._default_decoder\n")
    (repo / FILE).parent.mkdir(parents=True)
    (repo / FILE).write_text('[architecture]\nprivate = "forbid"\n')
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "a violation, and the rule")
    life = Life(tmp_path, base_script(implementer=[impl(RIGHT)]), mode="w1", repo=repo)
    life.command("start")
    assert life.serve() == M.AWAITING_APPROVAL
    life.command("approve")
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason
    rep = report(life.store, "m1")
    assert rep["checks"] == [{"name": "architecture", "before_ok": False,
                              "before": ["arch:private:calc.py->json._default_decoder"], "after_ok": True,
                              "after": []}]  # fmt: skip
    assert "Checks: architecture 1 failing before, passing now." in (life.integ() / "DELIVERY.md").read_text()
