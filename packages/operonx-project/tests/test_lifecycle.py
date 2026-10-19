"""P5: a milestone's whole lifecycle, in both modes, with a scripted coder."""

from __future__ import annotations

import json

import pytest

from operonx_project.model import MilestoneState as M
from operonx_project.model import Policy
from tests.flow_env import RIGHT, git
from tests.life_env import Life, base_script


def test_w1_whole_lifecycle(tmp_path):
    life = Life(tmp_path, base_script(), mode="w1")
    life.command("start")
    assert life.serve() == M.AWAITING_APPROVAL
    plan = life.store.ms_get("m1", "plan")
    assert plan["passed"] and plan["rounds"] == 1
    life.command("approve")
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason
    assert life.cards() == {"C1": "done"}
    integ = life.integ()
    assert (integ / "calc.py").read_text() == RIGHT
    delivery = (integ / "DELIVERY.md").read_text()
    assert "**C1** add numbers" in delivery
    assert "DELIVERY.md" in git(integ, "log", "-2", "--name-only")  # then the board commit (m1 done)
    base = life.store.ms_get("m1", "baseline")
    assert [c["name"] for c in base["checks"]] == ["tests"] and base["results"][0]["ok"]
    # each flow run is a run page in the studio: the project's run store, out of git
    traced = {d.name: json.loads((d / "meta.json").read_text())["status"]
              for d in life.project.runs.glob("*/*/*/m1.*")}  # fmt: skip
    assert traced == {"m1.discover.1": "ok", "m1.plan.1": "ok", "m1.C1.1": "ok", "m1.final.1": "ok"}, traced
    assert life.store.get_card("m1", "C1").run_id == "m1.C1.1"
    assert git(life.repo, "status", "--porcelain", "--ignored", ".operonx/runs").startswith("!!")


def test_w2_whole_lifecycle_brd_to_verified_rows(tmp_path):
    from operonx_project.brd.checker import check
    from operonx_project.brd.records import matrix_status
    from tests.life_env import ADD_ROW, DOUBLE, DOUBLE_ROW, w2_script, write_source

    src = write_source(tmp_path)
    policy = Policy.w2(max_parallel=1, ready_depth=1, approve_plan="auto")
    life = Life(tmp_path, w2_script(), mode="w2", policy=policy, sources=[{"path": str(src), "title": "Calc BRD"}])
    life.command("start")
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason
    assert life.cards() == {"C1": "done", "C2": "done"}
    # the replenisher detailed C2 once, when Ready ran dry, and stopped with no rows left
    assert len(life.coder.of("planner:replenish")) == 1
    brd = life.integ() / "docs" / "brd"
    assert matrix_status(brd) == {ADD_ROW: "implemented_verified", DOUBLE_ROW: "implemented_verified"}
    matrix = (brd / "requirements" / "requirements_traceability.md").read_text()
    assert "tests/acceptance/C2/test_c2_double.py::test_double" in matrix
    errors, _ = check(brd)
    assert errors == []
    assert (life.integ() / "calc.py").read_text() == DOUBLE
    delivery = (life.integ() / "DELIVERY.md").read_text()
    assert f"| {DOUBLE_ROW} | implemented_verified | C2 |" in delivery


