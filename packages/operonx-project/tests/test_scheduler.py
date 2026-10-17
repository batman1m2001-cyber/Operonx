import asyncio
import time

from operonx_project import Card, CardState, Milestone, Policy, Store
from operonx_project.board import column_of, needs_replenish
from operonx_project.scheduler import CardOutcome, Outcome, Scheduler

from .fakes import ScriptedIntegrator, ScriptedRunner


def setup(tmp_path, spec, policy=None, scripts=None, durations=None, integ=None):
    store = Store(tmp_path / "project.db")
    pol = policy or Policy.w2()
    store.add_milestone(Milestone("m", str(tmp_path), "m", policy=pol))
    store.add_cards([Card(cid, "m", cid, **kw) for cid, kw in spec.items()])
    runner = ScriptedRunner(store, "m", scripts or {}, durations, max_parallel=pol.max_parallel)
    integrator = ScriptedIntegrator(integ)
    return store, runner, integrator, Scheduler(store, "m", runner, integrator)


def states(store):
    return {c.id: c.state.value for c in store.cards("m")}


async def test_a_dag_runs_in_dependency_order_and_completes(tmp_path):
    spec = {
        "c1": dict(paths=["a/*"]),
        "c2": dict(deps=["c1"], paths=["b/*"]),
        "c3": dict(deps=["c1"], paths=["c/*"]),
        "c4": dict(deps=["c2", "c3"], paths=["d/*"]),
    }
    store, runner, integ, sched = setup(tmp_path, spec, durations={"c2": 0.05, "c3": 0.05})
    assert await sched.run() == Outcome.COMPLETE
    assert set(states(store).values()) == {"done"}
    assert runner.started[0] == "c1" and runner.started[-1] == "c4"
    assert sched.stats.max_running == 2  # c2 and c3 ran together


async def test_a_failure_blocks_only_what_depends_on_it(tmp_path):
    spec = {"c1": {}, "c2": dict(deps=["c1"]), "c3": dict(deps=["c2"]), "c4": {}}
    store, runner, integ, sched = setup(tmp_path, spec, scripts={"c1": ["failed"]})
    assert await sched.run() == Outcome.STUCK
    s = {c.id: c for c in store.cards("m")}
    assert s["c1"].state == CardState.FAILED and s["c4"].state == CardState.DONE
    assert s["c2"].state == CardState.BLOCKED and s["c2"].reason == "c1 is failed"
    assert s["c3"].state == CardState.BLOCKED and s["c3"].reason == "c2 is blocked"


async def test_a_question_parks_the_card_and_independent_work_continues(tmp_path):
    spec = {"c1": {}, "c4": dict(paths=["office/*"]), "c6": dict(deps=["c4"]), "c2": dict(paths=["arch/*"])}
    asked = CardOutcome("waiting_human", question={"q": "allowlist or block?"})
    store, runner, integ, sched = setup(tmp_path, spec, scripts={"c4": [asked, "verified"]})
    assert await sched.run() == Outcome.WAITING
    s = states(store)
    assert s["c4"] == "waiting_human" and s["c6"] == "pending" and s["c1"] == s["c2"] == "done"
    assert store.get_card("m", "c4").detail["question"] == {"q": "allowlist or block?"}

    # later (a new scheduler, as after a restart): the answer resumes the same card
    sched2 = Scheduler(store, "m", runner, integ)
    sched2.answer("c4", "A, but warn")
    assert await sched2.run() == Outcome.COMPLETE
    assert runner.answers["c4"] == ["A, but warn"] and runner.runs["c4"] == 2
    assert set(states(store).values()) == {"done"}


async def test_an_answer_for_a_card_not_waiting_is_ignored(tmp_path):
    store, runner, integ, sched = setup(tmp_path, {"c1": {}})
    sched.answer("c1", "yes")
    assert await sched.run() == Outcome.COMPLETE
    assert "ignored" in sched.stats.log[0] and "c1" not in runner.answers


async def test_overlapping_paths_never_run_together(tmp_path):
    spec = {"a": dict(paths=["src/x/**"]), "b": dict(paths=["src/x/y.py"]), "c": dict(paths=["docs/*"])}
    store, runner, integ, sched = setup(tmp_path, spec, durations={k: 0.03 for k in spec})
    assert await sched.run() == Outcome.COMPLETE  # ScriptedRunner raises on a violation


async def test_pull_waits_for_review_and_qa_before_starting(tmp_path):
    pol = Policy.w2(max_parallel=3, wip={"review": 1, "qa": 1})
    spec = {f"c{i}": dict(paths=[f"p{i}/*"]) for i in range(4)}
    store, runner, integ, sched = setup(tmp_path, spec, policy=pol, durations={k: 0.04 for k in spec})
    assert await sched.run() == Outcome.COMPLETE
    pol_push = Policy.w1(max_parallel=3)
    assert pol_push.start == "push"


