import pytest

from operonx_project import Card, CardState, IllegalTransition, Milestone, MilestoneState, Policy, Stage, Store
from operonx_project.store import CARD_MOVES, MILESTONE_MOVES


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / ".operonx" / "project" / "project.db")
    s.add_milestone(Milestone("m1", str(tmp_path), "first release", policy=Policy.w2()))
    return s


def test_every_card_move_in_the_table_is_allowed_and_nothing_else(store):
    states = list(CardState)
    for frm in states:
        for to in states:
            cid = f"{frm.value}-{to.value}"
            store.add_cards([Card(cid, "m1", cid, state=frm)])
            if to in CARD_MOVES[frm]:
                assert store.move_card("m1", cid, to).state == to
            else:
                with pytest.raises(IllegalTransition):
                    store.move_card("m1", cid, to)
                assert store.get_card("m1", cid).state == frm


def test_every_milestone_move_in_the_table_is_allowed_and_nothing_else(store, tmp_path):
    for frm in MilestoneState:
        for to in MilestoneState:
            mid = f"{frm.value}-{to.value}"
            store.add_milestone(Milestone(mid, str(tmp_path), mid, state=frm))
            if to in MILESTONE_MOVES[frm]:
                assert store.move_milestone(mid, to).state == to
            else:
                with pytest.raises(IllegalTransition):
                    store.move_milestone(mid, to)


def test_a_move_writes_its_event_and_values_in_one_write(store):
    store.add_cards([Card("c1", "m1", "CLI", deps=[], rows=["R-01"])])
    store.move_card("m1", "c1", CardState.READY)
    card = store.move_card("m1", "c1", CardState.RUNNING, run_id="run-1")
    assert (card.state, card.stage, card.run_id) == (CardState.RUNNING, Stage.IMPLEMENT, "run-1")
    kinds = [(e["kind"], e["data"].get("to")) for e in store.events("m1")]
    assert ("card.state", "running") in kinds and kinds[0] == ("milestone.created", None)


def test_only_a_running_card_has_a_stage(store):
    store.add_cards([Card("c1", "m1", "x")])
    with pytest.raises(IllegalTransition):
        store.set_stage("m1", "c1", Stage.REVIEW)


def test_state_is_never_set_around_the_table(store):
    store.add_cards([Card("c1", "m1", "x")])
    with pytest.raises(ValueError):
        store.update_card("m1", "c1", state="done")
    with pytest.raises(ValueError):
        store.update_milestone("m1", state="done")


def test_pause_remembers_the_stage_it_left(store):
    store.move_milestone("m1", MilestoneState.PLANNING)
    store.move_milestone("m1", MilestoneState.RUNNING)
    store.move_milestone("m1", MilestoneState.PAUSED, actor="owner")
    assert store.prior_state("m1") == MilestoneState.RUNNING


def test_round_trip_and_a_second_process_sees_it(store, tmp_path):
    store.add_cards(
        [
            Card(
                "c1",
                "m1",
                "x",
                criteria=["When a staged file is a .zip, the hook shall refuse it"],
                paths=["src/**"],
                detail={"q": 1},
            )
        ]
    )
    other = Store(tmp_path / ".operonx" / "project" / "project.db")
    got = other.get_card("m1", "c1")
    assert got.criteria[0].startswith("When") and got.paths == ["src/**"] and got.detail == {"q": 1}
    assert other.get_milestone("m1").policy.start == "pull"


def test_one_daemon_holds_the_lease(store):
    assert store.acquire_lease("a", ttl=30)
    assert not store.acquire_lease("b", ttl=30)
    assert store.acquire_lease("a", ttl=30)  # renew
    store.release_lease("a")
    assert store.acquire_lease("b", ttl=30)


def test_a_stale_lease_is_taken_over(store):
    assert store.acquire_lease("a", ttl=-1)
    assert store.acquire_lease("b", ttl=30)


def test_policy_rejects_unknown_knobs():
    with pytest.raises(ValueError):
        Policy(start="shove")
    assert Policy.w1().horizon == "all" and Policy.w2().depth == 4
