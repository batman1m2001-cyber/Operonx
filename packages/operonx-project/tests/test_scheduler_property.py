"""P1's gate: on random DAGs, under both policies, the scheduler keeps its rules.

- a card never starts before its dependencies are done;
- no more than max_parallel cards run, and overlapping paths never run together
  (both checked by ScriptedRunner at every start);
- every card ends done, failed, blocked (naming why), or waiting for the owner;
- the loop's step count is bounded by the work it did.
"""

import asyncio
import tempfile
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from operonx_project import Card, CardState, Milestone, Policy, Store
from operonx_project.dag import paths_overlap
from operonx_project.scheduler import CardOutcome, Outcome, Scheduler

from .fakes import ScriptedIntegrator, ScriptedRunner

PATHS = ["src/a/*", "src/b/*", "src/a/x.py", "docs/*", "tests/*"]
OUTCOMES = ["verified", "verified", "verified", "failed", "ask"]


@st.composite
def plans(draw):
    n = draw(st.integers(1, 9))
    cards = []
    for i in range(n):
        deps = draw(st.lists(st.integers(0, i - 1), unique=True, max_size=3)) if i else []
        cards.append(
            dict(
                id=f"c{i}",
                deps=[f"c{d}" for d in deps],
                paths=draw(st.lists(st.sampled_from(PATHS), min_size=1, max_size=2, unique=True)),
                priority=draw(st.integers(0, 3)),
                script=draw(st.lists(st.sampled_from(OUTCOMES), min_size=1, max_size=3)),
                integ=draw(st.lists(st.sampled_from(["merged", "merged", "conflict"]), min_size=1, max_size=3)),
                dur=draw(st.sampled_from([0, 0, 0.001, 0.003])),
            )
        )
    policy = draw(st.sampled_from(["w1", "w2"]))
    par = draw(st.integers(1, 3))
    return cards, policy, par


def outcome(step):
    if step == "ask":
        return CardOutcome("waiting_human", question={"q": "?"}, cost_usd=0.1)
    return CardOutcome(step, cost_usd=0.1)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(plans())
def test_scheduler_rules_hold_on_random_dags(plan):
    cards, pol_name, par = plan
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "p.db")
        pol = getattr(Policy, pol_name)(max_parallel=par, wip={"review": 1, "qa": 2})
        store.add_milestone(Milestone("m", tmp, "m", policy=pol))
        store.add_cards(
            [Card(c["id"], "m", c["id"], deps=c["deps"], paths=c["paths"], priority=c["priority"]) for c in cards]
        )
        runner = ScriptedRunner(
            store,
            "m",
            {c["id"]: [outcome(s) for s in c["script"]] for c in cards},
            {c["id"]: c["dur"] for c in cards},
            max_parallel=par,
        )
        integ = ScriptedIntegrator({c["id"]: c["integ"] for c in cards})
        sched = Scheduler(store, "m", runner, integ)
        result = asyncio.run(sched.run())

        final = {c.id: c for c in store.cards("m")}
        allowed = {CardState.DONE, CardState.FAILED, CardState.BLOCKED, CardState.WAITING_HUMAN}
        waiting = [c for c in final.values() if c.state == CardState.WAITING_HUMAN]
        for c in final.values():
            if c.state == CardState.READY:
                # only when a parked card holds overlapping paths (its work is not merged yet)
                assert any(paths_overlap(c.paths, w.paths) for w in waiting), c.id
            elif c.state == CardState.PENDING:
                # only behind a dependency that waits for the owner, directly or not
                assert any(
                    final[d].state in (CardState.WAITING_HUMAN, CardState.PENDING, CardState.READY) for d in c.deps
                ), c.id
            else:
                assert c.state in allowed, (c.id, c.state)
        for c in final.values():
            if c.state == CardState.BLOCKED:
                dep = c.reason.split(" is ")[0]
                assert final[dep].state in (CardState.FAILED, CardState.BLOCKED, CardState.CANCELLED)
            if c.state == CardState.DONE:
                assert all(final[d].state == CardState.DONE for d in c.deps)
        if all(c.state == CardState.DONE for c in final.values()):
            assert result == Outcome.COMPLETE
        elif any(c.state == CardState.WAITING_HUMAN for c in final.values()):
            assert result == Outcome.WAITING
        else:
            assert result == Outcome.STUCK
        work = sched.stats.starts + sched.stats.resumes + sched.stats.integrations
        assert sched.stats.steps <= 2 * work + len(cards) + 2