async def test_a_conflict_reopens_the_card_then_it_fails_after_max_reopens(tmp_path):
    store, runner, integ, sched = setup(
        tmp_path, {"c1": {}, "c2": {}}, integ={"c1": ["conflict", "merged"], "c2": ["regression"] * 5}
    )
    assert await sched.run() == Outcome.STUCK
    c1, c2 = store.get_card("m", "c1"), store.get_card("m", "c2")
    assert c1.state == CardState.DONE and c1.reopens == 1 and runner.runs["c1"] == 2
    assert c1.detail["reopen"]["why"] == "conflict"
    assert c2.state == CardState.FAILED and c2.reopens == 2 and "regression after 2 re-opens" in c2.reason


async def test_a_runner_bug_fails_the_card_not_the_milestone(tmp_path):
    def boom(card, answer):
        raise RuntimeError("disk full")

    store, runner, integ, sched = setup(tmp_path, {"c1": {}, "c2": {}}, scripts={"c1": [boom]})
    assert await sched.run() == Outcome.STUCK
    assert store.get_card("m", "c1").reason == "the runner raised RuntimeError: disk full"
    assert store.get_card("m", "c2").state == CardState.DONE


async def test_cancel_stops_running_cards_and_keeps_done_ones(tmp_path):
    spec = {"c1": {}, "c2": dict(deps=["c1"], paths=["x/*"]), "c3": dict(paths=["y/*"])}
    store, runner, integ, sched = setup(tmp_path, spec, durations={"c2": 5, "c3": 5})
    task = asyncio.ensure_future(sched.run())
    while store.get_card("m", "c2").state != CardState.RUNNING:
        await asyncio.sleep(0.01)
    sched.cancel()
    assert await asyncio.wait_for(task, 2) == Outcome.CANCELLED
    s = states(store)
    assert s == {"c1": "done", "c2": "cancelled", "c3": "cancelled"}


async def test_pause_lets_running_cards_finish_and_starts_nothing(tmp_path):
    spec = {"c1": dict(paths=["a/*"]), "c2": dict(deps=["c1"])}
    store, runner, integ, sched = setup(tmp_path, spec, durations={"c1": 0.1})
    task = asyncio.ensure_future(sched.run())
    await asyncio.sleep(0.02)
    sched.pause()
    assert await task == Outcome.PAUSED
    assert states(store) == {"c1": "done", "c2": "ready"}


async def test_the_budget_stops_new_starts(tmp_path):
    pol = Policy.w2(max_parallel=1)
    pol.budget.max_usd = 1.0  # each scripted run costs 0.5
    store, runner, integ, sched = setup(tmp_path, {f"c{i}": {} for i in range(4)}, policy=pol)
    assert await sched.run() == Outcome.BUDGET
    assert sorted(states(store).values()) == ["done", "done", "ready", "ready"]


async def test_a_rate_limit_holds_new_starts_until_its_reset_by_a_timer(tmp_path):
    hold = CardOutcome("requeue", "rate limited", not_before=time.time() + 0.3)
    store, runner, integ, sched = setup(tmp_path, {"c1": {}}, scripts={"c1": [hold, "verified"]})
    assert await sched.run() == Outcome.COMPLETE
    assert time.time() >= hold.not_before - 0.02 and runner.runs["c1"] == 2  # not before the reset (clock slack)
    assert sched.stats.steps < 20  # it slept on the timer, it did not spin


async def test_the_replenisher_tops_up_ready_and_stops_with_no_rows_left(tmp_path):
    rows = [f"R-{i:02d}" for i in range(7)]
    made = []

    async def replenish(cards, n):
        out = []
        for _ in range(n):
            if not rows:
                break
            r = rows.pop(0)
            out.append(Card(f"c-{r}", "m", r, rows=[r], paths=[f"{r}/*"]))
        made.append(len(out))
        return out

    store = Store(tmp_path / "project.db")
    store.add_milestone(Milestone("m", str(tmp_path), "m", policy=Policy.w2(max_parallel=2)))
    runner, integ = ScriptedRunner(store, "m", {}), ScriptedIntegrator()
    sched = Scheduler(store, "m", runner, integ, replenisher=replenish, rows_left=lambda: len(rows))
    assert await sched.run() == Outcome.COMPLETE
    assert len(store.cards("m")) == 7 and made[0] == 4  # first top-up = ready_depth (2 × 2)


def test_columns_and_replenish_need():
    c = Card("c", "m", "c", state=CardState.RUNNING, stage="review")
    assert column_of(c) == "review"
    assert column_of(Card("v", "m", "v", state=CardState.VERIFIED)) == "qa"
    assert needs_replenish([], Policy.w1(), rows_left=5) == 0  # W1 plans everything up front
    assert needs_replenish([], Policy.w2(), rows_left=5) == 4
