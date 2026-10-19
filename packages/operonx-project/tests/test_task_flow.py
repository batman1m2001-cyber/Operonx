"""P3 gate: task_flow with a scripted coder (plan §14, P3)."""

from __future__ import annotations

from tests.flow_env import ABS, RIGHT, WRONG, Env, impl


async def test_card_passes(tmp_path):
    env = Env(tmp_path, {"implementer": [impl(RIGHT)]})
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "verified", out
    assert [c["role"] for c in env.coder.calls] == ["test_author", "examiner", "implementer", "reviewer"]
    rounds = env.rounds()
    assert len(rounds) == 1 and rounds[0]["heldout"] is True
    card = env.card()
    assert card.detail["acceptance"]["files"] and card.detail["heldout"]["files"]
    assert card.cost_usd > 0
    # the implementer and the reviewer never get the held-out directory
    for c in env.coder.calls:
        if c["role"] != "examiner":
            assert str(env.rt.heldout_root) in c["hide"]


async def test_fails_then_fixes_in_the_same_session(tmp_path):
    env = Env(tmp_path, {"implementer": [impl(WRONG + "# try\n"), impl(RIGHT)]})
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "verified"
    first, second = env.coder.of("implementer")
    assert second["session_in"] == first["session"]  # resumed, not a new session
    assert "These checks fail" in second["prompt"] and "acceptance" in second["prompt"]
    assert "Acceptance criteria" not in second["prompt"]  # only the new information
    assert [r["next"] for r in env.rounds()] == ["fix", "verified"]
    assert env.stages[:3] == ["implement", "qa", "implement"]


def _finding(**kw):
    f = {"severity": "major", "file": "calc.py", "line": 2, "title": "add has no docstring", "detail": "say why"}
    f.update(kw)
    return f


async def test_review_findings_lead_to_a_fix(tmp_path):
    unverified = _finding(file="other.py", title="other.py is wrong")
    env = Env(
        tmp_path,
        {
            "implementer": [impl(RIGHT), impl('def add(a, b):\n    """Add."""\n    return a + b\n')],
            "reviewer": [{"output": {"findings": [_finding(), unverified]}}, {"output": {"findings": [unverified]}}],
        },
    )
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "verified"
    second = env.coder.of("implementer")[1]["prompt"]
    assert "add has no docstring" in second and "other.py" not in second  # unverified: does not block
    review2 = env.coder.of("reviewer")[1]["prompt"]
    assert "still open from the previous review" in review2
    assert [(r["next"], r["findings"]) for r in env.rounds()] == [("fix", 1), ("verified", 0)]


async def test_repeated_finding_goes_to_arbitration_then_the_owner(tmp_path):
    f = _finding()
    env = Env(
        tmp_path,
        {
            "implementer": [impl(RIGHT), impl(RIGHT + "# tweak\n")],
            "reviewer": [{"output": {"findings": [f]}}],  # the same finding every time
            "arbiter": [
                lambda req, i: {
                    "output": {
                        "verdicts": [
                            {
                                "id": req.prompt.split("id ")[1].split(":")[0],
                                "stands": True,
                                "reason": "still no docstring",
                            }
                        ]
                    }
                }
            ],
        },
    )
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "waiting_human", out
    q = out.question
    assert q["kind"] == "finding" and {o["key"] for o in q["options"]} == {"fix", "waive", "stop"}
    assert len(env.coder.of("arbiter")) == 1
    # the owner waives it: no implementer call, checks and review again, then verified
    impl_calls = len(env.coder.of("implementer"))
    env.store.update_card("m1", "C1", detail={**env.card().detail, "question": q}, run_id=out.run_id)
    out = await env.runner.resume(env.card(), "waive", env.stage)
    assert out.status == "verified", out
    assert len(env.coder.of("implementer")) == impl_calls
    assert len(env.coder.of("arbiter")) == 1


