---
title: A lighter board and BRD folder
status: done
phases:
  - {id: B1, title: A card is a few lines, status: done}
  - {id: B2, title: The BRD scaffold keeps what is read, status: done}
  - {id: B3, title: The boarded projects rewritten, status: done}
  - {id: B4, title: OneWealth's board from its plans, status: done}
updated: 2026-10-30
---

# A lighter board and BRD folder (2026-10-30)

**Asked** (owner). "Some project does not have any items on board, that's weird, refactor and recreate the
milestone for me. The current structure data for project board and builder is quite complex and heavy, can you
simplify them, keep them enough for AI assistant to continue building."

## Today

- `docs/brd/board/m0.md` runs 10–13 KB for 10–14 cards. Each card is written three times over: a row in the cards
  table, a heading, and 16 bullets. Most of the bullets hold a default (`Kind: code`, `Risk: low`, `Priority: 0`,
  `Epic:`, `Stage:`, `Reopens: 0`, `Run:`, `Cost: 0.0000`). The milestone's own table has the same problem
  (`Mode`, `Budget`, `Replans`).
- `operonx init` writes 20 files into `docs/brd`. Eleven are stubs of about 100 bytes (`architecture/`,
  `features/`, `decisions/`, `ux/`, `testing/`, `assets/`, `templates/`, `GLOSSARY.md`) that no adopted project
  has filled. The checker reads each of these folders only if it exists. `MAINTAINING.md` repeats what the README
  says.
- OneWealth (`~/mr-finance`) has 32 `docs/*_PLAN.md` and no `docs/brd/board/`, so the studio shows its board
  as empty.

## Decisions

- **D1. A card is its heading and what the next session needs.** The heading is `### C3 · title · status`. The
  bullets are Intent, Rows, Deps, Files and Criteria, plus Outcome when the card ended. Kind, Risk and Epic appear
  only when they are not the default. The milestone keeps State, Rows, Reason and its Goal. What only a rebuild
  needs moves into the HTML comment at the end of the file, and only when it is not the default: policy,
  timestamps, and each card's run, cost, stage, reopens and priority. The cards table goes; `board/README.md`
  is the glance.
- **D2. The reader takes both formats.** The four boards already in git keep reading. Store → files → store stays
  the contract (`tests/test_board_files.py`).
- **D3. The scaffold keeps what the engine and the checker read:** README (with MAINTAINING folded in),
  CHANGELOG, brd.toml, `sources/`, `requirements/` (the matrix and open questions) and `board/`. A project adds
  `features/`, `decisions/`, `ux/`, `testing/` or `assets/` when it has something to put there. The checker
  already reads each one only if it is present. `adopted_requirements.md` stays: adopt writes it as the
  extraction the matrix cites.
- **D4. Boarded projects are rewritten through the engine:** the board files are read and written again, and a
  stub left exactly as the template wrote it is deleted. These are local commits, never pushed.
- **D5. OneWealth's board comes from its plans.** Each plan becomes a milestone and each of its phases a card. A
  card's status comes from what the plan records (done, open, skipped). A plan that names no phases becomes one
  card. The board is built in a scratch copy and only `docs/brd/board/` is copied into the repo; no operonx CLI
  runs inside `~/mr-finance`.

## Gate

- `tests/test_board_files.py` passes, and an old-format file reads back equal to its store.
- `tests/internal/cli/test_init.py` and `brd check` pass on a fresh `operonx init`.
- The four boards are under 50% of their size. The studio's Project tab shows OneWealth's milestones with cards.

## Result (2026-10-30)

- The tests pass: `test_board_files.py` (an old-format board reads back equal and rewrites 20% smaller), `test_init`,
  the guide tests and the operonx-project suite.
- **The boards shrank 25–28%, not 50%.** qc-snatcher 13,450 → 9,940 B, meeting-prep-operonx 10,399 → 7,726,
  meeting-prep-brd 11,882 → 8,839, the callbot 11,538 → 8,426. What is left is each card's intent, rows,
  files and criteria: what the next session needs. The defaults and the repeated table are gone. The gate's
  50% was a guess made before measuring.
- The scaffold went from 20 files to 9. Each project lost the stubs it had left as the template wrote them, and
  `brd check` reported the same errors before and after (0 → 0).
- OneWealth: 32 plans became 32 milestones with 180 cards (28 done, 3 paused with open cards: NEWS_IMPACT N5,
  BUSINESS_FEEDBACK_1021 B4 and CLIENT_DATASET D0, D4–D8; ASSISTANT_ATTACH cancelled). `brd check` shows no board
  error. The studio reads the 32 milestones.
