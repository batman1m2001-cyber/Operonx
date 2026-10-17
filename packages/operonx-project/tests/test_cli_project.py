"""P5: the milestone commands. ``status`` and ``show`` say what the store says;
the other commands only queue, and the daemon refuses what does not apply."""

from __future__ import annotations

import asyncio
import io
import json
from contextlib import redirect_stdout

import pytest

from operonx_project.board import column_of
from operonx_project.cli import main
from operonx_project.daemon import Daemon, DaemonBusy
from operonx_project.model import MilestoneState as M
from tests.life_env import Life, base_script


def run(*argv) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main([str(a) for a in argv])
    assert code == 0, buf.getvalue()
    return buf.getvalue()


def test_status_and_show_match_the_store(tmp_path):
    life = Life(tmp_path, base_script(), mode="w1")
    repo = life.repo
    assert "queued approve" in run("approve", "m1", "--repo", repo)  # queued, refused later: nothing to approve
    life.command("start")
    assert life.serve() == M.AWAITING_APPROVAL
    text = run("status", "--repo", repo)
    assert "m1  awaiting_approval" in text and "the plan waits for the owner" in text
    run("approve", "m1", "--repo", repo)
    assert life.serve() == M.DONE
    store = life.store
    ms = store.get_milestone("m1")
    [st] = json.loads(run("status", "--repo", repo, "--json"))
    cards = store.cards("m1")
    assert st["state"] == ms.state.value == "done" and st["cost_usd"] == round(ms.cost_usd, 4) > 0
    assert st["cards"] == len(cards) and st["columns"]["done"] == sum(column_of(c) == "done" for c in cards)
    shown = json.loads(run("show", "m1", "--repo", repo, "--json", "--events", "1000"))
    assert [(c["id"], c["state"], c["cost_usd"]) for c in shown["cards"]] == [
        (c.id, c.state.value, round(c.cost_usd, 4)) for c in cards
    ]
    assert [e["seq"] for e in shown["events"]] == [e["seq"] for e in store.events("m1", limit=1000)]
    card = json.loads(run("show", "m1", "C1", "--repo", repo, "--json"))
    c1 = store.get_card("m1", "C1")
    assert (card["state"], card["run_id"], card["reopens"]) == (c1.state.value, c1.run_id, c1.reopens)
    assert card["detail"]["heldout"] == {"files": 1}  # how many, never the tests themselves
    assert "C1     done" in run("show", "m1", "--repo", repo)
    # the early approve was refused, and said why
    refused = [tuple(r) for r in store._db.execute("SELECT kind, status FROM commands").fetchall()]
    assert refused[0] == ("approve", "refused")


def test_start_queues_a_milestone_and_new_makes_an_empty_project(tmp_path):
    out = run("new", tmp_path / "fresh", "--name", "fresh")
    assert "docs/brd/" in out and (tmp_path / "fresh" / "docs" / "brd" / "README.md").exists()
    run("start", "--repo", tmp_path / "fresh", "--title", "first", "--goal", "a greeter", "--mode", "w1",
        "--auto-approve", "--max-usd", "3")  # fmt: skip
    from operonx_project.project import Project

    p = Project.open(tmp_path / "fresh")
    [ms] = p.store.milestones()
    assert (ms.id, ms.state, ms.policy.horizon, ms.policy.approve_plan, ms.policy.budget.max_usd) == (
        "m1", M.CREATED, "all", "auto", 3.0)  # fmt: skip
    assert [c["kind"] for c in p.store.pending_commands()] == ["start"]
    # no card waits on a question: the answer is refused here, not queued for the daemon to refuse
    assert main(["answer", "m1", "C1", "a", "--text", "and warn", "--repo", str(tmp_path / "fresh")]) == 1
    assert [c["kind"] for c in p.store.pending_commands()] == ["start"]


def test_one_daemon_per_project(tmp_path):
    life = Life(tmp_path, base_script())
    assert life.store.acquire_lease("someone-else", ttl=30)
    with pytest.raises(DaemonBusy):
        asyncio.run(Daemon(life.life).serve(once=True))
    life.store.release_lease("someone-else")
    asyncio.run(Daemon(life.life).serve(once=True))  # free again: nothing to drive, it returns