async def test_same_failure_three_times_revises_then_asks(tmp_path):
    env = Env(tmp_path, {"implementer": [impl(WRONG + f"# {i}\n") for i in range(6)]}, max_rounds=10)
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "waiting_human"
    assert out.question["category"] == "repeated_failure" and out.question["kind"] == "engine"
    calls = env.coder.of("implementer")
    assert len(calls) == 6
    assert calls[3]["session_in"] is None  # round 4: a new session, the approach revised
    assert "Take a different approach" in calls[3]["prompt"] and "Acceptance criteria" in calls[3]["prompt"]
    assert calls[4]["session_in"] == calls[3]["session"]
    assert [r["next"] for r in env.rounds()] == ["fix", "fix", "revise", "fix", "fix", "ask"]


async def test_heldout_failure_returns_names_and_messages_only(tmp_path):
    env = Env(tmp_path, {"implementer": [impl(ABS), impl(RIGHT)]})
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "verified"
    second = env.coder.of("implementer")[1]["prompt"]
    assert "test_negative: AssertionError: add handles negative numbers" in second
    assert "add(-1, -1)" not in second and str(env.rt.heldout_root) not in second
    assert [r.get("heldout") for r in env.rounds()] == [False, True]


async def test_max_rounds_is_honoured(tmp_path):
    # a different failure each round: never the same signature, so only max_rounds stops it
    steps = [impl(f"def add(a, b):\n    return a + b + {i + 1}\n") for i in range(5)]
    for i, s in enumerate(steps):
        s["write"]["tests/test_base.py"] = f"def test_base_{i}():\n    assert False\n"
    env = Env(tmp_path, {"implementer": steps}, max_rounds=2, cards=None)
    env.store.update_card("m1", "C1", paths=[])  # anywhere, so the base test may change
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "waiting_human"
    assert len(env.coder.of("implementer")) == 2
    assert {o["key"] for o in out.question["options"]} == {"keep", "skip", "stop", "rewrite_tests"}
    env.store.update_card("m1", "C1", detail={**env.card().detail, "question": out.question}, run_id=out.run_id)
    out = await env.runner.resume(env.card(), "skip", env.stage)
    assert out.status == "failed" and "skipped" in out.reason
    assert len(env.coder.of("implementer")) == 2


async def test_guard_rejects_an_edited_acceptance_test(tmp_path):
    cheat = impl(RIGHT)
    cheat["write"]["tests/acceptance/C1/test_c1_add.py"] = "def test_add():\n    assert True\n"
    cheat["write"]["notes.md"] = "outside the card's paths\n"
    env = Env(tmp_path, {"implementer": [cheat, {"write": {}}]})
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "verified"
    wt = env.wt()
    assert (wt / "tests/acceptance/C1/test_c1_add.py").read_text().endswith("assert add(2, 3) == 5\n")
    assert not (wt / "notes.md").exists()
    second = env.coder.of("implementer")[1]["prompt"]
    assert "protected acceptance file" in second and "outside the card's paths" in second
    # no commit on any branch ever held the cheat (by file: commits made in one
    # second have no reliable order in `git log`, so a position split is flaky)
    acceptance = env.git.git("log", "-p", "--all", "--", "tests/acceptance/C1/test_c1_add.py", cwd=wt)
    assert "assert True" not in acceptance and "assert add(2, 3) == 5" in acceptance
    assert env.git.git("log", "--all", "--oneline", "--", "notes.md", cwd=wt).strip() == ""


async def test_guard_rejects_a_private_source(tmp_path):
    secret = "CONFIDENTIAL BRD: the client's private numbers\n"
    from hashlib import sha256

    copy = impl(RIGHT)
    copy["write"]["docs/brief.txt"] = secret
    env = Env(tmp_path, {"implementer": [copy, {"write": {}}]},
              private={sha256(secret.encode()).hexdigest(): "brd.docx"})  # fmt: skip
    env.store.update_card("m1", "C1", paths=[])
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "verified"
    assert not (env.wt() / "docs/brief.txt").exists()
    assert "CONFIDENTIAL" not in env.git.git("log", "-p", "--all", cwd=env.wt())
    assert "private source 'brd.docx'" in env.coder.of("implementer")[1]["prompt"]


