"""A card worked by a person or an assistant (plan D7): take, submit, release.

The taken card starts like any other (its acceptance tests are written, its
held-out tests too), then its run parks for the taker instead of calling the
engine's implementer. A submission is a round through the same gates: the
checks, the review and the held-out tests. A failing one parks again with
what the round found; a passing one is merged and done.
"""

from __future__ import annotations

from operonx_project.api import act, taker_brief
from operonx_project.interactions import Interactions
from tests.flow_env import ABS, RIGHT
from tests.life_env import Life, base_script
from tests.test_lifecycle import M


def _taken_life(tmp_path) -> Life:
    life = Life(tmp_path, base_script(), mode="w1")
    life.command("start")
    assert life.serve() == M.AWAITING_APPROVAL
    life.command("approve")
    life.command("take", card="C1", actor="assistant")
    return life


def test_a_taken_card_waits_for_its_taker_and_a_submit_is_a_round(tmp_path):
    life = _taken_life(tmp_path)
    assert life.serve() == M.WAITING_HUMAN
    ms = life.store.get_milestone("m1")
    assert "waiting on a submit: C1 (assistant)" in ms.reason
    card = life.store.get_card("m1", "C1")
    assert card.state.value == "waiting_human" and card.detail["question"]["kind"] == "taken"
    assert card.detail["acceptance"]["files"] and card.detail["heldout"]  # the gates were made first
    assert Interactions(life.store).open() == []  # nothing waits on the owner
    brief = taker_brief(life.project, "m1", "C1")
    assert brief["waiting_for_submit"] and brief["round"] == 0 and brief["taken"]["actor"] == "assistant"
    wt = life.project.git.worktree_path("m1", "C1")
    assert brief["worktree"] == str(wt) and (wt / brief["acceptance_tests"]).is_dir()

    # it passes the visible test but not the held-out one: back to the taker, with what was found
    (wt / "calc.py").write_text(ABS)
    life.command("submit", card="C1", summary="abs then add")
    assert life.serve() == M.WAITING_HUMAN
    brief = taker_brief(life.project, "m1", "C1")
    assert brief["waiting_for_submit"] and brief["round"] == 1 and brief["feedback"]

    (wt / "calc.py").write_text(RIGHT)
    life.command("submit", card="C1", summary="plain add")
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason
    assert life.cards() == {"C1": "done"}
    assert (life.integ() / "calc.py").read_text() == RIGHT
    assert not [c for c in life.coder.calls if c["role"] == "implementer"]  # the engine never implemented it
    kinds = [e["kind"] for e in life.store.events("m1") if e["kind"].startswith("card.")]
    assert "card.taken" in kinds and kinds.count("card.submitted") == 2


def test_a_submit_that_changed_nothing_is_not_verified(tmp_path):
    # tipcalc m1/C6: a taker's empty submit was verified ("checks pass, review clean") and merged as nothing
    life = _taken_life(tmp_path)
    assert life.serve() == M.WAITING_HUMAN
    life.command("submit", card="C1", summary="no change")
    assert life.serve() == M.WAITING_HUMAN
    brief = taker_brief(life.project, "m1", "C1")
    assert brief["waiting_for_submit"] and brief["round"] == 1 and "changed no file" in brief["feedback"]
    assert life.store.get_card("m1", "C1").state.value == "waiting_human"


def test_release_gives_the_card_back_to_the_engine(tmp_path):
    life = _taken_life(tmp_path)
    assert life.serve() == M.WAITING_HUMAN
    life.command("release", card="C1")
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason
    assert [c["role"] for c in life.coder.calls].count("implementer") == 1


def test_take_refuses_what_it_cannot_take(tmp_path):
    life = _taken_life(tmp_path)
    life.serve()
    life.command("take", card="C1", actor="someone else")
    life.command("submit", card="C9")
    life.serve()
    refused = [c["result"] for c in life.store.commands("m1") if c["status"] == "refused"]
    assert any("taken by assistant" in r for r in refused) and any("no card 'C9'" in r for r in refused)
    # the studio and the assistant go through the api: no daemon, nothing queued
    got = act(life.project, {"op": "submit", "milestone": "m1", "card": "C1"}, actor="studio:me")
    assert got["status"] == "refused" and "no daemon" in got["message"]


