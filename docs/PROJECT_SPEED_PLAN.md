---
title: A card's round pays for the full suite once
status: done
phases:
  - {id: V1, title: Review before the full checks, status: done}
  - {id: V2, title: Quick checks — the acceptance tests and the tests near the change, status: done}
  - {id: V3, title: The owner's check edits stick, status: withdrawn}
  - {id: V4, title: Sonnet and Haiku by default, status: done}
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

## V3 — The owner's check edits stick (withdrawn)

m33: "tests without browser" was dropped on 10-30 19:51 and was a card check again on 10-31. **Not the engine.** The
`ms:m33:baseline` row was rewritten at 21:56:39 by the operator's own script (`m33_checks.py`, the "full suite once at
the end" change): it set `tests` to `when = "final"` and put `tests without browser` back as the per-card check, at
1800 s. `Lifecycle.rt()` already reads the checks from the store on every call. Nothing to change.

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

## Status (2026-10-31)

- V1+V2: `round_` runs quick checks → review → the card's checks → held-out. `_quick_paths` = the acceptance dir +
  `_near` (changed test files, `test_*<stem>*.py` from `git ls-files`, at most 40, and the last full run's failing
  ids). The round record carries `quick` (the paths) and `full_checks`. Gate: `test_a_finding_costs_the_quick_checks_
  not_the_whole_ones` (full checks run once, not twice) and `test_a_whole_check_failure_is_quick_checked_in_the_fix_
  round` (the fix round's quick checks name `tests/test_other.py::test_other`).
- V3: withdrawn, above.
- V4: `DEFAULT_MODELS`, `<role>:<risk>` lookup, settings.toml quotes the key; `tests/test_settings_models.py`.
