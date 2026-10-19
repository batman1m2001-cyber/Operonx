"""The milestone's cap holds in planning too, not only on the cards.

tipcalc's first milestone (2026-10-19) spent $6.46 against a $5 cap before
a card ran: three plan rounds, each reviewed, with no stage session checking
the money. These replay those costs with a scripted coder."""

from __future__ import annotations

from operonx_project.model import MilestoneState as M
from operonx_project.model import Policy
from tests.life_env import SURVEY, Life, base_script, card_json, plan

BLOCK = {"output": {"verdict": "revise", "notes": [{"severity": "blocker", "note": "say how rounding works"}]}}


def _policy(max_usd: float) -> Policy:
    p = Policy.w1()
    p.budget.max_usd = max_usd
    return p


def test_planning_stops_at_the_cap_and_the_owner_gets_the_plan(tmp_path):
    script = base_script(**{
        "planner:survey": [{**SURVEY, "cost": 0.09}],
        "planner:plan": [{**plan([card_json("C1")]), "cost": c} for c in (0.29, 0.49, 0.69)],
        "reviewer:plan-review": [{**BLOCK, "cost": c} for c in (1.76, 1.70, 1.45)],
    })  # fmt: skip
    life = Life(tmp_path, script, mode="w1", policy=_policy(5.0))
    life.command("start")
    assert life.serve() == M.AWAITING_APPROVAL
    ms = life.store.get_milestone("m1")
    # survey 0.09 + plan 0.29 + review 1.76 + plan 0.49 + review 1.70 = 4.33; plan 3 (≤ $0.67 allowed) → 5.02;
    # the third review is not paid for: the plan goes to the owner unreviewed, with that said
    calls = [c["step"] for c in life.coder.calls]
    assert calls == ["survey", "plan-1", "plan-review-1", "plan-2", "plan-review-2", "plan-3"], calls
    assert round(ms.cost_usd, 2) == 5.02
    rec = life.store.ms_get("m1", "plan")
    assert not rec["passed"] and rec["rounds"] == 3 and "not reviewed" in rec["notes"][0]["note"]


def test_each_stage_session_is_capped_at_what_is_left(tmp_path):
    life = Life(tmp_path, base_script(**{"planner:survey": [{**SURVEY, "cost": 0.9}]}), mode="w1",
                policy=_policy(1.0))  # fmt: skip
    allowed = {}
    run = life.coder.run

    async def spy(req, on_start=None):
        allowed[req.tags.get("step")] = req.max_usd
        return await run(req, on_start)

    life.coder.run = spy
    life.command("start")
    life.serve()
    assert allowed["survey"] == 1.0  # the per-call ceiling is $2: the milestone's $1 is lower
    assert round(allowed["plan-1"], 2) == 0.10  # what the survey left


def test_spent_before_a_stage_is_budget_exhausted_and_retry_resumes_that_stage(tmp_path):
    life = Life(tmp_path, base_script(**{"planner:survey": [{**SURVEY, "cost": 3.0}]}), mode="w1",
                policy=_policy(2.0))  # fmt: skip
    life.command("start")
    assert life.serve() == M.BUDGET_EXHAUSTED
    ms = life.store.get_milestone("m1")
    assert ms.reason.startswith("planning: spent $3.00 of $2.00 before plan-1"), ms.reason
    assert [c["step"] for c in life.coder.calls] == ["survey"]
    life.command("retry", usd=2)
    assert life.serve() == M.AWAITING_APPROVAL
    assert [c["step"] for c in life.coder.calls] == ["survey", "plan-1", "plan-review-1"]