async def test_rate_limit_requeues_and_the_next_run_resumes_the_session(tmp_path):
    env = Env(tmp_path, {"implementer": [impl(WRONG + "# 1\n"), {"status": "rate_limited", "resets_at": 4e9},
                                         impl(RIGHT)]})  # fmt: skip
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "requeue" and out.not_before == 4e9
    out = await env.runner.run(env.card(), env.stage)  # the scheduler starts it again later
    assert out.status == "verified"
    calls = env.coder.of("implementer")
    assert calls[2]["session_in"] == calls[0]["session"]
    assert len(env.coder.of("test_author")) == 1 and env.card().run_id.endswith(".2")


async def test_infra_error_fails_the_card(tmp_path):
    env = Env(tmp_path, {"implementer": [{"status": "infra", "error": "sandbox unavailable"}]})
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "failed" and "sandbox unavailable" in out.reason


async def test_an_empty_diff_retries_once_then_fails(tmp_path):
    env = Env(tmp_path, {"implementer": [{"write": {}}]})
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "failed" and "no change" in out.reason
    assert len(env.coder.of("implementer")) == 2
    assert "changed no file" in env.coder.of("implementer")[1]["prompt"]


async def test_a_failing_test_author_requeues_on_a_rate_limit(tmp_path):
    env = Env(tmp_path, {"test_author": [{"status": "rate_limited", "resets_at": 4e9}]})
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "requeue" and not env.coder.of("implementer")


async def test_a_parked_card_goes_on_after_the_engine_is_updated(tmp_path):
    # tipcalc m2/C3: parked on a question, the daemon restarted on a fixed engine (an op's code changed);
    # the resume was refused (JournalError: the graph changed), the card failed for good and the milestone replanned
    steps = [impl(f"def add(a, b):\n    return a + b + {i + 1}\n") for i in range(5)]
    for i, s in enumerate(steps):
        s["write"]["tests/test_base.py"] = f"def test_base_{i}():\n    assert False\n"
    env = Env(tmp_path, {"implementer": steps}, max_rounds=2, cards=None)
    env.store.update_card("m1", "C1", paths=[])
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "waiting_human"
    env.store.update_card("m1", "C1", detail={**env.card().detail, "question": out.question}, run_id=out.run_id)
    env.runner.engine._fingerprint = "an-updated-engine"  # what a changed op's code does to the graph's fingerprint
    out = await env.runner.resume(env.card(), "skip", env.stage)
    assert out.status == "failed" and "skipped" in out.reason  # the answer was applied, not the resume refused
    assert [e["kind"] for e in env.store.events("m1") if e["kind"] == "engine.updated"] == ["engine.updated"]


async def test_a_wrong_heldout_test_is_corrected_on_the_owners_word(tmp_path):
    # tipcalc m2/C6: the examiner compared two whole engine replies (their state objects compare by
    # identity), which no implementation can pass; rewriting the acceptance tests could not fix it
    from pathlib import Path

    from tests.flow_env import HELDOUT

    def examiner(text):
        return lambda req, i: {"write": {str(Path(req.add_dirs[0]) / "test_held.py"): text},
                               "output": {"files": ["test_held.py"]}}  # fmt: skip

    impossible = HELDOUT.replace("== -2, 'add handles negative numbers'", "is not -2, 'two runs are equal'")
    env = Env(tmp_path, {"implementer": [impl(RIGHT + f"# {i}\n") for i in range(3)],
                         "examiner": [examiner(impossible), examiner(HELDOUT)]}, max_rounds=2)  # fmt: skip
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "waiting_human"
    held = next(o for o in out.question["options"] if o["key"] == "rewrite_tests")
    assert held["label"] == "Rewrite its held-out tests (they are wrong)" and out.question["rewrite"] == "heldout"
    calls = len(env.coder.of("implementer"))
    env.store.update_card("m1", "C1", detail={**env.card().detail, "question": out.question}, run_id=out.run_id)
    out = await env.runner.resume(env.card(), {"choice": "rewrite_tests", "text": "is-not on an int"}, env.stage)
    assert out.status == "verified", out
    assert len(env.coder.of("implementer")) == calls  # the code was right: checked again, not rewritten
    again = env.coder.of("examiner")[1]["prompt"]
    assert "found your held-out tests" in again and "two runs are equal" in again and "is-not on an int" in again
    assert [e["kind"] for e in env.store.events("m1") if e["kind"] == "heldout.rewritten"] == ["heldout.rewritten"]