def test_the_api_starts_a_milestone_for_rows_of_the_checklist(tmp_path):
    from tests.life_env import ADD_ROW, Life, w2_script  # noqa: F811

    life = Life(tmp_path, w2_script(), mode="w2")
    brd = life.repo / "docs" / "brd"
    from operonx.cli.init import brd_files

    from operonx_project.brd.records import Requirement, add_matrix_rows

    for rel, content in brd_files("calc").items():
        (brd / rel).parent.mkdir(parents=True, exist_ok=True)
        (brd / rel).write_bytes(content)
    add_matrix_rows(brd, [Requirement(id=ADD_ROW, type="functional", quote="add", location="§1")], "Calc")
    got = act(life.project, {"op": "start", "title": "adding", "rows": ["NOPE-REQ-X-01"]})
    assert got["status"] == "invalid" and "NOPE-REQ-X-01" in got["message"]
    got = act(life.project, {"op": "start", "title": "adding", "rows": [ADD_ROW], "max_usd": 4})
    assert got["ok"] and got["milestone"] == "m2"
    ms = life.store.get_milestone("m2")
    assert ms.rows == [ADD_ROW] and ms.policy.budget.max_usd == 4
    assert [c["kind"] for c in life.store.commands("m2")] == ["start"]


def test_a_card_can_be_taken_ahead_and_waits_for_its_dependencies_first(tmp_path):
    from tests.flow_env import author_step, impl
    from tests.life_env import card_json, plan

    script = base_script(**{
        "planner:plan": [plan([card_json("C1"), card_json("C2", title="add again", deps=["C1"])])],
        "test_author": [author_step("C1"), author_step("C2")],
        "implementer": [impl(RIGHT)],
    })  # fmt: skip
    life = Life(tmp_path, script, mode="w1")
    life.command("start")
    assert life.serve() == M.AWAITING_APPROVAL
    life.command("approve")
    life.command("take", card="C2", actor="assistant")  # still pending: C1 is not done
    assert life.serve() == M.WAITING_HUMAN
    assert life.cards() == {"C1": "done", "C2": "waiting_human"}  # the engine did C1, then C2 waits for its taker
    assert taker_brief(life.project, "m1", "C2")["waiting_for_submit"]
    assert [c["cwd"].rsplit("/", 1)[-1] for c in life.coder.of("implementer")] == ["C1"]
    taken = next(e for e in life.store.events("m1") if e["kind"] == "card.taken")
    assert taken["card"] == "C2"


def test_a_wrong_acceptance_test_is_rewritten_on_the_owners_word(tmp_path):
    # tipcalc m2/C3: the test author expected output the tool never prints; the implementer may not
    # change it, so every round failed, and keep / skip / stop could not fix it
    from operonx_project.model import Policy
    from tests.flow_env import ACCEPTANCE, impl

    path = "tests/acceptance/C1/test_c1_add.py"
    wrong = ACCEPTANCE.replace("== 5", "== 6")
    script = base_script(**{
        "test_author": [{"write": {path: wrong}, "output": {"files": [path]}},
                        {"write": {path: ACCEPTANCE}, "output": {"files": [path]}}],
        "implementer": [impl(RIGHT + f"# {i}\n") for i in range(3)],
    })  # fmt: skip
    policy = Policy.w1(approve_plan="auto", heldout=False)
    policy.budget.max_rounds = 2
    life = Life(tmp_path, script, policy=policy)
    life.command("start")
    assert life.serve() == M.WAITING_HUMAN
    (q,) = Interactions(life.store).open()
    assert "rewrite_tests" in [o["key"] for o in q["payload"]["options"]]
    assert Interactions(life.store).submit_answer(q["id"], {"choice": "rewrite_tests", "text": "6 is wrong"}).ok
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason
    again = life.coder.of("test_author")[1]["prompt"]
    assert "found wrong by the owner" in again and "6 is wrong" in again
    assert (life.integ() / path).read_text() == ACCEPTANCE
