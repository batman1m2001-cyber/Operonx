---
title: A card's round pays for the full suite once
status: plan
phases:
  - {id: V1, title: Review before the full checks, status: open}
  - {id: V2, title: Quick checks — the acceptance tests and the tests near the change, status: open}
  - {id: V3, title: The owner's check edits stick, status: open}
  - {id: V4, title: Sonnet and Haiku by default, status: open}
updated: 2026-10-31
---

# A card's round pays for the full suite once (2026-10-31)

**Asked** (owner). "The current project builder flow is too slow and taking more token than a normal claude code
session, may be because we have too many phases: QA, dev...?" — then "go, save that plan".

## What m33 measured

mr-finance flow merge (milestone m33, 4 cards, 15.7 h, $21.25 on cards):

| where | time | money |
|---|---|---|
| implement (Opus sessions) | 4.25 h | $18.86 (17 sessions) |
| QA (the card's checks) | 4.14 h | — |
| review, triage, test author, examiner, arbiter together | ~4 min | ~$3 |
| waiting on the owner | 0.19 h | — |

The extra roles are not the cost. The 16-minute suite is: QA runs every card check before the review, so every
review finding costs a fix and a whole new QA (F3: 11 rounds, 4 of them a 4-minute fix followed by a 16-minute QA).
The implementer ran the same suite inside its session too (F1 timed out on it, F4 polled it with `sleep`); #241
told it not to, but m33 ran on the daemon from before #241.

## Today (operonx_project/flows/task_flow/ops.py, `round_`)

implement → guard → **every card check** (+ acceptance) → review → held-out. A finding sends the card to implement,
and the next round runs every card check again before the reviewer sees the fix.

## V1 — Review before the full checks

A round becomes: implement → guard → quick checks (V2) → review → **full checks** → held-out.

- The full card checks run only when the quick checks pass and the review has no blocking finding.
- A full-check failure is a fix round as today; its feedback names the failing tests, and the next round's quick
  checks include them (V2), so the fix is seen in seconds and the full checks run once more at the end.
- The `checked` record the merge reuses (`same_code`) is written by the full run only.
- Decision: the reviewer sees the quick results, not the full ones ("Check results (they pass)" names what ran).

## V2 — Quick checks

`quick` = the acceptance tests + the tests near the change, run with the acceptance command:

- the test files the card changed;
- `tests/**/test_*<stem>*.py` for each changed source file's stem;
- the test ids that failed in the card's last full run.

Their failures are judged against the baseline's failures of the card's pytest check (a test already failing on the
tip is old). A project whose checks are not pytest gets the acceptance tests only.

## V3 — The owner's check edits stick

m33: "tests without browser" was dropped on 10-30 and was a card check again on 10-31 (its round-3 QA failed on a
flaky test in it). Find the path that put it back (the `ms:<mid>:baseline` row, `Lifecycle` caches a milestone's
runtime with the checks it read once), and make the store the one source: a card's run reads the milestone's checks
at its start.

## V4 — Sonnet and Haiku by default

Owner (2026-10-31): "consider to use sonnet and haiku as much as you can in the project builder flow, I think most
of the coding can be done with sonnet". The implementer was 89% of m33's money, all on Opus.

| role | was | now |
|---|---|---|
| implementer | opus | **sonnet**; `implementer:high` = opus (a high-risk card) |
| planner, replenisher, extractor | opus | **sonnet** |
| test_author, arbiter | sonnet | sonnet |
| triage, surveyor, retrospective | sonnet | **haiku** |
| reviewer, plan_reviewer, examiner, milestone_reviewer, hunter | fable | fable |

- `role_model` looks up `<role>:<risk>` before `<role>`, so `[models] "implementer:high" = "opus"` and the owner's
  own settings still win.
- The checking roles stay on a different, stronger model than the coder (plan decision 5: different models by
  construction); they cost ~$3 of m33's $21.

## Gate

- A scripted-coder test: a card with one review finding runs the full checks **once** (today twice); a card whose
  full checks fail once runs them twice, and the quick checks of the fix round name the failing test.
- The full operonx-project suite, once, at the end.
- No real-model rerun of m33 (15 h, $21): the tipcalc gate shape on Sonnet, one card, reports full-check runs.

Estimate: V1+V2 2 h, V3 30 min, V4 30 min.
