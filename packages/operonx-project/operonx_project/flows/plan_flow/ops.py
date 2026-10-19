"""The ops of ``plan_flow``: the planner drafts, the engine and a reviewer check.

``draft`` is the planner's session (W1: the whole DAG; W2: the epics, one
architecture note and the first ``ready_depth`` cards); a revision resumes
the same session with the review's notes. ``review`` runs the mechanical
checks (:func:`~operonx_project.plan.check_plan`) and a fresh session on
another model, which is told what the checks found and which cards' paths
overlap without a dependency. The plan ends as the milestone's ``plan``
record: approved by the owner, or by policy when the review passed and no
card is risky (``approve_plan="auto"``).

The milestone's ``context`` record says what is planned: the goal, the
rows in scope with their readings, whether the repo is empty.
"""

from __future__ import annotations

from typing import Any, Dict, List

from operonx import op

from operonx_project.flows.stage import budget_left, stage_call, stage_request
from operonx_project.plan import cards_from_plan, check_plan, overlaps
from operonx_project.prompts import PLAN_REVIEW_SCHEMA, PLAN_SCHEMA, plan_prompt, plan_review_prompt
from operonx_project.runtime import get_runtime

__all__ = ["draft", "review"]


def _context(rt, mid: str) -> Dict[str, Any]:
    ctx = rt.store.ms_get(mid, "context")
    if ctx is None:
        raise RuntimeError(f"milestone {mid} has no context record: the requirements stage writes it")
    return ctx


@op
async def draft(key: str, mid: str, state: dict, notes: str) -> dict:
    rt = get_runtime(key)
    ms = rt.store.get_milestone(mid)
    ctx = _context(rt, mid)
    st = dict(state or {})
    n = int(st.get("round", 0)) + 1
    feedback = st.get("feedback") or ""
    if notes and n == 1:
        feedback = f"The owner asks for these changes:\n{notes}"
    prompt = plan_prompt(ms.goal, ctx.get("brief", ""), ctx.get("rows") or [], ms.policy.horizon, ms.policy.depth,
                         ms.policy.budget.max_cards, bool(ctx.get("greenfield")), feedback,
                         ctx.get("constraints", ""))  # fmt: skip
    if n > 1 and st.get("session"):
        prompt = f"Revise your plan.\n{feedback}\nAnswer with the whole plan again."
    req = stage_request(rt, mid, "planner", "planner", prompt, rt.git.ensure_integration(mid), f"plan-{n}",
                        schema=PLAN_SCHEMA, session=st.get("session") if n > 1 else None)  # fmt: skip
    res = await stage_call(rt, mid, f"plan-{n}", req)
    if not res.ok or not isinstance(res.output, dict):
        raise RuntimeError(f"the planner ended {res.status}: {res.error or res.text[-300:]}")
    st.update(round=n, plan=res.output, session=res.session, feedback="")
    return {"state": st}


@op
async def review(key: str, mid: str, state: dict) -> dict:
    rt = get_runtime(key)
    ms = rt.store.get_milestone(mid)
    ctx = _context(rt, mid)
    st = dict(state)
    n, plan = int(st["round"]), dict(st["plan"])
    existing = rt.store.cards(mid)  # a replan: done cards stay, every id stays taken
    green = bool(ctx.get("greenfield")) and not any(c.kind == "init" for c in existing)
    scope = [r["id"] for r in ctx.get("rows") or []]
    cards = cards_from_plan(mid, plan.get("cards") or [], plan.get("init") if green else None)
    problems = check_plan(plan, cards, scope, ms.policy, greenfield=green, existing=existing)
    ov = overlaps([*[c for c in existing if c.state.value == "done"], *cards])
    if budget_left(rt, mid) <= 0:  # the plan is paid for: the owner gets it unreviewed, never thrown away
        unreviewed = {"severity": "major", "note": f"not reviewed: the milestone spent ${ms.cost_usd:.2f} of its "
                      f"${ms.policy.budget.max_usd:.2f}; approve it as it is, replan, or retry with more"}  # fmt: skip
        rt.store.note(mid, "plan.budget", {"round": n, "cost_usd": ms.cost_usd, "reviewed": False})
        rt.store.ms_put(mid, "plan", {"plan": plan, "problems": problems, "notes": [unreviewed], "passed": False,
                                      "rounds": n, "overlaps": ov})  # fmt: skip
        return {"state": st, "next": "end"}
    prompt = plan_review_prompt(ms.goal, ctx.get("rows") or [], plan, problems, ov, ms.policy.horizon)
    req = stage_request(rt, mid, "reviewer", "plan_reviewer", prompt, rt.git.ensure_integration(mid),
                        f"plan-review-{n}", schema=PLAN_REVIEW_SCHEMA)  # fmt: skip
    res = await stage_call(rt, mid, f"plan-review-{n}", req)
    if not res.ok or not isinstance(res.output, dict):
        raise RuntimeError(f"the plan review ended {res.status}: {res.error or res.text[-300:]}")
    notes: List[Dict[str, Any]] = [x for x in res.output.get("notes") or [] if isinstance(x, dict)]
    blocking = [x for x in notes if x.get("severity") in ("blocker", "major")]
    passed = not problems and not blocking
    rt.store.note(mid, "plan.review", {"round": n, "problems": problems, "notes": len(notes),
                                       "blocking": len(blocking), "passed": passed})  # fmt: skip
    spent = budget_left(rt, mid) <= 0  # no money for another round: the owner gets the plan with its notes
    if spent and not passed:
        rt.store.note(mid, "plan.budget", {"round": n, "cost_usd": rt.store.get_milestone(mid).cost_usd,
                                           "reviewed": True})  # fmt: skip
    if not passed and n <= ms.policy.budget.plan_reviews and not spent:
        lines = [f"- (engine) {p}" for p in problems]
        for x in blocking:
            on = f", {x['card']}" if x.get("card") else ""
            lines.append(f"- ({x.get('severity')}{on}) {x.get('note')}")
        st["feedback"] = "\n".join(lines)
        return {"state": st, "next": "revise"}
    record = {
        "plan": plan,
        "problems": problems,
        "notes": blocking if not passed else [x for x in notes if x not in blocking],
        "passed": passed,
        "rounds": n,
        "overlaps": ov,
    }
    rt.store.ms_put(mid, "plan", record)
    return {"state": st, "next": "end"}
