"""P6: a card's question in a BRD milestone becomes a ``Q-nn`` in open_questions.md, and its answer the resolution."""

from __future__ import annotations

from operonx_project.brd.checker import check
from operonx_project.brd.records import QUESTIONS, load_questions
from operonx_project.interactions import Interactions
from operonx_project.model import MilestoneState as M
from operonx_project.model import Policy
from tests.flow_env import RIGHT, git
from tests.life_env import Life, w2_script, write_source


def test_a_card_question_is_a_brd_entry_resolved_by_the_answer(tmp_path):
    ask = {"output": {"summary": "stuck", "needs_human": {"question": "Should add accept strings?",
                                                          "category": "requirements",
                                                          "options": [{"key": "a", "label": "no"},
                                                                      {"key": "b", "label": "yes"}]}}}  # fmt: skip
    src = write_source(tmp_path)
    policy = Policy.w2(max_parallel=1, ready_depth=1, approve_plan="auto")
    script = w2_script(
        implementer=[ask, {"write": {"calc.py": RIGHT}}], **{"planner:replenish": [{"output": {"cards": []}}]}
    )
    life = Life(tmp_path, script, mode="w2", policy=policy, sources=[{"path": str(src), "title": "Calc BRD"}])
    life.command("start")
    assert life.serve() == M.WAITING_HUMAN
    brd = life.integ() / "docs" / "brd"
    [q] = [q for q in load_questions(brd) if "Should add accept strings?" in q.topic]
    assert q.state == "open" and q.topic.startswith("C1: ")
    [inter] = life.store.interactions("m1")
    assert inter["qid"] == q.id and inter["kind"] == "card"
    assert "C1: Should add accept strings" in git(life.integ(), "log", "-1", "--format=%s")

    inter_api = Interactions(life.store, project=life.project)
    card = life.store.get_card("m1", "C1")
    life.store.update_card("m1", "C1", actor="owner", paths=card.paths + ["calc_extra.py"],
                           criteria=card.criteria + ["WHEN add gets a str THE SYSTEM SHALL raise"])  # fmt: skip
    inter_api.sync()  # an edit to the card waiting on it leaves the question open
    assert life.store.get_interaction(inter["id"])["state"] == "open"
    got = inter_api.submit_answer(q.id, "a: and say so in the error", actor="owner", via="cli", mid="m1")
    assert got.ok, got.message
    assert inter_api.submit_answer(q.id, "b").status == "already"
    life.serve()
    entries = {x.id: x for x in load_questions(brd)}
    assert entries[q.id].state == "resolved"
    text = (brd / QUESTIONS).read_text()
    assert "a — a: and say so in the error (owner," in text
    assert life.store.get_interaction(inter["id"])["state"] == "delivered"
    errors, _ = check(brd)
    assert errors == []
