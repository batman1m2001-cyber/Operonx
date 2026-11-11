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


def test_policy_rejects_unknown_knobs():
    with pytest.raises(ValueError):
        Policy(start="shove")
    assert Policy.w1().horizon == "all" and Policy.w2().depth == 4