@pytest.mark.parametrize("env", ["engine", pytest.param("own", marks=pytest.mark.slow)])  # own: PyPI, ~1 min
def test_greenfield_milestone_creates_its_project_with_operonx_init(tmp_path, env):
    from operonx_project.project import new_project
    from tests.life_env import card_json, plan

    proj = new_project(tmp_path / "gf", name="gfdemo", worktrees=tmp_path / "worktrees")
    test = "from extra import triple\n\n\ndef test_triple():\n    assert triple(2) == 6\n"
    script = {
        "planner:plan": [plan([card_json("C1", title="triple", intent="extra.triple triples", paths=["src/extra/**"],
                                         criteria=[{"row": "", "text": "WHEN triple(x) THE SYSTEM SHALL return 3x"}])],
                              init={"template": "hello", "toolchain": "pip", "name": "gfdemo", "why": "a job"})],
        "test_author": [{"write": {"tests/acceptance/C1/test_c1_triple.py": test}}],
        "examiner": [{"output": {"files": []}}],
        "implementer": [{"write": {"src/extra/__init__.py": "def triple(x):\n    return 3 * x\n"}}],
    }  # fmt: skip
    life = Life(tmp_path, script, mode="w1", repo=proj.repo, goal="a triple function", policy=Policy.w1(env=env))
    life.command("start")
    assert life.serve() == M.AWAITING_APPROVAL
    assert not life.coder.of("planner:survey")  # an empty repo is not surveyed
    base = life.store.ms_get("m1", "baseline")
    assert base["greenfield"] and [c["name"] for c in base["checks"]] == ["tests", "lint", "guide"]
    assert "operonx lint --build" in base["checks"][1]["cmd"] and "guide --check" in base["checks"][2]["cmd"]
    life.command("approve")
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason
    assert life.cards() == {"C0": "done", "C1": "done"}
    c1 = life.store.get_card("m1", "C1")
    assert c1.deps == ["C0"]
    # C1's QA ran the operonx checks on the project operonx init made, and they passed
    assert {r["name"]: r["ok"] for r in c1.detail["baseline"]} == {"tests": True, "lint": True, "guide": True}
    integ = life.integ()
    for f in ("app/main.py", "operonx.toml", "AGENTS.md", ".operonx/guide/README.md", "src/extra/__init__.py"):
        assert (integ / f).exists(), f
    log = git(integ, "log", "--format=%s")
    assert "m1/C0: operonx init --template hello --pip" in log
    if env == "engine":
        return
    # the project's own environment, made with its toolchain after init; C1's checks ran with it
    assert "m1/C0: the guide copy of the project's own environment" in log
    [made] = [e for e in life.store.events("m1", limit=100_000) if e["kind"] == "env.made"]
    venv = life.project.state / "venv"
    assert made["data"]["ok"] and (venv / "bin" / "python").exists()
    assert str(venv / "bin" / "python") in life.store.ms_get("m1", "baseline")["checks"][0]["cmd"]


def test_plan_review_is_capped_and_the_notes_go_to_the_owner(tmp_path):
    from tests.life_env import card_json, plan

    revise = {"output": {"verdict": "revise", "notes": [{"severity": "major", "card": "C1", "note": "split it"}]}}
    life = Life(tmp_path, base_script(**{"reviewer:plan-review": [revise]}), mode="w1")
    life.command("start")
    assert life.serve() == M.AWAITING_APPROVAL
    rec = life.store.ms_get("m1", "plan")
    # one draft and two revisions (plan_reviews=2), then the owner, not another loop
    assert rec["rounds"] == 3 and not rec["passed"] and rec["notes"][0]["note"] == "split it"
    assert len(life.coder.of("planner:plan")) == 3 and len(life.coder.of("reviewer:plan-review")) == 3
    assert "split it" in life.coder.of("planner:plan")[1]["prompt"]
    assert "unresolved" in life.store.get_milestone("m1").reason
    # the owner may approve a plan the reviewer still questions
    life.command("approve")
    assert life.serve() == M.DONE

    # a plan that fails the engine's own checks cannot be approved
    life2 = Life(tmp_path / "b", base_script(**{"planner:plan": [plan([card_json("C1", deps=["C1"])])]}))
    life2.command("start")
    assert life2.serve() == M.AWAITING_APPROVAL
    assert "C1 depends on itself" in life2.store.ms_get("m1", "plan")["problems"]
    seq = life2.command("approve")
    life2.serve()
    done = {c["seq"]: c for c in life2.store._db.execute("SELECT * FROM commands").fetchall()}
    assert done[seq]["status"] == "refused" and "fails the engine's checks" in done[seq]["result"]
    assert life2.state() == M.AWAITING_APPROVAL and life2.store.cards("m1") == []


