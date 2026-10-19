"""The decision table (plan §7), row by row."""

from __future__ import annotations

import pytest

from operonx_project.decide import Decision, RoundFacts, decide, question_fingerprint


def facts(**kw):
    return RoundFacts(**{"round": 1, "max_rounds": 6, **kw})


def test_clean_round_is_verified():
    assert decide(facts()).next == "verified"
    assert "held-out pass" in decide(facts(heldout_ok=True)).reason


@pytest.mark.parametrize(
    "status,expect", [("rate_limited", "requeue"), ("cancelled", "requeue"), ("infra", "failed"), ("auth", "ask")]
)
def test_coder_status_rows(status, expect):
    d = decide(facts(coder_status=status, resets_at=123.0, checks_ok=False))
    assert d.next == expect
    if status == "rate_limited":
        assert d.not_before == 123.0
    if status == "auth":
        assert d.question["category"] == "access"


def test_needs_human_answered_by_triage_is_a_fix():
    d = decide(facts(needs_human={"question": "which?"}, triage={"action": "answer", "answer": "this"}))
    assert d.next == "fix" and "Answer (decided from evidence): this" in d.feedback


def test_needs_human_escalated_is_ask():
    t = {"action": "escalate", "category": "tradeoff", "message": {"problem": "p", "why": "w", "options": [{}]}}
    d = decide(facts(needs_human={"question": "which?"}, triage=t))
    assert d.next == "ask" and d.question["category"] == "tradeoff" and d.question["problem"] == "p"


def test_guard_blocker_is_a_fix():
    d = decide(facts(guard_blockers=["x is protected"]))
    assert d.next == "fix" and "x is protected" in d.feedback


def test_empty_diff_retries_once_then_fails():
    d = decide(facts(empty_diff=True))
    assert d.next == "fix" and d.counts_empty
    assert decide(facts(empty_diff=True, empty_retries=1)).next == "failed"
    assert decide(facts(empty_diff=True, kind="docs")).next == "fix"  # tipcalc m1/C6: an unchanged docs card
    assert decide(facts(empty_diff=True, kind="init")).next == "verified"


def test_new_failure_is_a_fix():
    d = decide(facts(checks_ok=False, new_failures=["tests"], failure_tail="boom", same_failure=1))
    assert d.next == "fix" and "boom" in d.feedback


def test_same_failure_three_times_revises_once_then_asks():
    f = dict(checks_ok=False, new_failures=["tests"], same_failure=3)
    assert decide(facts(**f)).next == "revise"
    d = decide(facts(**f, revised=True))
    assert d.next == "ask" and d.question["category"] == "repeated_failure"


def test_findings_are_a_fix_and_upheld_ones_ask():
    fs = [{"severity": "major", "file": "a.py", "line": 1, "title": "t"}]
    assert decide(facts(findings=fs)).next == "fix"
    d = decide(facts(findings=fs, upheld=fs))
    assert d.next == "ask" and {o["key"] for o in d.question["options"]} == {"fix", "waive", "stop"}


def test_heldout_failure_is_a_fix_with_names_only():
    d = decide(facts(heldout_ok=False, heldout_failures=["test_x: should do y"]))
    assert d.next == "fix" and "test_x: should do y" in d.feedback


@pytest.mark.parametrize("row", [
    dict(checks_ok=False, new_failures=["t"], same_failure=1),
    dict(findings=[{"severity": "major", "file": "a", "line": 1, "title": "t"}]),
    dict(heldout_ok=False, heldout_failures=["x"]),
    dict(guard_blockers=["b"]),
    dict(checks_ok=False, new_failures=["t"], same_failure=3),  # even a revision
])  # fmt: skip
def test_every_fix_stops_at_max_rounds_and_budget(row):
    d = decide(facts(round=6, **row))
    assert d.next == "ask" and d.question["category"] == "repeated_failure"
    assert {o["key"] for o in d.question["options"]} == {"keep", "skip", "stop"}
    d = decide(facts(round=2, cost_usd=5.0, card_max_usd=5.0, **row))
    assert d.next == "ask" and d.question["category"] == "budget"


def test_verified_wins_at_the_last_round():
    assert decide(facts(round=6)).next == "verified"


def test_question_fingerprint_ignores_case_and_spacing():
    assert question_fingerprint("C1", "r", "Which  File?") == question_fingerprint("C1", "r", "which file?")
    assert question_fingerprint("C1", "r", "a") != question_fingerprint("C2", "r", "a")


def test_unknown_decision_raises():
    with pytest.raises(ValueError):
        Decision("maybe", "")


def test_escalated_options_always_have_a_key_and_a_label():
    # tipcalc rebuild/C3: triage sent {"option": "A: ..."}; the Board drew "undefined" buttons that answered nothing
    msg = {
        "problem": "the tests expect a raise",
        "recommended": "A: replace the tests",
        "options": [{"option": "A (recommended): rewrite the tests"}, {"option": "B: keep them"}, "C: a helper"],
    }
    d = decide(facts(needs_human={"question": "which?"}, triage={"action": "escalate", "message": msg}))
    q = d.question
    assert [(o["key"], o["label"]) for o in q["options"]] == [
        ("option_1", "A (recommended): rewrite the tests"),
        ("option_2", "B: keep them"),
        ("option_3", "C: a helper"),
    ]
    assert q["recommended"] == "option_1"
    keyed = {**msg, "options": [{"key": "keep", "label": "Keep"}], "recommended": "keep"}
    q = decide(facts(needs_human={"question": "which?"}, triage={"action": "escalate", "message": keyed})).question
    assert q["options"][0]["key"] == "keep" and q["recommended"] == "keep"
