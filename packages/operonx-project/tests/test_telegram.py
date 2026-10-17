"""P6: the owner's questions over Telegram, against FakeBotAPI (a **test double**: a
localhost HTTP server speaking the Bot API subset the bridge uses; real
Telegram is P10).

The board is set up by hand (cards moved to ``waiting_human`` with a
question), except in the first test, where a real daemon parks a card, the
owner taps a button and the card resumes to done.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

import pytest
from operonx import Retry
from operonx.core.loggings import LOGGER

from operonx_project.cli import main as cli
from operonx_project.daemon import Daemon
from operonx_project.interactions import Interactions, label_answer
from operonx_project.messages import callback, parse_callback, render
from operonx_project.model import Card, CardState, Policy
from operonx_project.model import MilestoneState as M
from operonx_project.project import Project, create_milestone
from operonx_project.telegram import FakeBotAPI, TelegramBridge, TelegramError, TelegramMessenger
from tests.flow_env import RIGHT, make_repo
from tests.life_env import Life, base_script

TOKEN = "123456789:AAH-test-token-of-the-fake-bot-0123456789abcd"
OWNER, CHAT = 42, -1004242
OPTIONS = [{"key": "a", "label": "plus"}, {"key": "b", "label": "minus"}]


class Captured(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def logs():
    h, level = Captured(), LOGGER.level
    LOGGER.addHandler(h)
    LOGGER.setLevel(logging.DEBUG)
    yield h
    LOGGER.removeHandler(h)
    LOGGER.setLevel(level)


@pytest.fixture
def fake():
    f = FakeBotAPI(TOKEN)
    yield f
    f.close()


def _transient(e):
    return isinstance(e, TelegramError) and e.transient


def board(tmp_path, cards=("C1",), **question):
    """A milestone running, with *cards* parked on a question each."""
    proj = Project.open(make_repo(tmp_path), worktrees=tmp_path / "wt")
    s = proj.store
    create_milestone(proj, "calc", goal="calc.add adds", mode="w1", policy=Policy.w1(), mid="m1")
    for to in (M.PLANNING, M.RUNNING):
        s.move_milestone("m1", to)
    s.add_cards([Card(c, "m1", f"card {c}", deps=[]) for c in cards])
    for c in cards:
        for to in (CardState.READY, CardState.RUNNING, CardState.WAITING_HUMAN):
            s.move_card("m1", c, to)
        q = {
            "interrupt_id": uuid.uuid4().hex,
            "kind": "human",
            "category": "requirements",
            "problem": f"{c}: plus or minus?",
            "why": "the BRD says neither",
            "options": OPTIONS,
            **question,
        }
        s.update_card("m1", c, detail={"question": q})  # fmt: skip
    return proj


def bridge(proj, fake, **kw):
    inter = Interactions(proj.store, **kw)
    retry = Retry(max_attempts=2, initial=0.01, max_interval=0.05, jitter=False, on=_transient)
    tg = TelegramMessenger(TOKEN, api=fake.url)
    return TelegramBridge(proj.store, inter, tg, CHAT, [OWNER], project_name="calc", retry=retry, resend_after_s=0)


def run(coro):
    return asyncio.run(coro)


async def poll(b):
    await b.poll(timeout=0.2)


def applied(store):
    """Play the daemon: every queued command ran."""
    for c in store.pending_commands():
        store.finish_command(c["seq"], "done")


# ── the whole path, with a real daemon ─────────────────────────────────────
def test_a_button_tap_resumes_the_parked_card_and_the_message_says_so(tmp_path, fake):
    ask = {"output": {"summary": "stuck", "needs_human": {"question": "Plus or minus?", "category": "requirements",
                                                          "options": OPTIONS}}}  # fmt: skip
    life = Life(tmp_path, base_script(implementer=[ask, {"write": {"calc.py": RIGHT}}]),
                policy=Policy.w1(heldout=False, approve_plan="auto"))  # fmt: skip
    b = bridge(life.project, fake)

    def serve():
        run(Daemon(life.life, poll_s=0.02, lease_ttl=1, interactions=b.inter, bridge=b).serve(once=True))

    life.command("start")
    serve()
    assert life.state() == M.WAITING_HUMAN
    [sent] = fake.sent
    assert "Plus or minus?" in sent["text"] and "Q-01" in sent["text"]
    data = sent["buttons"][0][1]  # "minus"
    assert parse_callback(data)[1] == "b"
    fake.tap(OWNER, CHAT, sent["message_id"], data)
    run(poll(b))
    assert "taken" in fake.callbacks[-1]["text"]
    serve()
    assert life.state() == M.DONE
    assert "The owner's decision: minus" in life.coder.of("implementer")[-1]["prompt"]
    edit = fake.edits[-1]
    assert edit["message_id"] == sent["message_id"] and edit["text"].startswith("✅ Answered: b")
    assert "C1 resumed" in edit["text"]


# ── the ways an answer comes in ────────────────────────────────────────────
def test_a_reply_to_the_question_answers_it_in_the_owners_words(tmp_path, fake):
    proj = board(tmp_path)
    b = bridge(proj, fake)
    run(b.sync())
    [sent] = fake.sent
    fake.message(OWNER, CHAT, "b) but warn when the result is negative", reply_to=sent["message_id"])
    run(poll(b))
    [cmd] = proj.store.pending_commands()
    assert cmd["kind"] == "answer" and cmd["card"] == "C1"
    assert cmd["payload"]["answer"] == {"choice": "b", "text": "b) but warn when the result is negative"}
    assert fake.last_to(CHAT)["text"].startswith("✅")


def test_unthreaded_words_answer_the_only_open_question_and_ask_which_when_two_are(tmp_path, fake):
    proj = board(tmp_path, cards=("C1", "C2"))
    b = bridge(proj, fake)
    run(b.sync())
    assert len(fake.sent) == 2
    fake.message(OWNER, CHAT, "plus, obviously")
    run(poll(b))
    assert not proj.store.pending_commands()
    assert "2 questions are open" in fake.last_to(CHAT)["text"]
    # one answered by its reply: the next unthreaded words go to the other one
    fake.message(OWNER, CHAT, "a", reply_to=fake.sent[0]["message_id"])
    fake.message(OWNER, CHAT, "minus")
    run(poll(b))
    cmds = proj.store.pending_commands()
    assert [(c["card"], c["payload"]["answer"]) for c in cmds] == [("C1", {"choice": "a", "text": ""}),
                                                                  ("C2", {"choice": "b", "text": ""})]  # fmt: skip
    assert "Taken as your answer to Q-02 (C2)" in fake.last_to(CHAT)["text"]


def test_a_duplicate_update_and_a_second_answer_change_nothing(tmp_path, fake):
    proj = board(tmp_path)
    b = bridge(proj, fake)
    run(b.sync())
    [sent] = fake.sent
    data = sent["buttons"][0][0]
    uid = fake.tap(OWNER, CHAT, sent["message_id"], data)
    fake.push(
        {
            "update_id": uid,
            "callback_query": {
                "id": "again",
                "from": {"id": OWNER},
                "data": data,
                "message": {"message_id": sent["message_id"], "chat": {"id": CHAT}},
            },
        }
    )  # noqa: E501
    run(poll(b))
    assert len(proj.store.pending_commands()) == 1 and len(fake.callbacks) == 1  # the duplicate is not handled
    fake.message(OWNER, CHAT, "actually b", reply_to=sent["message_id"])
    run(poll(b))
    assert len(proj.store.pending_commands()) == 1
    assert "already answered (a" in fake.last_to(CHAT)["text"]
    # the daemon applies it; the question's message says it was answered
    applied(proj.store)
    run(b.sync())
    assert fake.edits[-1]["text"].startswith("✅ Answered: a")
    n = len(fake.edits)
    run(b.sync())
    assert len(fake.edits) == n  # edited once


def test_an_answer_after_a_replan_is_stale_and_says_what_is_current(tmp_path, fake):
    proj = board(tmp_path)
    s = proj.store
    b = bridge(proj, fake)
    run(b.sync())
    [old] = fake.sent
    # a replan: the card's question is gone, a new plan waits for approval
    s.move_card("m1", "C1", CardState.CANCELLED)
    s.move_milestone("m1", M.PLANNING)
    s.update_milestone("m1", replans=1)
    s.move_milestone("m1", M.AWAITING_APPROVAL)
    s.ms_put("m1", "plan", {"plan": {"cards": [{"id": "C3", "title": "add, again"}]}, "problems": [], "notes": []})
    b.inter.sync()  # the daemon's loop: the plan's question is made, the card's superseded
    fake.tap(OWNER, CHAT, old["message_id"], old["buttons"][0][0])
    run(poll(b))
    assert not s.pending_commands()
    assert "no longer current; the current one: Q-02 (approval)" in fake.callbacks[-1]["text"]
    run(b.sync())
    assert any(e["message_id"] == old["message_id"] and e["text"].startswith("⏹ No longer current") for e in fake.edits)
    plan_msg = fake.last_to(CHAT)
    assert "the plan waits for your approval" in plan_msg["text"] and "C3 add, again" in plan_msg["text"]
    fake.tap(OWNER, CHAT, plan_msg["message_id"], plan_msg["buttons"][0][0])  # Approve
    run(poll(b))
    assert [c["kind"] for c in s.pending_commands()] == ["approve"]


def test_revising_a_plan_needs_words(tmp_path, fake):
    proj = board(tmp_path, cards=())
    s = proj.store
    s.move_milestone("m1", M.PLANNING)
    s.move_milestone("m1", M.AWAITING_APPROVAL)
    s.ms_put("m1", "plan", {"plan": {"cards": []}, "problems": [], "notes": []})
    b = bridge(proj, fake)
    run(b.sync())
    msg = fake.sent[-1]
    fake.tap(OWNER, CHAT, msg["message_id"], msg["buttons"][0][1])  # Revise, by button: no words
    run(poll(b))
    assert not s.pending_commands() and "needs your words" in fake.callbacks[-1]["text"]
    fake.message(OWNER, CHAT, "split C1 in two", reply_to=msg["message_id"])
    run(poll(b))
    [cmd] = s.pending_commands()
    assert cmd["kind"] == "replan" and cmd["payload"]["notes"] == "split C1 in two"


# ── time ───────────────────────────────────────────────────────────────────
def test_expiry_takes_the_safe_default_or_flags_the_question(tmp_path, fake):
    clock = [time.time()]
    proj = board(tmp_path, cards=("C1", "C2"), recommended="a")
    proj.store.update_card("m1", "C1", detail={"question": {**proj.store.get_card("m1", "C1").detail["question"],
                                                           "safe_default": True}})  # fmt: skip
    b = bridge(proj, fake, remind_after_s=10, expire_after_s=20, now=lambda: clock[0])
    run(b.sync())
    first = list(fake.sent)
    clock[0] += 11
    run(b.sync())
    reminders = [m for m in fake.sent if m["text"].startswith("⏰")]
    assert len(reminders) == 2 and reminders[0]["reply_parameters"]["message_id"] == first[0]["message_id"]
    clock[0] += 10
    run(b.sync())
    [cmd] = proj.store.pending_commands()
    assert cmd["card"] == "C1" and cmd["payload"]["answer"] == {"choice": "a", "text": ""}
    assert proj.store.get_interaction(cmd["payload"]["interaction"])["answered_by"] == "default"
    texts = [m["text"] for m in fake.sent]
    assert any("took the safe default (a)" in t for t in texts)
    assert any("Q-02 expired with no safe default" in t for t in texts)
    clock[0] += 100
    run(b.sync())  # flagged once, not again
    assert sum("expired with no safe default" in m["text"] for m in fake.sent) == 1
    from operonx_project.cli import status_data

    assert status_data(proj)[0]["flagged"] == ["Q-02"]


# ── who may answer ─────────────────────────────────────────────────────────
def test_an_unauthorized_user_or_chat_is_dropped_silently(tmp_path, fake, logs):
    proj = board(tmp_path)
    b = bridge(proj, fake)
    run(b.sync())
    [sent] = fake.sent
    fake.message(999, CHAT, "secret words of a stranger", reply_to=sent["message_id"])
    fake.tap(999, CHAT, sent["message_id"], sent["buttons"][0][0])
    fake.message(OWNER, 31337, "a")  # the owner, in another chat
    run(poll(b))
    assert not proj.store.pending_commands()
    assert len(fake.sent) == 1 and not fake.callbacks  # no reply at all
    assert "dropped an update from user 999" in logs.text and "secret words" not in logs.text


def test_cancel_asks_for_a_confirming_tap(tmp_path, fake):
    proj = board(tmp_path)
    b = bridge(proj, fake)
    fake.message(OWNER, CHAT, "/cancel m1")
    run(poll(b))
    ask = fake.last_to(CHAT)
    assert "Cancel m1" in ask["text"] and not proj.store.pending_commands()
    yes = ask["buttons"][0][0]
    assert yes.startswith("c:")
    fake.tap(OWNER, CHAT, ask["message_id"], yes)
    run(poll(b))
    [cmd] = proj.store.pending_commands()
    assert cmd["kind"] == "cancel" and cmd["actor"] == f"telegram:{OWNER}"
    fake.tap(OWNER, CHAT, ask["message_id"], yes)  # a second tap: used up
    run(poll(b))
    assert len(proj.store.pending_commands()) == 1 and "expired" in fake.callbacks[-1]["text"]
    # "keep it" queues nothing
    fake.message(OWNER, CHAT, "/cancel m1")
    run(poll(b))
    keep = fake.last_to(CHAT)
    fake.tap(OWNER, CHAT, keep["message_id"], keep["buttons"][0][1])
    run(poll(b))
    assert len(proj.store.pending_commands()) == 1


def test_the_other_commands(tmp_path, fake):
    proj = board(tmp_path)
    b = bridge(proj, fake)
    run(b.sync())  # the daemon's loop: the question is open (and sent)
    fake.sent.clear()
    for text in ("/status", "/pending", "/milestone m1", "/pause", "/resume", "/budget m1 +5", "/what"):
        fake.message(OWNER, CHAT, text)
    run(poll(b))
    texts = [m["text"] for m in fake.sent]
    assert texts[0].startswith("m1 · running · calc — 0/1 cards")
    assert "Q-01 · m1/C1 — C1: plus or minus?" in texts[1]
    assert "• C1 waiting_human" in texts[2]
    assert [c["kind"] for c in proj.store.pending_commands()] == ["pause", "unpause", "retry"]
    assert proj.store.pending_commands()[-1]["payload"] == {"usd": 5.0}
    assert texts[-1].startswith("/status")  # an unknown command: the help


# ── when Telegram fails ────────────────────────────────────────────────────
def test_a_failed_send_is_retried_and_the_cli_answers_meanwhile(tmp_path, fake, logs):
    proj = board(tmp_path, problem=f"the token {TOKEN} must never show")
    b = bridge(proj, fake)
    fake.fail_next(1)  # transient, retried at once
    run(b.sync())
    assert (
        len(fake.sent) == 1
        and TOKEN not in fake.sent[0]["text"]
        and "[redacted:telegram-token]" in fake.sent[0]["text"]
    )
    proj2 = board(tmp_path / "two")
    b2 = bridge(proj2, fake)
    fake.fail_next(2)  # both attempts fail: it waits in the store
    run(b2.sync())
    [inter] = proj2.store.interactions()
    assert inter["delivery"] == "failed" and len(fake.sent) == 1
    assert cli(["pending", "--repo", str(proj2.repo)]) == 0
    assert cli(["answer", "--repo", str(proj2.repo), "m1", "Q-01", "b"]) == 0
    [cmd] = proj2.store.pending_commands()
    assert cmd["payload"]["answer"] == {"choice": "b", "text": ""}
    assert cli(["answer", "--repo", str(proj2.repo), "m1", "C1", "a"]) == 1  # already answered
    applied(proj2.store)
    run(b2.sync())  # nothing was ever sent for it: nothing to edit, nothing to resend
    assert len(fake.sent) == 1
    for text in (logs.text, "\n".join(str(e) for e in proj2.store.events("m1"))):
        assert TOKEN not in text and TOKEN.split(":")[1] not in text


def test_a_wrong_token_is_a_permanent_error(fake):
    tg = TelegramMessenger("1:" + "x" * 40, api=fake.url)
    with pytest.raises(TelegramError) as err:
        run(tg.send(CHAT, "hi"))
    assert err.value.code == 401 and not err.value.transient
    assert "x" * 40 not in str(err.value) and "x" * 40 not in repr(tg)


# ── the pieces ─────────────────────────────────────────────────────────────
def test_label_answer_and_callback_data():
    assert label_answer(OPTIONS, "B") == ("b", "")
    assert label_answer(OPTIONS, "a: and log it") == ("a", "a: and log it")
    assert label_answer(OPTIONS, "Minus") == ("b", "")
    assert label_answer(OPTIONS, "an entirely new idea") == (None, "an entirely new idea")
    data = callback("0123abcd", "keep", 12)
    assert data == "a:0123abcd:keep:12" and parse_callback(data) == ("0123abcd", "keep", 12)
    assert parse_callback("a:nothex!!:x:1") is None
    with pytest.raises(ValueError):
        callback("0123abcd", "k" * 24, 10**40)


def test_a_long_question_is_cut_to_telegrams_limit():
    inter = {"milestone": "m1", "card": "C1", "qid": "Q-01", "kind": "card", "short": "0123abcd", "version": 1,
             "payload": {"problem": "x" * 9000, "options": OPTIONS, "recommended": "a"}}  # fmt: skip
    text, buttons = render(inter, "calc")
    assert len(text) <= 4096 and text.endswith("operonx project show m1 C1)")
    assert [label for label, _ in buttons[0]] == ["plus (recommended)", "minus"]


def test_the_cli_answers_a_card_whose_question_the_daemon_has_not_opened_yet(tmp_path):
    proj = board(tmp_path)  # the card waits; no daemon loop has run, so there is no interaction yet
    assert not proj.store.interactions()
    assert cli(["answer", "--repo", str(proj.repo), "m1", "C1", "b"]) == 0
    [cmd] = proj.store.pending_commands()
    assert (cmd["kind"], cmd["card"], cmd["payload"]) == ("answer", "C1", {"answer": "b"})


# ── the studio's door: operonx-project api (P8) ────────────────────────────
def _api(proj, req) -> dict:
    import io
    import json
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli(["api", "--repo", str(proj.repo), json.dumps(req)])
    out = json.loads(buf.getvalue())
    assert code == (0 if out["ok"] else 1)
    return out


def test_the_api_answers_with_the_same_rules(tmp_path):
    proj = board(tmp_path, cards=("C1", "C2"))
    Interactions(proj.store).sync()
    got = _api(proj, {"op": "answer", "milestone": "m1", "ref": "Q-01", "answer": "b"})
    assert got["ok"] and got["seq"]
    again = _api(proj, {"op": "answer", "milestone": "m1", "ref": "Q-01", "answer": "a"})
    assert not again["ok"] and again["status"] == "already"
    # C2 moves on (a replan cancelled it): its question is stale, and the answer says what is current
    proj.store.move_card("m1", "C2", CardState.CANCELLED)
    stale = _api(proj, {"op": "answer", "milestone": "m1", "ref": "C2", "answer": "a"})
    assert stale["status"] == "stale" and not stale["ok"]
    assert [c["kind"] for c in proj.store.pending_commands()] == ["answer"]


def test_the_api_queues_the_rest_and_moves_a_card_not_started(tmp_path):
    proj = board(tmp_path, cards=("C1",))
    proj.store.add_cards([Card("C9", "m1", "later")])
    assert _api(proj, {"op": "pause", "milestone": "m1"})["status"] == "queued"
    assert _api(proj, {"op": "retry", "milestone": "m1", "hours": 2})["ok"]
    assert proj.store.pending_commands()[-1]["payload"] == {"hours": 2.0}
    assert not _api(proj, {"op": "replan", "milestone": "m1", "notes": " "})["ok"]
    assert _api(proj, {"op": "prioritize", "milestone": "m1", "card": "C9", "priority": -5})["ok"]
    assert proj.store.get_card("m1", "C9").priority == -5
    assert _api(proj, {"op": "prioritize", "milestone": "m1", "card": "C1", "priority": 1})["status"] == "refused"
    assert _api(proj, {"op": "nope", "milestone": "m1"})["status"] == "invalid"
    assert _api(proj, {"op": "pause", "milestone": "m7"})["status"] == "unknown"