def test_replan_keeps_done_cards_and_replaces_the_rest(tmp_path):
    from tests.flow_env import author_step, impl
    from tests.life_env import card_json, plan

    c2 = card_json("C2", title="add again", deps=["C1"])
    c3 = card_json("C3", title="add, another way", deps=["C1"])
    script = base_script(**{
        "planner:plan": [plan([card_json("C1"), c2]), plan([c3])],
        "test_author": [author_step("C1"), author_step("C2"), author_step("C3")],
        "implementer": [impl(RIGHT), {"status": "infra", "error": "the sandbox died"}, impl(RIGHT + "# again\n")],
    })  # fmt: skip
    life = Life(tmp_path, script, mode="w1")
    life.command("start")
    life.serve()
    life.command("approve")
    assert life.serve() == M.AWAITING_APPROVAL  # C2 failed for good → replan → the new plan waits
    ms = life.store.get_milestone("m1")
    assert ms.replans == 1 and life.cards() == {"C1": "done", "C2": "cancelled"}
    replan_prompt = life.coder.of("planner:plan")[1]["prompt"]
    assert "Done cards are kept and never redone" in replan_prompt and '"id": "C1"' in replan_prompt
    life.command("approve")
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason
    assert life.cards() == {"C1": "done", "C2": "cancelled", "C3": "done"}
    # C1 was never redone: one implementer session each for C1, C2, C3
    assert [c["cwd"].rsplit("/", 1)[-1] for c in life.coder.of("implementer")] == ["C1", "C2", "C3"]
    assert (
        "C2 (add again) is cancelled: replaced by a replan; it had failed: infrastructure: the sandbox died"
        in (life.integ() / "DELIVERY.md").read_text()
    )


def test_the_replenisher_keeps_ready_at_depth_and_stops_with_no_rows_left(tmp_path):
    from tests.flow_env import author_step, impl
    from tests.life_env import (
        ADD_ROW,
        DOUBLE,
        DOUBLE_ROW,
        card_json,
        double_author,
        double_card,
        plan,
        w2_script,
        write_source,
    )

    third = "CALC-REQ-MATH-03"
    src = write_source(tmp_path)
    src.write_text(src.read_text() + "\n## 3. Halving\n\nThe calculator must halve a number.\n")
    ex = w2_script()["planner:extractor"][0]["output"]
    ex["requirements"].append({"id": third, "type": "functional", "section": "§3",
                               "quote": "The calculator must halve a number.", "reading": "halve"})  # fmt: skip
    c3 = card_json("C3", third, title="halve", deps=["C2"], epic="E1",
                   criteria=[{"row": third, "text": "WHEN half(x) THE SYSTEM SHALL return x / 2"}])  # fmt: skip
    epics = [{"id": "E1", "title": "arithmetic", "rows": [ADD_ROW, DOUBLE_ROW, third]}]
    half = DOUBLE + "\n\ndef half(x):\n    return x / 2\n"
    script = w2_script(**{
        "planner:extractor": [{"output": ex}],
        "planner:plan": [plan([card_json("C1", ADD_ROW, epic="E1")], epics=epics)],
        "planner:replenish": [{"output": {"cards": [double_card()]}}, {"output": {"cards": [c3]}}],
        "test_author": [author_step("C1"), double_author(), {"write": {}}],
        "implementer": [impl(RIGHT), impl(DOUBLE), impl(half)],
    })  # fmt: skip
    policy = Policy.w2(max_parallel=1, ready_depth=2, approve_plan="auto", heldout=False)
    life = Life(tmp_path, script, mode="w2", policy=policy, sources=[{"path": str(src)}])
    life.command("start")
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason
    calls = life.coder.of("planner:replenish")
    # Ready + Backlog held 1 of 2 each time it was asked: one card, twice; then no rows were left
    assert len(calls) == 2 and all("Detail up to 1 new cards" in c["prompt"] for c in calls)
    assert third in calls[1]["prompt"] and DOUBLE_ROW not in calls[1]["prompt"].split("Rows with no card yet")[1]
    assert life.cards() == {"C1": "done", "C2": "done", "C3": "done"}
